# Python libraries import statements
import os
import sys
import shutil
import pathlib
import logging
from distutils.dir_util import copy_tree


# AWS import statements
import boto3
from constructs import Construct
from aws_cdk.aws_lambda import Runtime
from aws_cdk import Duration, Stack, Size
from aws_cdk.aws_logs import RetentionDays
from aws_cdk.aws_lambda_python_alpha import PythonFunction
from aws_cdk.aws_iam import ManagedPolicy, PermissionsBoundary


# Small trick so importing the system settings works
# This keeps us from having to re-specify certain values in different places
# e.g.: without this, we'd have to specify the bucketName in two files
commonPath = str(pathlib.Path.cwd()) + "/stacks/common"
if commonPath not in sys.path:
    sys.path.insert(0, commonPath)
# print("\n\nsys.path: {}\n\n".format(sys.path))

# This application's import statements
from src.python import systemSettings     # variable not used; needed to load settings to memory
from . import commonStackFunctions as csf
from src.python.superGlblVars import config
from src.python.systemMode import SystemMode
from src.python.superGlblVars import projectName


logger = logging.getLogger()
logging.basicConfig(
    format="%(asctime)s - %(levelname)-8s - %(module)s:%(lineno)d - %(message)s"
)

# Get both sets of config settings (for the system and deployment)
executionMode = config["mode"]
cdkConfig = csf.initCdkSettings()
# Stupid DynaConf relies on environment variables
os.environ["ENV_FOR_DYNACONF"] = executionMode.name


class HPatrolCollectionStack(Stack):
    cwd = str(pathlib.Path.cwd())

    def __init__(
        self,
        profile: str,
        stackName: str,
        description: str,
        baseStackName: str,
        scope: Construct,
        **kwargs
    ) -> None:
        super().__init__(scope, stackName, description=description, **kwargs)

        env = None
        csf.loggerSetup()
        self.stackName = stackName
        self.baseStackName = baseStackName

        logger.info(f"Stack ID: {self.stackName}")

        if "env" in kwargs:
            env = kwargs.get("env")

        try:
            acctInfo = csf.getAccountInfo(env, profile)
        except KeyError as err:
            return None

        accountNumber = acctInfo["accountId"]
        logger.info(f"Account#: {accountNumber}")
        regionName = acctInfo["region"]
        logger.info(f"Region: {regionName}")
        # # Just for debugging; enable when necessary
        # csf.printAllAccounts(regionName)

        # try:
        #     self.botoSession = boto3.Session(
        #         profile_name=profile,
        #         region_name=regionName
        #     )
        #     # Now test if credentials are valid
        #     self.botoSession.client("kms").list_aliases()
        # except Exception as exc:
        #     logger.error(exc)
        #     logger.error("Will now exit... (this may hang; go ahead and Ctrl+C)")
        #     return None

        # # Apply the BoundaryPolicy to the entire stack
        # boundaryPolicy = ManagedPolicy.from_managed_policy_name(
        #     self,
        #     "permissions_boundary",
        #     "ose.boundary.DeveloperFull"
        # )
        # PermissionsBoundary.of(self).apply(boundaryPolicy)

        self._lambdaRole = csf.createLambdaRoles(self, cdkConfig)
        self._createLambdas(accountNumber, regionName)
        # This CDK recipe assumes that any S3 buckets necessary are already created
        # Note that the /hashfiles prefix in the bucket should have a policy to expire objects


    def _createLambdas(self, accountNumber, region) -> None:
        # Construct the Lambdas
        self._createLambdasStructure(region)

        # Import/create the Lambda Layer for dependencies
        ytdlpLayer = csf.createYtdlDependencyLayer(self)
        reqsFile = f"{self.cwd}/stacks/systemResources/lambdaRequirements.txt"
        self._dependsLayer = csf.createDependenciesLayerBin(self, reqsFile)
        self._dependsLayer = csf.createDependenciesLayer(self, reqsFile)
        ffprobeLayer = csf.addFfprobeLayer(self, accountNumber, region)
        ffmpegLayer = csf.addFfmpegLayer(self, accountNumber, region)
        pycurlLayer = csf.addPycurlLayer(self)
        logger.info("Lambda layers created")

        # self._createPlaywrightLambda(ffmpegLayer)     # Not working yet
        self._createStreamingVideosLambda(ffmpegLayer)
        # Notice Stills and Videos lambdas are similar; diff being ffprobe
        # TODO: Stills shouldn't need the ytdlpLayer
        self._createStillsLambda(ytdlpLayer, pycurlLayer)
        self._createVideosLambda(ffprobeLayer, ytdlpLayer, pycurlLayer)


    def _createStillsLambda(self, ytdlpLayer, pycurlLayer) -> None:
        theLambda = PythonFunction(
            self,
            "stillsLambda",
            description="Collects stills data from received parameters",
            entry=self.buildDirs["collectorBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Stills",
            memory_size=256,
            ephemeral_storage_size=Size.mebibytes(512),
            # Using "unreserved account concurrency"
            timeout=Duration.minutes(15),
            retry_attempts=0,
            log_retention=RetentionDays.THREE_MONTHS,
            # Stills don't use yt_dlp but needs it for "boot up" checks
            layers=[
                self._dependsLayer,
                pycurlLayer,
                ytdlpLayer
            ],
            environment={
                "stackName": self.stackName,
                "HPatrolStatusQueue": self.baseStackName + "_" + projectName + "Status",
            }
        )
        logger.info("Stills lambda defined")

        # # Connect the Subscription Filters for the Audit Service
        # csf.makeLogSubscriptionFilter(
        #     stackObj=self,
        #     executionMode=executionMode,
        #     auditAccount=cdkConfig["AUDIT_ACCOUNT"],
        #     lambdaObj=theLambda
        # )


    def _createVideosLambda(self, ffprobeLayer, ytdlpLayer, pycurlLayer) -> None:
        theLambda = PythonFunction(
            self,
            "videosLambda",
            description="Collects video data from received parameters",
            entry=self.buildDirs["collectorBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Videos",
            memory_size=2048,
            ephemeral_storage_size=Size.mebibytes(10240),
            # Using "unreserved account concurrency"
            timeout=Duration.minutes(15),
            retry_attempts=0,
            log_retention=RetentionDays.THREE_MONTHS,
            layers=[
                self._dependsLayer,
                ffprobeLayer,
                pycurlLayer,
                ytdlpLayer
            ],
            environment={
                "stackName": self.stackName,
                "HPatrolStatusQueue": self.baseStackName + "_" + projectName + "Status",
            }
        )
        logger.info("Videos lambda defined")

        # # Connect the Subscription Filters for the Audit Service
        # csf.makeLogSubscriptionFilter(
        #     stackObj=self,
        #     executionMode=executionMode,
        #     auditAccount=cdkConfig["AUDIT_ACCOUNT"],
        #     lambdaObj=theLambda
        # )


    def _createStreamingVideosLambda(self, ffmpegLayer) -> None:
        theLambda = PythonFunction(
            self,
            "videosStreamingLambda",
            description="Streams video and stores data from received parameters",
            entry=self.buildDirs["collectorBuildDir"],
            index="src/python/streamCollectorMain.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_StreamVideos",
            memory_size=2048,
            ephemeral_storage_size=Size.mebibytes(10240),
            # Using "unreserved account concurrency"
            timeout=Duration.minutes(15),
            retry_attempts=0,
            log_retention=RetentionDays.THREE_MONTHS,
            layers=[
                self._dependsLayer,
                ffmpegLayer
            ],
            environment={
                "stackName": self.stackName,
                "HPatrolStatusQueue": self.baseStackName + "_" + projectName + "Status",
            },
        )
        logger.info("Streaming Videos lambda defined")

        # # Connect the Subscription Filters for the Audit Service
        # csf.makeLogSubscriptionFilter(
        #     stackObj=self,
        #     executionMode=executionMode,
        #     auditAccount=cdkConfig["AUDIT_ACCOUNT"],
        #     lambdaObj=theLambda
        # )


    # Not working yet; kept here for future improvement
    # def _createPlaywrightLambda(self, ffmpegLayer) -> None:
    #     theLambda = PythonFunction(
    #         self,
    #         "playwrightLambda",
    #         description="Uses Playwright to intercept streamed video and stores data from received parameters",
    #         entry=self.buildDirs["collectorBuildDir"],
    #         index="main.py",
    #         role=self._lambdaRole,
    #         runtime=Runtime.FROM_IMAGE,
    #         handler="lambdaHandler",
    #         function_name=self.baseStackName + "_Playwright",
    #         memory_size=2048,
    #         ephemeral_storage_size=Size.mebibytes(4096),
    #         # Using "unreserved account concurrency"
    #         timeout=Duration.minutes(15),
    #         retry_attempts=0,
    #         log_retention=RetentionDays.THREE_MONTHS,
    #         layers=[
    #             self._dependsLayer,
    #             ffmpegLayer
    #         ],
    #         environment={
    #             "stackName": self.stackName,
    #             "HPatrolStatusQueue": self.baseStackName + "_" + projectName + "Status",
    #         }
    #     )
    #     logger.info("Playwright lambda defined")

    #     # Connect the Subscription Filters for the Audit Service
    #     csf.makeLogSubscriptionFilter(
    #         stackObj=self,
    #         executionMode=executionMode,
    #         auditAccount=cdkConfig["AUDIT_ACCOUNT"],
    #         lambdaObj=theLambda
    #     )


    # This collates together the codes that will be uploaded to the lambdas
    # It's like this to save ourselves duplicate code management; reuse base code for different lambdas
    # FYI: Newer versions of CDK now respect following softlinks, so this is not necessary if
    #      deploying with the links in place; however, that's not always the case.
    #      It appears we need to update this CDK script...when time allows
    # TODO: Update CDK script
    def _createLambdasStructure(self, region) -> None:
        commonSrcDir = f"{self.cwd}/stacks/common"
        collectorSrcDir = f"{self.cwd}/stacks/collector"

        logger.info("Constructing lambda build dirs")
        outputDir = f"{self.cwd}/.lambdaBuild/{region}"

        # Clean out the output directory if already there
        outputPath = pathlib.Path(outputDir)
        if outputPath.exists():
            logger.info(f"Clearing output dir: {outputDir}")
            shutil.rmtree(outputPath)

        collectorDir = outputDir + "/collector"
        pathlib.Path(collectorDir).mkdir(parents=True, exist_ok=True)
        copy_tree(collectorSrcDir, collectorDir)
        copy_tree(commonSrcDir, collectorDir)
        logger.info("Lambda build dirs created")

        self.buildDirs = {
              "collectorBuildDir": collectorDir
        }

        # Delete common/testResources on PROD deployments
        if executionMode == SystemMode.PROD:
            for aDir in self.buildDirs.values():
                toDel = f"{aDir}/testResources"
                # logger.debug(f"Deleting testResources dir: {toDel}")
                shutil.rmtree(toDel)
