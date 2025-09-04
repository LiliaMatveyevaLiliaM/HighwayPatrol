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
from aws_cdk.aws_sqs import Queue
from aws_cdk.aws_lambda import Runtime
from aws_cdk import Duration, Stack, Size
from aws_cdk.aws_logs import RetentionDays
from aws_cdk.aws_sqs import QueueEncryption
from aws_cdk.aws_events_targets import LambdaFunction
from aws_cdk.aws_lambda_python_alpha import PythonFunction
from aws_cdk.aws_lambda_event_sources import SqsEventSource
from aws_cdk.aws_events import Rule, Schedule, RuleTargetInput
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
logging.basicConfig(format="%(asctime)s - %(levelname)-8s - %(module)s:%(lineno)d - %(message)s")

# Get both sets of config settings (for the system and deployment)
executionMode = config["mode"]
cdkConfig = csf.initCdkSettings()
# Stupid DynaConf relies on environment variables
os.environ["ENV_FOR_DYNACONF"] = executionMode.name


class HPatrolProcessingStack(Stack):
    cwd = str(pathlib.Path.cwd())

    bucketName = config["defaultWrkBucket"]

    def __init__(self,
        profile: str,
        stackName: str,
        description: str,
        baseStackName: str,
        scope: Construct,
        **kwargs) -> None:
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

        try:
            self.botoSession = boto3.Session(profile_name=profile, region_name=regionName)
            # Now test if credentials are valid
            self.botoSession.client("kms").list_aliases()
        except Exception as exc:
            logger.error(exc)
            logger.error("Will now exit... (this may hang; go ahead and Ctrl+C)")
            return None

        # Apply the BoundaryPolicy to the entire stack
        boundaryPolicy = ManagedPolicy.from_managed_policy_name(self, "permissions_boundary", "ose.boundary.DeveloperFull")
        PermissionsBoundary.of(self).apply(boundaryPolicy)

        self._lambdaRole = csf.createLambdaRoles(self, cdkConfig)
        self._createQueues()
        self._createLambdas(accountNumber, regionName)
        # This CDK recipe assumes that any S3 buckets necessary are already created


    def _createQueues(self) -> None:
        # self._createBaggingQueue()
        self._createDispatchQueue()
        # self._createTranscodingQueue()


    def _createBaggingQueue(self) -> None:
        # Construct the SQS Queue
        self._baggingQueue = Queue(
            self, "HPatrolBaggingQueue",
            queue_name=self.baseStackName + "_" + projectName + "Bagging",
            encryption=QueueEncryption.KMS_MANAGED,
            visibility_timeout=Duration.hours(2)
        )
        logger.debug("Bagging queue defined")


    def _createTranscodingQueue(self) -> None:
        # Construct the SQS Queue
        self._transcodeQueue = Queue(
            self, "HPatrolTranscodeQueue",
            queue_name=self.baseStackName + "_" + projectName + "Transcode",
            encryption=QueueEncryption.KMS_MANAGED,
            visibility_timeout=Duration.hours(2)
        )
        logger.debug("Transcoding queue defined")


    def _createDispatchQueue(self) -> None:
        # Construct the SQS Queue
        self._dispatchQueue = Queue(
            self, "HPatrolDispatchQueue",
            queue_name=self.baseStackName + "_" + projectName + "Dispatch",
            encryption=QueueEncryption.KMS_MANAGED,
            visibility_timeout=Duration.hours(2)
        )
        logger.info("Dispatch queue defined")


    def _createLambdas(self, accountNumber, region) -> None:
        # Construct the Lambdas
        self._createLambdasStructure()

        # Import/create the Lambda Layer for dependencies
        reqsFile = f"{self.cwd}/stacks/systemResources/lambdaRequirements.txt"
        self._dependsLayer = csf.createDependenciesLayer(self, reqsFile)
        ffmpegLayer = csf.addFfmpegLayer(self, accountNumber, region)
        logger.info("Lambda layers created")

        # self._createMinionLambda()
        # self._createDroverLambda()
        # self._createMarshalLambda()
        self._createSchedulerLambda()
        self._createDispatcherLambda()
        # self._createTranscoderLambda(ffmpegLayer)


    def _createSchedulerLambda(self) -> None:
        theLambda = PythonFunction(self, "schedulerLambda",
            description="Distributes work to the Dispatcher to start work",
            entry=self.buildDirs["schedulerBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Scheduler",
            memory_size=512,
            # Using "unreserved account concurrency"
            timeout=Duration.minutes(15),
            retry_attempts=1,
            log_retention=RetentionDays.ONE_MONTH,
            layers=[
                self._dependsLayer
            ],
            environment={
                "stackName": self.stackName,
                "HPatrolDispatchQueue": self._dispatchQueue.queue_url
            }
        )
        logger.info("Scheduler lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)

        # Create the "fireing" rule
        # AWS cron expressions have the following format:
        #   cron(Minutes Hours Day-of-month Month Day-of-week Year)
        # Note that you cannot supply both "day" and "weekDay" at the same time; it will error out
        # Running every 10mins
        cronRule = Rule(
            self, "SchedulerRule",
            schedule=Schedule.cron(
                minute="00/10",
                hour="*",
                # day="?",
                month="*",
                week_day="*",
                year="*"),
            enabled=True
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createDispatcherLambda(self) -> None:
        #Create an SQS event source for Lambda
        sqsEventSource = SqsEventSource(self._dispatchQueue, batch_size=1)

        theLambda = PythonFunction(self, "dispatcherLambda",
            description="Invokes the Collector lambdas based on instructions received",
            entry=self.buildDirs["dispatcherBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Dispatcher",
            memory_size=1024,
            # Using "unreserved account concurrency"
            events=[sqsEventSource],
            timeout=Duration.minutes(15),
            retry_attempts=0,
            log_retention=RetentionDays.ONE_MONTH,
            layers=[
                self._dependsLayer
            ],
            environment={
                "stackName": self.stackName
            }
        )
        logger.info("Dispatcher lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)


    def _createMarshalLambda(self) -> None:
        theLambda = PythonFunction(self, "marshalLambda",
            description="Identifies still image files and sends to Minion to collate and zip",
            entry=self.buildDirs["marshalBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Marshal",
            memory_size=1024,
            # Using "unreserved account concurrency"
            timeout=Duration.minutes(15),
            retry_attempts=1,
            log_retention=RetentionDays.ONE_MONTH,
            layers=[
                self._dependsLayer
            ],
            environment={
                "stackName": self.stackName,
                "HPatrolBaggingQueue": self._baggingQueue.queue_url
            }
        )
        logger.debug("Marshal lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)

        # Create the "fireing" rule
        # AWS cron expressions have the following format:
        #   cron(Minutes Hours Day-of-month Month Day-of-week Year)
        # Note that you cannot supply both "day" and "weekDay" at the same time; it will error out
        # Running every day at 0800EDT
        cronRule = Rule(
            self, "MarshalRule",
            schedule=Schedule.cron(
                minute="00",
                hour="12",
                # day="?",
                month="*",
                week_day="*",
                year="*"),
            enabled=True
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.debug("Cron rule defined")


    def _createMinionLambda(self) -> None:
        #Create an SQS event source for Lambda
        sqsEventSource = SqsEventSource(self._baggingQueue, batch_size=1)

        # Notice that this lambda has greater memory capacity
        theLambda = PythonFunction(self, "minionLambda",
            description="Collate still images into zips and place in pickup location",
            entry=self.buildDirs["minionBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Minion",
            memory_size=1024,
            # Using "unreserved account concurrency"
            ephemeral_storage_size=Size.mebibytes(4096),
            events=[sqsEventSource],
            timeout=Duration.minutes(15),
            retry_attempts=0,
            log_retention=RetentionDays.ONE_MONTH,
            layers=[
                self._dependsLayer
            ],
            environment={
                "stackName": self.stackName
            }
        )

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)

        logger.debug("Minion lambda defined")


    def _createDroverLambda(self) -> None:
        theLambda = PythonFunction(self, "droverLambda",
            description="Identifies aimpoints to send tasks to Tanscoder",
            entry=self.buildDirs["droverBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Drover",
            memory_size=1024,
            # Using "unreserved account concurrency"
            timeout=Duration.minutes(15),
            retry_attempts=1,
            log_retention=RetentionDays.ONE_MONTH,
            layers=[
                self._dependsLayer
            ],
            environment={
                "stackName": self.stackName,
                "HPatrolTranscodeQueue": self._transcodeQueue.queue_url
            }
        )
        logger.debug("Drover lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)

        # Create the "fireing" rules
        # AWS rate expressions have the following format:
        #   rate(duration: Duration)
        # Running every minute
        cronRule = Rule(
            self, "DroverRule-Transcode",
            schedule=Schedule.rate(Duration.minutes(1)),
            enabled=True
        )
        tgtInput = {"task": "transcode"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))

        # Running every minute
        cronRule = Rule(
            self, "DroverRule-Audios",
            schedule=Schedule.rate(Duration.minutes(1)),
            enabled=True
        )
        tgtInput = {"task": "audio"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))

        # Note however, that the rule for Timelapse operations is on cron format
        # AWS cron expressions have the following format:
        #   cron(Minutes Hours Day-of-month Month Day-of-week Year)
        # Note that you cannot supply both "day" and "weekDay" at the same time; it will error out
        # Running every 10mins
        cronRule = Rule(
            self, "DroverRule-Timelapse",
            schedule=Schedule.cron(
                minute="00/10",
                hour="*",
                # day="?",
                month="*",
                week_day="*",
                year="*"),
            enabled=True
        )
        tgtInput = {"task": "timelapse"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.debug("Cron rules defined")


    def _createTranscoderLambda(self, ffmpegLayer) -> None:
        #Create an SQS event source for Lambda
        sqsEventSource = SqsEventSource(self._transcodeQueue, batch_size=1)

        # Notice that this lambda has greater memory capacity
        theLambda = PythonFunction(self, "transcoderLambda",
            description="Collate video clips and transcode them",
            entry=self.buildDirs["transcoderBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Transcoder",
            memory_size=2560,
            # Using "unreserved account concurrency"
            ephemeral_storage_size=Size.mebibytes(4096),
            events=[sqsEventSource],
            timeout=Duration.minutes(15),
            log_retention=RetentionDays.ONE_MONTH,
            layers=[
                self._dependsLayer,
                ffmpegLayer
            ],
            environment={
                "stackName": self.stackName
            }
        )

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)

        logger.debug("Transcoder lambda defined")


    class LambdaBuildDir:
        def __init__(self, name, srcDir, outputDir):
            self.name = name
            self.srcDir = srcDir
            self.outputDir = outputDir


    # This collates together the codes that will be uploaded to the lambdas
    # It's like this to save ourselves duplicate code management; reuse base code for different lambdas
    def _createLambdasStructure(self) -> None:
        commonSrcDir = f"{self.cwd}/stacks/common"
        outputDir = f"{self.cwd}/.lambdaBuild"

        # Clean out the output directory if already there
        outputPath = pathlib.Path(outputDir)
        if outputPath.exists():
            logger.info(f"Clearing output dir: {outputDir}")
            shutil.rmtree(outputPath)

        systemComponents = [
              "drover"
            , "minion"
            , "marshal"
            , "scheduler"
            , "transcoder"
            , "dispatcher"
        ]

        componentDirs = [self.LambdaBuildDir(
            name = component,
            srcDir = f"{self.cwd}/stacks/{component}", 
            outputDir = f"{outputDir}/{component}")
            for component in systemComponents]

        logger.info("Constructing the Lambda build dirs")
        self.buildDirs = {}
        for component in componentDirs:
            pathlib.Path(component.outputDir).mkdir(parents=True, exist_ok=True)
            copy_tree(commonSrcDir, component.outputDir)
            copy_tree(component.srcDir, component.outputDir)
            self.buildDirs[f"{component.name}BuildDir"] = component.outputDir

        logger.info("Lambda build dirs created")
        # logger.debug(self.buildDirs)

        # Delete common/testResources on PROD deployments
        if executionMode == SystemMode.PROD:
            for aDir in self.buildDirs.values():
                toDel = f"{aDir}/testResources"
                # logger.debug(f"Deleting testResources dir: {toDel}")
                shutil.rmtree(toDel)
