#!/bin/sh

# Script to handle dockerfile creation and deployment
# Defaults to deploying to DEV
#
# Usage:
# prepPlaywrightContainer.sh --help (-h)
# Print something helpful
#
# prepPlaywrightContainer.sh -credentials (-c) <--prod>
# Go out to Amazon to get credentials to deploy the local docker container into AWS' ECR.
# Requires local AWS creds, i.e. inconceivable has been run first.
#
# prepPlaywrightContainer.sh --build (-b)
# Copy in HighwayPatrol and build new docker container
#
# prepPlaywrightContainer.sh --prune (-p)
# Remove all local docker resources, including HP files
#
# prepPlaywrightContainer.sh --deploy (-d) -r [repoName] <--prod> 
# Send the local container to AWS - requires docker creds (-c)
#
# prepPlaywrightContainer.sh --all (-a) -r [repoName] <--prod> 
# Get credentials
# Prune local resources
# build docker container
# push container to AWS ECR
# prune local resources again

f_usage() {
    printf '%s\n' \
        "This script handles docker creation and deployment.  Defaults to deploying to DEV" \
        "Usage: prepPlaywrightContainer.sh (--all|--credentials|--build|--prune|--deploy) -r [repoName] <--prod>" \
        "" \
        "Examples:" \
        "prepPlaywrightContainer.sh -credentials (-c) <--prod>" \
        "  Go out to Amazon to get credentials to deploy the local docker container into AWS' ECR." \
        "  Requires local AWS creds, i.e. inconceivable has been run first." \
        "" \
        "prepPlaywrightContainer.sh --build (-b)" \
        "  Copy in HighwayPatrol and build new docker container" \
        "" \
        "prepPlaywrightContainer.sh --prune (-p)" \
        "  Remove all local docker resources, including HP files" \
        "" \
        "prepPlaywrightContainer.sh --deploy (-d) -r [repoName] <--prod>" \
        "  Send the local container to AWS - requires docker creds (-c)" \
        "" \
        "prepPlaywrightContainer.sh --all (-a) -r [repoName] <--prod>" \
        "  Get credentials" \
        "  Prune local resources" \
        "  build docker container" \
        "  push container to AWS ECR" \
        "  prune local resources again" \
        ""

    exit 1
}

devAccount="358908341133"
prodAccount="515974619758"
repoName=./getBaseStackName.sh
containerName="playwrightcollector"
lambdaFunctionName="playwrightcollector"

doDeploy=false
doCreds=false
doPrune=false
doBuild=false
doProd=false
doAll=false

# =============Functions start here=============

# Reaches out to AWS to get login creds for docker to push images to the ECR
# prereq is that the user has local creds, i.e. has run inconceivable
f_doCreds() {
    echo " ======== Obtaining AWS credentials for Docker ======== "
    if [ "$doProd" = false ]; then
        CMD1="aws --profile mendeleev-ch-test ecr get-login-password --region us-east-1"
        CMD2="docker login --username AWS --password-stdin ${devAccount}.dkr.ecr.us-east-1.amazonaws.com"
    else
        CMD1="aws --profile thorium-ch-prod ecr get-login-password --region us-east-1"
        CMD2="docker login --username AWS --password-stdin ${prodAccount}.dkr.ecr.us-east-1.amazonaws.com"
    fi
    echo "${CMD1}|${CMD2}"
    echo " ======== Executing ======== "
    ${CMD1}|${CMD2}
}

# This removes all docker artifacts as well as the local copy of HP
f_doPrune() {
    echo " ======== Removing local docker assets ======== "
    docker image ls -q | xargs -I {} docker image rm {} -f     # Searches for and removes all images
    docker volume ls -q | xargs -I {} docker image rm {} -f    # Searches for and removes all volume stores
    yes | docker system prune                                  # Removes all unattached docker resources and caches
    echo " ======== Removing local HP assets ======== "
    rm *.py
    rm -r addons
    rm -r orangeUtils
    rm -r utils
}

# This copies HP into the current directory so docker can build it into the container
f_doCopy() {
    echo " ======== Copying HP assets into current directory ======== "
    cp ../../*.py .
    mkdir addons
    cp ../../addons/* addons/
    mkdir orangeUtils
    cp ../../orangeUtils/* orangeUtils/
    mkdir utils
    cp ../../utils/* utils/
    chmod 755 -R *
}

# This builds the docker container locally
f_doBuild() {
    f_doCopy
    echo " ======== Building docker container ======== "
    echo "docker build -t ${containerName} ."
    echo " ======== Executing ======== "
    docker build -t ${containerName} .
}

# This takes the local docker container, tags it, and sends it up AWS ECR
f_doDeploy() {
    echo " ======== Deploying container to AWS ECR ======== "

    if [ "$doProd" = false ]; then
        CMD1="docker tag ${containerName}:latest ${devAccount}.dkr.ecr.us-east-1.amazonaws.com/${repoName}:latest"
        CMD2="docker push ${devAccount}.dkr.ecr.us-east-1.amazonaws.com/${repoName}:latest"
        CMD3="aws --profile mendeleev-ch-test lambda update-function-code --function-name ${lambdaFunctionName} --image-uri=${devAccount}.dkr.ecr.us-east-1.amazonaws.com/${repoName}:latest --publish"
    else
        CMD1="docker tag ${containerName}:latest ${prodAccount}.dkr.ecr.us-east-1.amazonaws.com/${repoName}:latest"
        CMD2="docker push ${prodAccount}.dkr.ecr.us-east-1.amazonaws.com/${repoName}:latest"
        CMD3="aws --profile thorium-ch-prodt lambda update-function-code --function-name ${lambdaFunctionName} --image-uri=${prodAccount}.dkr.ecr.us-east-1.amazonaws.com/${repoName}:latest --publish"
    fi

    echo "${CMD1}"
    echo "${CMD2}"
    echo " ======== Executing ======== "
    ${CMD1}
    ${CMD2}
}

# =============Main starts here=============

opts=":abcdhpr-:"
while getopts "$opts" optChar; do
    case "${optChar}" in
        -)
            case "${OPTARG}" in
                all)
                    doDeploy=true
                    doCreds=true
                    doPrune=true
                    doBuild=true
                    doAll=true
                ;;
                build)
                    doBuild=true
                ;;
                credentials)
                    doCreds=true
                ;;
                deploy)
                    doDeploy=true
                ;;
                help)
                    f_usage
                ;;
                prune)
                    doPrune=true
                ;;
                prod)
                    doProd=true
                ;;
                *)
                    if [ "$OPTERR" = 1 ] && [ "${optspec:0:1}" != ":" ]; then
                        echo "Unknown option --${OPTARG}" >&2
                        f_usage
                    fi
                ;;
            esac
        ;;
        a)
            doDeploy=true
            doCreds=true
            doPrune=true
            doBuild=true
            doAll=true
        ;;
        b)
            doBuild=true
        ;;
        c)
            doCreds=true
        ;;
        d)
            doDeploy=true
        ;;
        h)
            f_usage
        ;;
        p)
            doPrune=true
        ;;
        r)
            val="${!OPTIND}"
            repoName=${val}
        ;;
        *)
            if [ "$OPTERR" != 1 ] || [ "${opts:0:1}" = ":" ]; then
                echo "Non-option argument: '-${OPTARG}'" >&2
                f_usage
            fi
        ;;
    esac
done

if [ "$doCreds" = true ]; then
    f_doCreds
fi

if [ "$doPrune" = true ]; then
    f_doPrune
fi

if [ "$doBuild" = true ]; then
    f_doBuild
fi

if [ "$doDeploy" = true ]; then
    f_doDeploy
fi

# If we're doing everything, clean up again at the end
if [ "$doAll" = true ]; then
    f_doPrune
fi
