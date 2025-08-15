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


class HPatrolFrankfurtStack(Stack):
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

        self._createRdtcLambda()
        self._createCam72Lambda()
        self._createAvantaLambda()
        self._createUfanetLambda()
        self._createInterraLambda()
        self._createLantaMeLambda()
        self._createAstrakhanLambda()
        self._createSaferegionLambda()


    def _createCam72Lambda(self) -> str:
        theLambda = PythonFunction(self, "cam72Lambda",
            description="Collects and parses Cam72 site to create aimpoints",
            entry=self.buildDirs["cam72BuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Cam72",
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
        logger.info("Cam72 lambda defined")

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
        # Running every 4 days; starting at minute 5 just because
        cronRule = Rule(
            self, "Cam72Rule",
            schedule=Schedule.cron(
                minute="05",
                hour="00",
                day="*/4",
                month="*",
                # week_day="*/2",
                year="*"),
            enabled=True
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createAvantaLambda(self) -> str:
        theLambda = PythonFunction(self, "avantaLambda",
            description="Collects and parses Avanta-Telecom site to create aimpoints",
            entry=self.buildDirs["avantaBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Avanta",
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
        logger.info("Avanta lambda defined")

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
        # Running every day at 1014UTC
        cronRule = Rule(
            self, "AvantaRule",
            schedule=Schedule.cron(
                minute="14",
                hour="10",
                # day="?",
                month="*",
                week_day="*",
                year="*"),
            enabled=True
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createSaferegionLambda(self) -> str:
        theLambda = PythonFunction(self, "saferegionLambda",
            description="Collects and parses SaferegionNet site to create aimpoints",
            entry=self.buildDirs["saferegionBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Saferegion",
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
        logger.info("SaferegionNet lambda defined")

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
        # Running every 12hrs at 0614, and 1814
        cronRule = Rule(
            self, "SaferegionRule",
            schedule=Schedule.cron(
                minute="14",
                hour="06,18",
                day="*",
                month="*",
                # week_day="?",
                year="*"),
            enabled=False   # 2025.03.12 Disabled; seems redirecting and behind paywall; re-check in future
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createInterraLambda(self) -> str:
        theLambda = PythonFunction(self, "interraLambda",
            description="Collects and parses interra site to create aimpoints",
            entry=self.buildDirs["interraBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Interra",
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
        logger.info("Interra lambda defined")

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
        # Running every Monday at 12:23
        cronRule = Rule(
            self, "InterraRule",
            schedule=Schedule.cron(
                minute="23",
                hour="12",
                # day="?",
                month="*",
                week_day="MON",
                year="*"),
            enabled=True
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createLantaMeLambda(self) -> str:
        theLambda = PythonFunction(self, "lantaMeLambda",
            description="Collects and parses LantaMe site to create aimpoints",
            entry=self.buildDirs["lantaMeBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_LantaMe",
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
        logger.info("LantaMe lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)

        # Create the "fireing" rule
        # AWS supports rate rules, too
        # Running every 157 minutes (every 2.5hours and 7minutes)
        # This will look pretty random unless scrutinized 
        rateRule = Rule(
            self, "LantaMeRule",
            schedule=Schedule.rate(Duration.minutes(157)),
            enabled=True
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        rateRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Rate rule defined")

    def _createUfanetLambda(self) -> str:
        theLambda = PythonFunction(self, "ufanetLambda",
            description="Collects and parses Ufanet site to create aimpoints",
            entry=self.buildDirs["ufanetBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Ufanet",
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
        logger.info("Ufanet lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)

        # Create the "fireing" rule for Ufanet
        # AWS cron expressions have the following format:
        #   cron(Minutes Hours Day-of-month Month Day-of-week Year)
        # Note that you cannot supply both "day" and "weekDay" at the same time; it will error out
        # Running every Tuesday at 20:19
        cronRule = Rule(
            self, "UfanetRule",
            schedule=Schedule.cron(
                minute="19",
                hour="20",
                # day="*",
                month="*",
                week_day="TUE",
                year="*"),
            enabled=True
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createRdtcLambda(self) -> None:
        theLambda = PythonFunction(self, "rdtcLambda",
            description="Collects and parses rdtc site (city-n) to create aimpoints",
            entry=self.buildDirs["rdtcBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Rdtc",
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
        logger.info("rdtc lambda defined")

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
        # Running weekly
        cronRule = Rule(
            self, "RdtcRule",
            schedule=Schedule.cron(
                minute="25",
                hour="05",
                week_day="SUN",
                month="*",
                year="*"),
            enabled=True
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createAstrakhanLambda(self) -> None:
        theLambda = PythonFunction(
            self,
            "astrakhanLambda",
            description="Collects and parses astrakhan.ru domain to create aimpoints and collect camera metadata",
            entry=self.buildDirs["astrakhanBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_Astrakhan",
            memory_size=1024,
            # Using "unreserved account concurrency"
            timeout=Duration.minutes(15),
            retry_attempts=1,
            log_retention=RetentionDays.ONE_MONTH,
            layers=[self._dependsLayer],
            environment={"stackName": self.stackName},
        )
        logger.info("Astrakhan lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda
        )

        # Create the "fireing" rule
        # AWS cron expressions have the following format:
        #   cron(Minutes Hours Day-of-month Month Day-of-week Year)
        # Note that you cannot supply both "day" and "weekDay" at the same time; it will error out
        # Running every week
        cronRule = Rule(
            self,
            "AstrakhanRule",
            schedule=Schedule.cron(
                minute="25",
                hour="05",
                # day="?",
                month="*",
                week_day="SUN",
                year="*"),
            enabled=True
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(
            LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput))
        )
        logger.info("Cron rule defined")


    # This collates together the codes that will be uploaded to the lambdas
    # It's like this to save ourselves duplicate code management; reuse base code for different lambdas
    def _createLambdasStructure(self) -> None:
        commonSrcDir = f"{self.cwd}/stacks/common"
        cam72SrcDir = f"{self.cwd}/stacks/generators/cam72Parser"
        avantaSrcDir = f"{self.cwd}/stacks/generators/avantaParser"
        ufanetSrcDir = f"{self.cwd}/stacks/generators/ufanetParser"
        rdctSrcDir = f"{self.cwd}/stacks/generators/ipcamRdtcParser"
        interraSrcDir = f"{self.cwd}/stacks/generators/interraParser"
        lantaMeSrcDir = f"{self.cwd}/stacks/generators/lantaMeParser"
        astrakhanSrcDir = f"{self.cwd}/stacks/generators/astrakhanParser"
        saferegionSrcDir = f"{self.cwd}/stacks/generators/saferegionNetParser"

        logger.info("Constructing lambda build dirs")
        outputDir = f"{self.cwd}/.lambdaBuild"

        cam72Dir = outputDir + "/cam72Parser"
        avantaDir = outputDir + "/avantaParser"
        ufanetDir = outputDir + "/ufanetParser"
        rdtcDir = outputDir + "/ipcamRdtcParser"
        interraDir = outputDir + "/interraParser"
        lantaMeDir = outputDir + "/lantaMeParser"
        astrakhanDir = outputDir + "/astrakhanParser"
        lantaMeBuildDir = outputDir + "/lantaMeParser"
        saferegionDir = outputDir + "/saferegionNetParser"

        pathlib.Path(rdtcDir).mkdir(parents=True, exist_ok=True)
        copy_tree(rdctSrcDir, rdtcDir)
        copy_tree(commonSrcDir, rdtcDir)

        pathlib.Path(cam72Dir).mkdir(parents=True, exist_ok=True)
        copy_tree(cam72SrcDir, cam72Dir)
        copy_tree(commonSrcDir, cam72Dir)

        pathlib.Path(avantaDir).mkdir(parents=True, exist_ok=True)
        copy_tree(avantaSrcDir, avantaDir)
        copy_tree(commonSrcDir, avantaDir)

        pathlib.Path(ufanetDir).mkdir(parents=True, exist_ok=True)
        copy_tree(ufanetSrcDir, ufanetDir)
        copy_tree(commonSrcDir, ufanetDir)

        pathlib.Path(interraDir).mkdir(parents=True, exist_ok=True)
        copy_tree(interraSrcDir, interraDir)
        copy_tree(commonSrcDir, interraDir)

        pathlib.Path(lantaMeDir).mkdir(parents=True, exist_ok=True)
        copy_tree(lantaMeSrcDir, lantaMeDir)
        copy_tree(commonSrcDir, lantaMeDir)

        pathlib.Path(saferegionDir).mkdir(parents=True, exist_ok=True)
        copy_tree(saferegionSrcDir, saferegionDir)
        copy_tree(commonSrcDir, saferegionDir)

        pathlib.Path(astrakhanDir).mkdir(parents=True, exist_ok=True)
        copy_tree(astrakhanSrcDir, astrakhanDir)
        copy_tree(commonSrcDir, astrakhanDir)

        logger.info("Lambda build dirs created")

        self.buildDirs = {
              "rdtcBuildDir": rdtcDir
            , "cam72BuildDir": cam72Dir
            , "avantaBuildDir": avantaDir
            , "ufanetBuildDir": ufanetDir
            , "interraBuildDir": interraDir
            , "lantaMeBuildDir": lantaMeDir
            , "astrakhanBuildDir": astrakhanDir
            , "lantaMeBuildDir": lantaMeBuildDir
            , "saferegionBuildDir": saferegionDir
        }

        # Delete common/testResources on PROD deployments
        if executionMode == SystemMode.PROD:
            for aDir in self.buildDirs.values():
                toDel = f"{aDir}/testResources"
                # logger.debug(f"Deleting testResources dir: {toDel}")
                shutil.rmtree(toDel)
