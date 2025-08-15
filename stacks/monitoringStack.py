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


class HPatrolMonitoringStack(Stack):
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
        self._createStatusQueue()
        self._createLambdas()


    def _createStatusQueue(self) -> None:
        self._statusQueue = Queue(
            self, "HPatrolStatusQueue",
            queue_name=self.baseStackName + "_" + projectName + "Status",
            encryption=QueueEncryption.KMS_MANAGED,
            visibility_timeout=Duration.hours(2), 
            retention_period=Duration.minutes(30)
        )
        logger.info("Status queue defined")


    def _createLambdas(self)-> None:
        # Construct the Lambdas
        self._createLambdasStructure()

        # Import/create the Lambda Layer for dependencies
        reqsFile = f"{self.cwd}/stacks/systemResources/lambdaRequirements.txt"
        self._dependsLayer = csf.createDependenciesLayer(self, reqsFile)
        logger.info("Lambda layers created")

        self._createMonitorLambda()
        self._createHistorianLambda()
        self._createDisablerLambda()
        self._createEnablerLambda()


    def _createMonitorLambda(self) -> None:
        theLambda = PythonFunction(self, "monitorLambda",
            description="Periodically sends aimpoints in the monitored directory to the Dispatcher to start work",
            entry=self.buildDirs["monitorBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Monitor",
            memory_size=512,
            timeout=Duration.minutes(15),
            retry_attempts=1,
            log_retention=RetentionDays.ONE_MONTH,
            layers=[
                self._dependsLayer
            ],
            environment={
                "stackName": self.stackName,
                "HPatrolDispatchQueue": self.baseStackName + "_" + projectName + "Dispatch", 
            }
        )
        logger.info("Monitor lambda defined")

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
        # Runs every 12hours
        rateRule = Rule(
            self, "MonitorRule",
            schedule=Schedule.rate(Duration.hours(12)),
            enabled=True
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        rateRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createHistorianLambda(self) -> None:
        #Create an SQS event source for Lambda
        sqsEventSource = SqsEventSource(self._statusQueue,
                                        max_batching_window=Duration.seconds(300),
                                        batch_size=10000)

        theLambda = PythonFunction(self, "historianLambda",
            description="Logs results from collectors in the 'aimpointStatus' directory.",
            entry=self.buildDirs["historianBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Historian",
            memory_size=512,
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
        logger.info("Historian lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)


    def _createDisablerLambda(self) -> None:
        theLambda = PythonFunction(self, "disablerLambda",
            description="Periodically checks the status of aimpoints on collection.\
                         If consistent failure for 30 minutes is detected, move aimpoint to the '/monitored' directory",
            entry=self.buildDirs["disablerBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Disabler",
            memory_size=1024,
            timeout=Duration.minutes(15),
            retry_attempts=1,
            log_retention=RetentionDays.ONE_MONTH,
            layers=[
                self._dependsLayer
            ],
            environment={
                "stackName": self.stackName
            }
        )
        logger.debug("Disabler lambda defined")

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
        # Running every 30mins
        cronRule = Rule(
            self, "DisablerRule",
            schedule=Schedule.cron(
                minute="*/30",
                hour="*",
                # day="?",
                month="*",
                week_day="*",
                year="*"),
            enabled=True
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.debug("Cron rule defined")


    def _createEnablerLambda(self) -> None:
        theLambda = PythonFunction(self, "enablerLambda",
            description="Periodically checks status of aimpoints in 'monitored' status.\
                         Any successful collection re-enables the aimpoint.",
            entry=self.buildDirs["enablerBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Enabler",
            memory_size=1024,
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

        logger.debug("Enabler lambda defined")


        # Create the "fireing" rule
        # AWS cron expressions have the following format:
        #   cron(Minutes Hours Day-of-month Month Day-of-week Year)
        # Note that you cannot supply both "day" and "weekDay" at the same time; it will error out
        # Running every 5mins
        cronRule = Rule(
            self, "EnablerRule",
            schedule=Schedule.cron(
                minute="*/5",
                hour="*",
                # day="?",
                month="*",
                week_day="*",
                year="*"),
            enabled=True
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.debug("Cron rule defined")


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
              "enabler"
            , "monitor"
            , "disabler"
            , "historian"
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
