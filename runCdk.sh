#!/bin/bash

set -eu -o pipefail

MY_NAME=${0}
ACTION=list
AWS_ACCOUNT=none
STACK_NAME=processing
BASE_STACK_NAME=`./getBaseStackName.sh`
STACKS=$(jq -r '.context.stackNames[]' cdk.json)
readarray -t COLREGIONS < <(jq -r '.context.collectionRegion[]' cdk.json)

f_usage() {
    printf '%s\n' \
        "This script is a wrapper to perform CDK operations" \
        "Note that stack name will be prepended with '${BASE_STACK_NAME}-'" \
        "This baseStackName can be modified in stacks/common/src/python/superGlblVars.py" \
        "" \
        "Usage: ${MY_NAME/\.\//} -p <AWS account> -s <what stack> -a <synth|list|deploy|destroy>" \
        "       -p profile" \
        "       -s stack; can use \"all\"" \
        "       -a action" \
        ""
    exit 1
}


f_userConfirmation() {
    echo "Will ${ACTION} ${STACK_NAME} in ${AWS_ACCOUNT}..."
    echo ""
}


f_allStacksCmd() {
    # Loop for other-than-collection stacks
    for stack in $STACKS
    do
        f_cdkCmd ${ACTION} --context baseStackName=$BASE_STACK_NAME --context selectedStack="$BASE_STACK_NAME""$stack" --asset-parallelism true --concurrency 50 "$BASE_STACK_NAME""$stack"
        # f_cdkCmd ${ACTION} --profile $AWS_ACCOUNT --context profile=$AWS_ACCOUNT --context baseStackName=$BASE_STACK_NAME --context selectedStack="$BASE_STACK_NAME""$stack" --asset-parallelism true --concurrency 50 "$BASE_STACK_NAME""$stack"
    done

    # Loop for collection stack in all regions
    f_allCollectionStacksCmd
}


f_allCollectionStacksCmd() {
    # Deploy collection stack in all regions in $COLREGIONS
    for regionName in "${COLREGIONS[@]}"
    do
        f_singleCollectionStackCmd "$regionName"
    done
}


f_singleCollectionStackCmd() {
    # regionCode=$(python3 -c "import sys, stacks.common.src.python.orangeUtils.utils as ut; print(ut.getRegionCode(sys.argv[1]))" "$1")
    # if [ -z "$regionCode" ]; then
    #     echo "ERROR: Region '${1}' not found; exiting"
    #     exit 1
    # fi
    f_cdkCmd ${ACTION} --context collectionRegion="us-east-1" --context baseStackName="$BASE_STACK_NAME" --context selectedStack="$BASE_STACK_NAME""-collection" --exclusively --asset-parallelism true --concurrency 50 "$BASE_STACK_NAME""-collection-us-east-1"
    # f_cdkCmd ${ACTION} --profile $AWS_ACCOUNT --context profile=$AWS_ACCOUNT --context collectionRegion="$regionCode" --context baseStackName="$BASE_STACK_NAME" --context selectedStack="$BASE_STACK_NAME""-collection" --exclusively --asset-parallelism true --concurrency 50 "$BASE_STACK_NAME""-collection-""$regionCode"
}


f_cdkCmd() {
    # CDK_PYTHON_VERSION=$(python -c 'from importlib import metadata; print(metadata.version("aws-cdk-lib"))')

    # if ! [[ $CDK_PYTHON_VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+ ]];
    # then
    #     echo "ERROR: Cannot determine CDK_PYTHON_VERSION! Found CDK_PYTHON_VERSION=${CDK_PYTHON_VERSION} Exiting!"
    #     exit 1
    # fi

    # "npx" runs a command from a local or remote npm package
    # --yes is to suppress the prompt asking to install packages if necessary
    # CDK_CMD="npx --yes -p aws-cdk@${CDK_PYTHON_VERSION} cdk ${*}"
    CDK_CMD="cdk ${*}"

    unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE
    unset CDK_DEPLOY_ACCOUNT CDK_DEPLOY_REGION CDK_DEFAULT_ACCOUNT CDK_DEFAULT_REGION

    exec &> >(tee ${MY_NAME%.*}_${AWS_ACCOUNT}_${ACTION}.log) # copy stdout and stderr to log file
    echo ${CDK_CMD}
    ${CDK_CMD}

    unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE
    unset CDK_DEPLOY_ACCOUNT CDK_DEPLOY_REGION CDK_DEFAULT_ACCOUNT CDK_DEFAULT_REGION
}


# =============Main starts here=============
[[ $# -gt 1 ]] || f_usage

declare -i S_COUNT=0
declare -i P_COUNT=0
declare -i A_COUNT=0


# Notice here we prepend the baseStackName (for default options)
STACK_NAME=${BASE_STACK_NAME}-${STACK_NAME}

while getopts 's:p:a:' OPTION
do
    case ${OPTION} in
        s)
            S_COUNT=$(( S_COUNT + 1))
            # Notice here we prepend the baseStackName
            STACK_NAME=${BASE_STACK_NAME}-${OPTARG}
        ;;
        p)
            P_COUNT=$(( P_COUNT + 1))
            AWS_ACCOUNT=${OPTARG}
        ;;
        a)
            A_COUNT=$(( A_COUNT + 1))
            ACTION=${OPTARG}
        ;;
        h|?|:)
            f_usage
        ;;
    esac
done

# Enforce required options; max 1 of each
[[ ${S_COUNT} -eq 1 ]] || f_usage
[[ ${P_COUNT} -eq 1 ]] || f_usage
[[ ${A_COUNT} -eq 1 ]] || f_usage

[[ ${ACTION} =~ ^(synth|list|deploy|destroy)$ ]] || f_usage


# Activate Conda environment from script
eval "$(conda shell.bash hook)"
# Note that the environment name used is from environment.yml
# conda activate hpatrol

case ${ACTION} in
    deploy)
        f_userConfirmation
        if [[ ${STACK_NAME} =~ (-all)$ ]]; then
            f_allStacksCmd
        elif [[ ${STACK_NAME} =~ (-collection)$ ]]; then
            f_allCollectionStacksCmd
        elif [[ ${STACK_NAME} =~ "-collection" ]]; then
            # Deploy collection stack to one region
            # We need to tell CDK which region we are deploying to, parse it from STACK_NAME
            region="${STACK_NAME#${BASE_STACK_NAME}-collection-}"
            f_singleCollectionStackCmd "$region"
        else
            f_cdkCmd ${ACTION} --context baseStackName=$BASE_STACK_NAME --context selectedStack=$STACK_NAME --exclusively --asset-parallelism true --concurrency 50 ${STACK_NAME}
            # f_cdkCmd ${ACTION} --profile $AWS_ACCOUNT --context profile=$AWS_ACCOUNT --context baseStackName=$BASE_STACK_NAME --context selectedStack=$STACK_NAME --exclusively --asset-parallelism true --concurrency 50 ${STACK_NAME}
        fi
    ;;
    synth)
        if [[ ${STACK_NAME} =~ (-all)$ ]]; then
            f_allStacksCmd
        elif [[ ${STACK_NAME} =~ (-collection)$ ]]; then
            f_allCollectionStacksCmd
        elif [[ ${STACK_NAME} =~ "-collection" ]]; then
            region=${STACK_NAME#${BASE_STACK_NAME}-collection-}
            f_singleCollectionStackCmd $region
        else
            f_cdkCmd ${ACTION} --context baseStackName=$BASE_STACK_NAME --context selectedStack=$STACK_NAME --exclusively --asset-parallelism true --concurrency 50 ${STACK_NAME}
            # f_cdkCmd ${ACTION} --profile $AWS_ACCOUNT --context profile=$AWS_ACCOUNT --context baseStackName=$BASE_STACK_NAME --context selectedStack=$STACK_NAME --exclusively --asset-parallelism true --concurrency 50 ${STACK_NAME}
        fi
    ;;
    list)
        f_cdkCmd ${ACTION} --context baseStackName=$BASE_STACK_NAME --context selectedStack=$STACK_NAME
        # f_cdkCmd ${ACTION} --profile $AWS_ACCOUNT --context profile=$AWS_ACCOUNT --context baseStackName=$BASE_STACK_NAME --context selectedStack=$STACK_NAME
    ;;
    destroy)
        if [[ ${STACK_NAME} =~ (-init)$ ]]; then
            echo ""
            echo "WARNING: Destroy is not allowed for '${STACK_NAME}'; must call CloudFormation directly"
            echo ""
            exit 0
        fi
        f_userConfirmation
        if [[ ${STACK_NAME} =~ (-all)$ ]]; then
            f_allStacksCmd
        elif [[ ${STACK_NAME} =~ (-collection)$ ]]; then
            f_allCollectionStacksCmd
        elif [[ ${STACK_NAME} =~ "-collection" ]]; then
            region=${STACK_NAME#${BASE_STACK_NAME}-collection-}
            f_singleCollectionStackCmd $region
        else
            f_cdkCmd ${ACTION} --context baseStackName=$BASE_STACK_NAME --context selectedStack=$STACK_NAME --exclusively ${STACK_NAME}
            # f_cdkCmd ${ACTION} --profile $AWS_ACCOUNT --context profile=$AWS_ACCOUNT --context baseStackName=$BASE_STACK_NAME --context selectedStack=$STACK_NAME --exclusively ${STACK_NAME}
        fi
    ;;
    *)
        f_usage
    ;;
esac

echo "Elapsed seconds: ${SECONDS}"
