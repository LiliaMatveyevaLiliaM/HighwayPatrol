# Python libraries import statements
import os
import sys
import shutil
import logging
import pathlib
import subprocess
from random import randint
from dynaconf import Dynaconf


# AWS import statements
import boto3
import botocore.exceptions as bexcept
from aws_cdk.aws_kinesis import Stream
from aws_cdk import Environment, Stack
from aws_cdk.aws_lambda import Code, Function, LayerVersion
from aws_cdk.aws_logs_destinations import KinesisDestination
from aws_cdk.aws_logs import FilterPattern, SubscriptionFilter
from aws_cdk.aws_iam import ManagedPolicy, Role, ServicePrincipal


# Small trick so importing the system settings works
# This keeps us from having to re-specify certain values in different places
# e.g.: without this, we'd have to specify the bucketName in two files
commonPath = str(pathlib.Path.cwd()) + "/stacks/common"
if commonPath not in sys.path:
    sys.path.insert(0, commonPath)
# print("\n\nsys.path: {}\n\n".format(sys.path))

# This application's import statements
from src.python.superGlblVars import projectName


logger = logging.getLogger()
logging.basicConfig(format="%(asctime)s - %(levelname)-8s - %(module)s:%(lineno)d - %(message)s")


def initCdkSettings() -> dict:
    cwd = pathlib.Path.cwd()
    secretName = ".secrets.yaml"
    secretsFile = cwd / f"projectResources/{secretName}"
    settingsFile = cwd / "stacks/systemResources/deploymentSettings.yaml"

    # Just checking...
    try:
        youThere = secretsFile.resolve(strict=True)
        youThere = settingsFile.resolve(strict=True)
    except FileNotFoundError as err:
        # Can't use logger() here because it hasn't been initialized; need print()
        print("\nERROR!")
        print(f"\tFile '{err.filename}' not found")
        missing = os.path.basename(err.filename)
        if missing == secretName:
            print("\tIf deploying VPN stack make sure the secrets file has correct info")
            print("\tIf not deploying VPN stack just 'touch' the file to ignore this check")
        print("\n")
        raise FileNotFoundError(missing) from None

    settingsFiles = [
        settingsFile,
        secretsFile
    ]

    return Dynaconf(
        settings_files=settingsFiles,
        environments=True,
        filter_strategy=None,
        ignore_unknown_envvars=True,
    )
 

        # permissions_boundary=ManagedPolicy.from_managed_policy_name(
        #     stackObjRef, 
        #     "lambdaBoundary", 
        #     cdkConfig["PERMISSIONS_BOUNDARY"]
        #     ),
def createLambdaRoles(stackObjRef, cdkConfig) -> None:
    # Create the Lambda Roles
    lambdaRole = Role(stackObjRef,
        f"{projectName}LambdaRole",
        assumed_by=ServicePrincipal("lambda.amazonaws.com"),
        permissions_boundary=None,
        managed_policies=[
            ManagedPolicy.from_aws_managed_policy_name("AmazonS3FullAccess"),
            ManagedPolicy.from_aws_managed_policy_name("AmazonSQSFullAccess"),
            ManagedPolicy.from_aws_managed_policy_name("AWSLambda_FullAccess"),
            ManagedPolicy.from_aws_managed_policy_name("CloudWatchFullAccess"),
            ManagedPolicy.from_aws_managed_policy_name("AWSCloudFormationReadOnlyAccess"),
            ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaSQSQueueExecutionRole"),
            ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole")
        ]
    )
            # ManagedPolicy.from_managed_policy_name(
            #     stackObjRef, 
            #     "lambda_key_access_policy", 
            #     managed_policy_name="KMS_Key_User")
    logger.info("Lambda roles defined")
    return lambdaRole


# A requirements.txt file on the lambda's root directory will trigger a pip install
# during deployment. This way, we do only one pip install for all deployed lambdas within a stack.
# Borrowed from https://stackoverflow.com/questions/58855739/how-to-install-external-modules-in-a-python-lambda-function-created-by-aws-cdk
def createDependenciesLayer(theStack, requirementsFile) -> LayerVersion:
    logger.info("Creating lambda layers")
    outputDir = f"{theStack.cwd}/.lambdaDependencies"

    # Install requirements for layer in the outputDir
    if not os.environ.get("SKIP_PIP"):
        # Clean out the existing directory if already there
        outputPath = pathlib.Path(outputDir)
        if outputPath.exists():
            shutil.rmtree(outputPath)

        # Note: pip will create the output dir if it does not exist
        subprocess.check_call(
            f"pip install -r {requirementsFile} -t {outputDir}/python".split()
        )

    return LayerVersion(
        theStack,
        theStack.baseStackName + "-dependencies",
        code=Code.from_asset(outputDir)
    )


# Some libraries need to be installed as binaries, specifically the crypto lib
def createDependenciesLayerBin(theStack, requirementsFile) -> LayerVersion:
    logger.info("Creating lambda layers")
    outputDir = f"{theStack.cwd}/.lambdaDependencies"

    # Install requirements for layer in the outputDir
    if not os.environ.get("SKIP_PIP"):
        # Clean out the existing directory if already there
        outputPath = pathlib.Path(outputDir)
        if outputPath.exists():
            shutil.rmtree(outputPath)

        # Note: pip will create the output dir if it does not exist
        # Crypto library requires libc binary to avoid this error:
        #   Unable to import module 'src.python.main': /lib64/libc.so.6: version `GLIBC_2.28' not found
        #                                            : (required by /opt/python/cryptography/hazmat/bindings/_rust.abi3.so)
        #   Info here: https://repost.aws/knowledge-center/lambda-python-package-compatible
        # The library is used to modify the CA file for VPN access
        subprocess.check_call(
            f"pip install -r {requirementsFile} -t {outputDir}/python --platform manylinux2014_x86_64 --only-binary=:all:".split()
        )

    return LayerVersion(
        theStack,
        theStack.baseStackName + "-dependencies",
        code=Code.from_asset(outputDir)
    )


def printAllAccounts(region) -> dict:
    botoSession = boto3.Session()
    profiles = botoSession.available_profiles

    logger.debug("****************ALL ACCOUNTS****************")
    for profile in profiles:
        botoSession = boto3.Session(profile_name=profile, region_name=region)
        stsClient = botoSession.client("sts")
        try:
            account = stsClient.get_caller_identity()["Account"]
            logger.debug(f"{profile},{account}")
        except bexcept.NoCredentialsError:
            logger.error(f"{profile},--- no credentials --")
        except bexcept.InvalidConfigError:
            logger.error(f"{profile},--- invalid config --")
        except Exception as exc:
            logger.error(f"{profile},--- exception --")
            logger.error(exc)
    logger.debug("****************END ALL ACCOUNTS****************")


def getAccountInfo(env: Environment, profile: str) -> dict:

    try:
        logger.debug("*******************")
        logger.debug(f"profile_name={profile}, region_name={env.region}")
        logger.debug("*******************")
        session = boto3.Session(profile_name=profile, region_name=env.region)
        sts_client = session.client("sts")
        accountId = sts_client.get_caller_identity()["Account"]
        region = sts_client.meta.region_name
    except Exception as exc:
        logger.error(exc)
        logger.error("Will now exit... (this may hang; go ahead and Ctrl+C)")
        raise KeyError

    result = {"accountId": accountId, "region": region}

    return result


def loggerSetup():
    # Set up some logging parameters and disable the really detailed logging by other packages
    logger.setLevel(logging.DEBUG)

    # If you want to see some behind-the-scenes action from AWS, comment out their lines (or change log levels)
    logging.getLogger("boto").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("s3transfer").setLevel(logging.WARNING)


# Create Subscription Filters so the Audit Service gets a copy of the logs
def makeLogSubscriptionFilter(
        stackObj: Stack,
        auditAccount: str,
        executionMode: str,
        lambdaObj: Function) -> SubscriptionFilter:

    logger.info(f"Setting audit service filters for {executionMode} environment")

    # Get the various fields we'll need later
    lambdaLog = lambdaObj.log_group
    lambdaRegion = lambdaObj.env.region

    # Derive the destination ARN
    destinationArn = f"arn:aws:logs:{lambdaRegion}:{auditAccount}:destination:collection-audit-data-stream-dest"

    # Create a random int to use as part of ID's - not seen in AWS Console
    randIdStr = str(randint(1000, 9999))

    # Get a reference to the Kinesis Stream we'll be sending to and identify it as the destination
    auditStreamId = "audit_stream_" + randIdStr
    auditStream = Stream.from_stream_arn(stackObj,
                                         auditStreamId,
                                         stream_arn=destinationArn)
    auditDestination = KinesisDestination(auditStream)

    # Create the pattern string we're using as a filter
    filterPattern = FilterPattern.literal('{ $.eventType = \"audit\" }')

    # Create the subscription filter
    subFilterId = "subscription_filter_" + randIdStr
    subFilter = SubscriptionFilter(stackObj,
                                   subFilterId,
                                   log_group=lambdaLog,
                                   destination=auditDestination,
                                   filter_pattern=filterPattern)
    return subFilter


def addFfprobeLayer(selfObj, accountNumber: str, region: str) -> LayerVersion:
    # Utilize a pre-deployed lambda layer
    # Layer zip file in the main resources directory; filename "ffprobe-lambdaLayer.zip"
    logger.debug("Connecting FFPROBE layer")

    ffprobeLayerArn = f"arn:aws:lambda:{region}:{accountNumber}:layer:ffprobe:1"
    return LayerVersion.from_layer_version_arn(
        selfObj,
        selfObj.stackName + "-ffprobe",
        layer_version_arn=ffprobeLayerArn
    )


# Borrowed from https://stackoverflow.com/questions/58855739/how-to-install-external-modules-in-a-python-lambda-function-created-by-aws-cdk
def createYtdlDependencyLayer(self) -> LayerVersion:
    logger.info("Creating yt-dlp layer")
    requirementsFile = "stacks/systemResources/lambdaYtdlp.txt"
    outputDir = f"{self.cwd}/.lambdaYtdlpDependency"

    # Install requirements for layer in the outputDir
    if not os.environ.get("SKIP_PIP"):
        # Clean out the existing directory if already there
        outputPath = pathlib.Path(outputDir)
        if outputPath.exists():
            shutil.rmtree(outputPath)

        subprocess.check_call(
            f"pip install -r {requirementsFile} -t {outputDir}/python".split()
    )

    return LayerVersion(
        self,
        self.stackName + "-ytdlDependency",
        code=Code.from_asset(outputDir)
    )


def addFfmpegLayer(selfObj, accountNumber: str, region: str) -> LayerVersion:
    # Utilize a pre-deployed lambda layer
    # Layer zip file in the main resources directory; filename "ffmpeg-lambdaLayer.zip"
    logger.debug("Connecting FFMPEG layer")

    # TODO: Remove hardcoded layer version
    #   Implement function to determine latest lambda layer version to use
    ffmpegLayerArn = f"arn:aws:lambda:{region}:{accountNumber}:layer:ffmpeg:2"
    return LayerVersion.from_layer_version_arn(
        selfObj,
        selfObj.stackName + "-ffmpeg",
        layer_version_arn=ffmpegLayerArn
    )


def addPycurlLayer(self) -> LayerVersion:
    # Add pycurl as a layer as it can't be pip installed due to dependency on libcurl
    logger.debug("Connecting Pycurl layer")
    requirementsFile = "stacks/systemResources/pycurl.txt"
    outputDir = f"{self.cwd}/.pycurlDependency"

    if not os.environ.get("SKIP_PIP"):
        # Clean out the existing directory if already there
        outputPath = pathlib.Path(outputDir)
        if outputPath.exists():
            shutil.rmtree(outputPath)

        subprocess.check_call(
            f"pip install -r {requirementsFile} -t {outputDir}/python".split()
    )

    return LayerVersion(
        self,
        self.stackName + "-pycurl",
        code=Code.from_asset(outputDir)
    )
