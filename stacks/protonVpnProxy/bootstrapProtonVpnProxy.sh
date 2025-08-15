#!/bin/bash

# This starts all background processes for the ProtonVPN MITMproxy container
# and acts as Docker PID 1 to keep the Docker container alive.

# Since the script functions as PID 1, it must periodically check to verify
# background processes are alive and to handle SIGTERM when the ECS job aborts
# or shuts down normally. 

# Note that ProtonVPN is running in a "kill switch" mode which will block any
# traffic if the ProtonVPN process exits abnormally.

# This is what the script does on execution:
    # Configure ProtonVPN client configuration files
    # Get a list of target country ProtonVPN servers
    # Start ProtonVPN client connecting to the target country server using OpenVPN credentials
    # Start mitmproxy
    # Loop forever until SIGTERM, or mitmproxy exits
    #     Upon exit, write mitmproxy output to Cloudwatch logs

echo "Entered Proton Image Script"
echo "Today is: `date`"

# Define Vars
TIER=2  # The purchased account tier level on ProtonVPN
MY_NAME=${0/\.\//}  # Script name w/out dot slash
TEMP_DIR=$(mktemp)

# Gotta hate environment variables
echo "Needed envVars current values:"
echo "    AWS_REGION: ${AWS_REGION}"
echo "    ENV_FOR_DYNACONF: ${ENV_FOR_DYNACONF}"
echo "    MITMPROXY_USER: ${MITMPROXY_USER}"
echo "    MITMPROXY_BINARY: ${MITMPROXY_BINARY}"
echo "    PROXY_LISTEN_PORT: ${PROXY_LISTEN_PORT}"
echo " "


trap "{ echo 'Caught SIGTERM, exiting!'; f_exit; }" SIGTERM

f_exit() {
    echo " "
    local EXIT_CODE=${1:-0}
    echo "Killing ${MITMPROXY_BINARY}..."
    pkill ${MITMPROXY_BINARY}
    f_protonVpnDisconnect
    # Give buffered log entries an opportunity to be delivered
    sleep 15
    exit ${EXIT_CODE}
}

f_logError() {
    echo "*** ERROR:  $1"
    python3 -u src/logError.py \
        -reporter ${MY_NAME} \
        -country ${COUNTRY} \
        -errorMsg "$1"
}

f_logErrorAndExit() {
    f_logError "$1"
    f_exit 1
}

f_protonVpnConnectServers_justShow() {
    local COUNTRY=$1
    OVPN_FILE="${COUNTRY,,}.protonvpn.udp.ovpn" #lowercase
    PROTONVPN_COMMAND="openvpn --config src/${OVPN_FILE} --auth-user-pass .protonSecrets &"
    echo "Just for show...would have run command"
    echo ${PROTONVPN_COMMAND}
}

f_protonVpnConnectServers() {
    local COUNTRY=$1
    OVPN_FILE="${COUNTRY,,}.protonvpn.udp.ovpn" #lowercase
    # Backgrounding the process because openvpn blocks execution for printing logs
    PROTONVPN_COMMAND="openvpn --config src/${OVPN_FILE} --auth-user-pass .protonSecrets &"
    # Note that we may get the following notification:
    #   WARNING: this configuration may cache passwords in memory -- use the auth-nocache option to prevent this
    # We DO want it to cache the pass, because as stated in the docs:
    #   If specified, this directive will cause OpenVPN to immediately forget username/password inputs
    #   after they are used. As a result, when OpenVPN needs a username/password, it will prompt for
    #   input from stdin, which may be multiple times during the duration of an OpenVPN session
    # This hasn't been tested on our end though; caching this on ECS is considered low-risk
    echo ${PROTONVPN_COMMAND}
    eval ${PROTONVPN_COMMAND}
    if [ $? -ne 0 ]; then
        echo "Error trying to execute; Exiting..."
        exit
    fi

    # Make sure it's not a US connection
    CONN_COUNTRY=$(timeout 120 bash -c 'until [ ! -z "$EVAL_COUNTRY_CODE" -a "$EVAL_COUNTRY_CODE" != "US" ]; do sleep 30; \export EVAL_COUNTRY_CODE=$(curl -s https://ipinfo.io/json | jq -r ".country"); done; \echo $EVAL_COUNTRY_CODE')
    [[ $? -ne 0 ]] &&  f_logErrorAndExit "Unable to connect! Exiting!"

    echo "Connected to protonvpn country: $CONN_COUNTRY"
    if [[ ${CONN_COUNTRY} == "US" ]]; then
        f_logErrorAndExit "Only got U.S. Exiting!"
    fi

    if [[ ${CONN_COUNTRY} != ${COUNTRY} ]]; then
        echo "Warning! Requested country ($COUNTRY) differs from connected: $CONN_COUNTRY"
    fi

    if [[ ${CONN_COUNTRY} == "null" ]]; then
        # Had this situation once when a whole-country access point went down
        # The script keeps going but connects w/out VPN
        # Rather have this than stay on an infinite loop retrying the country
        # TODO: Stop network comms without erroring out the script
        #   Should probably just disable network comms but let the script continue
        #   because by erroring out, the script will re-try again and again.
        echo "******************************"
        echo "* Warning! Warning! Warning! *"
        echo "*       VPN IS NOT UP        *"
        echo "* Warning! Warning! Warning! *"
        echo "******************************"
    fi
}

f_testInetConnection() {
    echo "vvvvvvvvvvv TESTING INET CONNECTION vvvvvvvvvvv"
    # TOTEST="https://0yjmfrxhl0.execute-api.us-east-1.amazonaws.com/"
    # TOTEST="www.google.com"
    TOTEST="https://ipinfo.io/json"
    echo "\$ curl $TOTEST"
    curl $TOTEST
    echo -e "\n^^^^^^^^^^^ TESTED INET CONNECTION ^^^^^^^^^^^^\n"
}

f_protonVpnDisconnect(){
    echo "Disconnecting..."
    pkill openvpn
    PROTONVPN_EXIT_CODE=$?
    [[ ${PROTONVPN_EXIT_CODE} -ne 0 ]] &&  f_logError "Non-zero return value (${PROTONVPN_EXIT_CODE}) disconnecting openvpn"
}

f_startReadProxy() {
    # Run mitmproxy in background as coprocess; save coproc file descriptors for use later
    # From https://unix.stackexchange.com/questions/497614/bash-execute-background-process-whilst-reading-output
    #      https://stackoverflow.com/questions/10867153/background-process-redirect-to-coproc
    echo "Starting ${MITMPROXY_BINARY}..."

    # By default mitmproxy uses port 8080
    coproc (trap '' PIPE; gosu ${MITMPROXY_USER} ${MITMPROXY_BINARY} --listen-port ${PROXY_LISTEN_PORT} < /dev/null & disown)
    exec {COPROC_SAVE[0]}<&${COPROC[0]}- {COPROC_SAVE[1]}>&${COPROC[1]}-

    # Loop forever on proxy stdout echoing for Cloudwatch logs
    # read is interrupted by SIGTERM so read timeout is for checking other exit conditions
    READ_TIMEOUT_SEC=60
    echo "Looping forever on ${MITMPROXY_BINARY} stdout; read timeout every ${READ_TIMEOUT_SEC}s..."
    while ${FOREVER:-true}; do
        # echo "====================`date`"
        # The way read -t actually works is READ_TIMEOUT_SEC seconds is given to read the next input
        # Thus if lines are continually coming in, the timeout will reset
        # In order to break this loop, READ_TIMEOUT_SEC secs need to pass without stdout lines to read
        while read -t ${READ_TIMEOUT_SEC} -r line ; do
            if [[ "${line}" != '' ]]; then
                # Just echo the action to see what's going on
                echo "${MITMPROXY_BINARY} ${line}"
            fi
        done <&${COPROC_SAVE[0]}

        # Exit if proxy process has exited
        if ! pgrep ${MITMPROXY_BINARY} >/dev/null; then
            echo "Error trying to execute proxy binary ${MITMPROXY_BINARY}! Exiting..."
            FOREVER=false
        fi
    done
}

while [ "$#" -gt 0 ]; do
    case $1 in
        -secret) AWS_SECRET_NAME="$2"; shift ;;
        -country) COUNTRY="$2"; shift ;;
        -go) GONOGO="$2"; shift ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

COUNTRY=${COUNTRY?-country parameter required!}
AWS_SECRET_NAME=${AWS_SECRET_NAME?-secret parameter required!}

CREDS=`aws secretsmanager get-secret-value --region ${AWS_REGION} --secret-id ${AWS_SECRET_NAME} --query SecretString --output text`
if [ $? -ne 0 ]; then
    exit
fi

USERNAME=`echo ${CREDS} | jq -r ".Username"`
PASSWORD=`echo ${CREDS} | jq -r ".Password"`

echo "Running Job (ProtonVPN - OpenVPN Client);  \
country=${COUNTRY}; \
username=${USERNAME}"

# The previous protonvpn CLI version added a "+plc" to the uname; don't know what it is
# The postfixes after the uname indicate something because of this below
# This was pulled from the ovpn files downloaded from Proton after logging in
##      If you are a paying user you can also enable the ProtonVPN ad blocker (NetShield) or Moderate NAT:
##      Use: "YourUserNameHere+f1" as username to enable anti-malware filtering
##      Use: "YourUserNameHere+f2" as username to additionally enable ad-blocking filtering
##      Use: "YourUserNameHere+nr" as username to enable Moderate NAT
##      Note that you can combine the "+nr" suffix with other suffixes.

echo "Configuring protonvpn credentials..."
cat > ./.protonSecrets <<EOF
${USERNAME}
${PASSWORD}
EOF

chmod 600 .protonSecrets


# Simple test so logs show network access
f_testInetConnection

echo "-----------ip r BEFORE vpn-----------"
ip r

# Just a simple gate so we can test w/out explicitly starting proton
if [[ ${GONOGO} != "yes" ]]; then
    f_protonVpnConnectServers_justShow ${COUNTRY}
    echo "NoGo testing; exiting..."
    exit 0
fi
echo " "
echo "Continuing forward w/Proton connection..."

# ProtonVPN will not connect in mendeleev-dev; don't even bother trying
if [[ ${ENV_FOR_DYNACONF} != "DEV" ]]; then
    f_protonVpnConnectServers ${COUNTRY}
else
    echo "WARNING: Environment is dev; will not start protonvpn..."
fi

echo "-----------ip r AFTER vpn-----------"
ip r
f_testInetConnection

f_startReadProxy

echo "Done!"

f_exit
