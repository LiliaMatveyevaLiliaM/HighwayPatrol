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
from src.python.superGlblVars import config
from src.python.systemMode import SystemMode


logger = logging.getLogger()
logging.basicConfig(format="%(asctime)s - %(levelname)-8s - %(module)s:%(lineno)d - %(message)s")

# Get both sets of config settings (for the system and deployment)
executionMode = config["mode"]
cdkConfig = csf.initCdkSettings()
# Stupid DynaConf relies on environment variables
os.environ["ENV_FOR_DYNACONF"] = executionMode.name


class HPatrolStockholmStack(Stack):
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

        self._createCud59Lambda()
        self._createNorwayLambda()
        self._createMoidomLambda()
        self._createThoriumLambda()
        self._createFluorineLambda()


    def _createMoidomLambda(self) -> str:
        theLambda = PythonFunction(self, "moidomLambda",
            description="Collects and parses Moidom-Stream site to create aimpoints",
            entry=self.buildDirs["moidomBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Moidom",
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
        logger.info("Moidom lambda defined")

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
        # Running every day at 0905UTC
        cronRule = Rule(
            self, "MoidomRule",
            schedule=Schedule.cron(
                minute="05",
                hour="09", # 9 AM UTC, 5 AM EST
                # day="?",
                month="*",
                week_day="*",
                year="*"),
            enabled=True
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createThoriumLambda(self) -> str:
        theLambda = PythonFunction(self, "thoriumLambda",
            description="Collects and parses Thorium site to create aimpoints",
            entry=self.buildDirs["thoriumBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Thorium",
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
        logger.info("Thorium lambda defined")

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
        # Running every day at 0602UTC # (0202EST)
        cronRule = Rule(
            self, "ThoriumRule",
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


    def _createNorwayLambda(self) -> str:
        theLambda = PythonFunction(self, "norwayLambda",
            description="Collects and parses larger list to create aimpoints",
            entry=self.buildDirs["norwayBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Norway",
            memory_size=1024,
            # Using "unreserved account concurrency"
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
        logger.info("Norway lambda defined")

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
        # Running weekly at 1200UTC
        cronRule = Rule(
            self, "NorwayRule",
            schedule=Schedule.cron(
                minute="00",
                hour="12",
                # day="?",
                month="*",
                week_day="2",
                year="*"),
            enabled=False   # 03.05.24 Ray: Disabled on requestor"s orders
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createFluorineLambda(self) -> str:
        theLambda = PythonFunction(self, "fluorineLambda",
            description="Collects and parses Fluorine site to create aimpoints",
            entry=self.buildDirs["fluorineBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Fluorine",
            memory_size=2048,
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
        logger.info("Fluorine lambda defined")

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
        # Running every day at 0802UTC (0402EST)
        cronRule = Rule(
            self, "FluorineRule",
            schedule=Schedule.cron(
                minute="02",
                hour="08",
                # day="?",
                month="*",
                week_day="*",
                year="*"),
            enabled=True
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createCud59Lambda(self) -> str:
        theLambda = PythonFunction(self, "cud59Lambda",
            description="Collects and parses Cud59 site to create aimpoints",
            entry=self.buildDirs["cud59BuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Cud59",
            memory_size=2048,
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
        logger.info("Cud59 lambda defined")

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
        # Running every Monday at 1215UTC
        cronRule = Rule(
            self, "Cud59Rule",
            schedule=Schedule.cron(
                minute="15",
                hour="12",
                # day="*",
                month="*",
                week_day="MON",
                year="*"),
            enabled=True
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    # This collates together the codes that will be uploaded to the lambdas
    # It's like this to save ourselves duplicate code management; reuse base code for different lambdas
    def _createLambdasStructure(self) -> None:
        commonSrcDir = f"{self.cwd}/stacks/common"
        cud59SrcDir = f"{self.cwd}/stacks/generators/cud59Parser"
        norwaySrcDir = f"{self.cwd}/stacks/generators/norwayParser"
        moidomSrcDir = f"{self.cwd}/stacks/generators/moidomParser"
        thoriumSrcDir = f"{self.cwd}/stacks/generators/thoriumParser"
        fluorineSrcDir = f"{self.cwd}/stacks/generators/fluorineParser"

        logger.info("Constructing lambda build dirs")
        outputDir = f"{self.cwd}/.lambdaBuild"

        cud59Dir = outputDir + "/cud59Parser"
        norwayDir = outputDir + "/norwayParser"
        moidomDir = outputDir + "/moidomParser"
        thoriumDir = outputDir + "/thoriumParser"
        fluorineDir = outputDir + "/fluorineParser"

        pathlib.Path(cud59Dir).mkdir(parents=True, exist_ok=True)
        copy_tree(cud59SrcDir, cud59Dir)
        copy_tree(commonSrcDir, cud59Dir)

        pathlib.Path(norwayDir).mkdir(parents=True, exist_ok=True)
        copy_tree(norwaySrcDir, norwayDir)
        copy_tree(commonSrcDir, norwayDir)

        pathlib.Path(thoriumDir).mkdir(parents=True, exist_ok=True)
        copy_tree(thoriumSrcDir, thoriumDir)
        copy_tree(commonSrcDir, thoriumDir)

        pathlib.Path(moidomDir).mkdir(parents=True, exist_ok=True)
        copy_tree(moidomSrcDir, moidomDir)
        copy_tree(commonSrcDir, moidomDir)

        pathlib.Path(fluorineDir).mkdir(parents=True, exist_ok=True)
        copy_tree(fluorineSrcDir, fluorineDir)
        copy_tree(commonSrcDir, fluorineDir)

        logger.info("Lambda build dirs created")

        self.buildDirs = {
              "cud59BuildDir": cud59Dir
            , "norwayBuildDir": norwayDir
            , "moidomBuildDir": moidomDir
            , "thoriumBuildDir": thoriumDir
            , "fluorineBuildDir": fluorineDir
        }

        # Delete common/testResources on PROD deployments
        if executionMode == SystemMode.PROD:
            for aDir in self.buildDirs.values():
                toDel = f"{aDir}/testResources"
                # logger.debug(f"Deleting testResources dir: {toDel}")
                shutil.rmtree(toDel)
