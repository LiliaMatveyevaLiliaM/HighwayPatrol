#
# System configuration parameters
#


# External libraries import statements
import os


# This application's import statements
try:
    # These are for when running in an EC2
    from superGlblVars import config
    from systemMode import SystemMode

except ModuleNotFoundError as err:
    # These are used during Lambda and CDK operations
    from src.python.superGlblVars import config
    from src.python.systemMode import SystemMode

# Determine whether we're running on lambda or not
onLambda = "AWS_LAMBDA_FUNCTION_NAME" in os.environ

# System can run in either "DEV", "TEST" or "PROD" mode
# Only in "test" mode, system won't reach out on the net, but will
# instead use the files in the testResources directories.
# Only in "prod" mode, will the system go through all paginations and
# iterations of lists, loops, etc.
config["mode"] = SystemMode.TEST

# Where to put all of our working files
config["workDirectory"] = "/tmp/highwaypatrol"
config["logsDirectory"] = os.path.join(config["workDirectory"], "logs")

# Names of environment variables needed; to check if the vars are set
# Note that the value here is the env var's name, NOT the value of the variable
# This is done as a helper to the CDK script since we don't know the element's name until CDK runs
# unless, of course, we hard-code it in the stack script, and in most situations, we don't want that
config["stsQueueVarName"] = "HPatrolStatusQueue"
config["bagQueueVarName"] = "HPatrolBaggingQueue"
config["disQueueVarName"] = "HPatrolDispatchQueue"
config["tcdQueueVarName"] = "HPatrolTranscodeQueue"

# System queues; can be overriden by environment variables
# The overriding happens in processInit.py
config["bagQueue"] = "highwayPatrol_hPatrolBagging"
config["disQueue"] = "highwayPatrol_hPatrolDispatch"
config["tcdQueue"] = "highwayPatrol_hPatrolTranscode"
config["statusQueue"] = "highwayPatrol_hPatrolStatus"


# Location of FFMPEG executables
if onLambda:
    config["ffmpeg"] = "/opt/bin/ffmpeg"
    config["ffprobe"] = "/opt/bin/ffprobe"
else:
    config["ffmpeg"] = "/bin/ffmpeg"
    config["ffprobe"] = "/bin/ffprobe"


# Note that there are 2 buckets to be specified
    # config["defaultWrkBucket"] indicates the working bucket
    # config["defaultDstBucket"] indicates destination for deliveries

# Amazon Web Services config
if config["mode"] == SystemMode.PROD:
    config["awsProfile"] = "thorium-ch-prod"
    config["defaultWrkBucket"] = "thorium-ch-prod"
    config["defaultDstBucket"] = "thorium-ch-prod"
else:
    config["awsProfile"] = "mendeleev-ch-test"
    config["defaultWrkBucket"] = "whPatrol-test"
    config["defaultDstBucket"] = "whPatrol-test"

# Here order matters; this MUST go after the config["awsProfile"] specifications above
if onLambda:
    config["awsProfile"] = False


# For ECS and VPN
config["ecsClusterName"] = "hPatrol-vpn-cluster"
config["protonVpnSecretsName"] = "hPatrol-protonSecrets"
config["asgCapacityProviderName"] = "asg-capacity-provider"
config["protonVpnProxyContainerName"] = "protonvpn-container"


# Default session headers; user-agent strings are added in processInit for randomization
config["sessionHeaders"] = {
    "Connection": "keep-alive",
    "Cache-Control": "max-age=0",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
}

# How often is the system expected to wake up (in minutes)
# This number should match the cron wake-up for the system Scheduler
# Later in the code, we add a buffer of overlap so as to not lose data feeds
config["systemPeriodicity"] = 10

# Proxy to use during requests' library connections
# If no proxy is to be used, use value False
config["proxy"] = "mendeleev.whirl.dom:14400"
if onLambda:
    config["proxy"] = False

# Site to check our IP
config["chkIpURL"] = "https://0yjmfrxhl0.execute-api.us-east-1.amazonaws.com"
