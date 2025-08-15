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
from aws_cdk import Duration, Stack
from aws_cdk.aws_lambda import Runtime
from aws_cdk.aws_logs import RetentionDays
from aws_cdk.aws_events_targets import LambdaFunction
from aws_cdk.aws_lambda_python_alpha import PythonFunction
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
from src.python.systemMode import SystemMode
from src.python.superGlblVars import config


logger = logging.getLogger()
logging.basicConfig(format="%(asctime)s - %(levelname)-8s - %(module)s:%(lineno)d - %(message)s")

# Get both sets of config settings (for the system and deployment)
executionMode = config["mode"]
cdkConfig = csf.initCdkSettings()
# Stupid DynaConf relies on environment variables
os.environ["ENV_FOR_DYNACONF"] = executionMode.name


class HPatrolZurichStack(Stack):
    cwd = str(pathlib.Path.cwd())

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

        # Construct the Lambdas
        self._createLambdasStructure()

        # Import/create the Lambda Layer for dependencies
        reqsFile = f"{self.cwd}/stacks/systemResources/lambdaRequirements.txt"
        self._dependsLayer = csf.createDependenciesLayer(self, reqsFile)
        logger.info("Lambda layers created")

        self._createCeriumLambda()
        self._createBazaNetLambda()


    def _createCeriumLambda(self) -> str:
        theLambda = PythonFunction(self, "ceriumLambda",
            description="Collects and parses Cerium site to create aimpoints",
            entry=self.buildDirs["ceriumBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Cerium",
            memory_size=1024,
            # Using "unreserved account concurrency"
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
        logger.info("Cerium lambda defined")

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
        # Running every day at 0602UTC (0202EST)
        cronRule = Rule(
            self, "CeriumRule",
            schedule=Schedule.cron(
                minute="02",
                hour="06",
                # day="?",
                month="*",
                week_day="*",
                year="*"),
            enabled=True
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createBazaNetLambda(self) -> str:
        theLambda = PythonFunction(self, "bazaNetLambda",
            description="Collects and parses bazaNet site to create aimpoints",
            entry=self.buildDirs["bazaNetBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_BazaNet",
            memory_size=1024,
            # Using "unreserved account concurrency"
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
        logger.info("BazaNet lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)

        # Create the "fireing" rule
        # Running every 12hrs
        # 2025.04.03 was weekly but one day the tokens changed after our trigger day and we lost access for a bit
        # 2025.04.14 had changed it to every 3days and had some gaps too
        rateRule = Rule(
            self, "BazaNetRule",
            schedule=Schedule.rate(Duration.hours(12)),
            enabled=True
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        rateRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    # This collates together the codes that will be uploaded to the lambdas
    # It's like this to save ourselves duplicate code management; reuse base code for different lambdas
    def _createLambdasStructure(self) -> None:
        commonSrcDir = f"{self.cwd}/stacks/common"
        ceriumSrcDir = f"{self.cwd}/stacks/generators/ceriumParser"
        bazaNetSrcDir = f"{self.cwd}/stacks/generators/bazaNetParser"

        logger.info("Constructing lambda build dirs")
        outputDir = f"{self.cwd}/.lambdaBuild"

        ceriumDir = outputDir + "/ceriumParser"
        bazaNetDir = outputDir + "/bazaNetParser"

        pathlib.Path(ceriumDir).mkdir(parents=True, exist_ok=True)
        copy_tree(ceriumSrcDir, ceriumDir)
        copy_tree(commonSrcDir, ceriumDir)

        pathlib.Path(bazaNetDir).mkdir(parents=True, exist_ok=True)
        copy_tree(bazaNetSrcDir, bazaNetDir)
        copy_tree(commonSrcDir, bazaNetDir)

        logger.info("Lambda build dirs created")
        self.buildDirs = {
              "ceriumBuildDir": ceriumDir
            , "bazaNetBuildDir": bazaNetDir
        }

        # Delete common/testResources on PROD deployments
        if executionMode == SystemMode.PROD:
            for aDir in self.buildDirs.values():
                toDel = f"{aDir}/testResources"
                # logger.debug(f"Deleting testResources dir: {toDel}")
                shutil.rmtree(toDel)
