# Python libraries import statements
import os
import sys
import pathlib
import logging


# AWS import statements
import boto3
from aws_cdk import Stack
from aws_cdk import Duration
from aws_cdk import aws_s3 as s3
from constructs import Construct
from aws_cdk import RemovalPolicy
from aws_cdk import aws_lambda as _lambda
from aws_cdk.aws_iam import ManagedPolicy, PermissionsBoundary


# Small trick so importing the system settings works
# This keeps us from having to re-specify certain values in different places
# e.g.: without this, we'd have to specify the bucketName in two files
projectPath = str(pathlib.Path.cwd())
commonPath = f"{projectPath}/stacks/common"
if commonPath not in sys.path:
    sys.path.insert(0, commonPath)
# print(f"\n\nsys.path: {sys.path}\n\n")

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


class HPatrolInitStack(Stack):
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
        csf.printAllAccounts(regionName)

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
        self._createBucket()
        self._createLayers()


    def _createLayers(self) -> None:
        self._createFfmpegLayer()
        self._createFfprobeLayer()


    def _createBucket(self) -> None:
        bucket = s3.Bucket(
            self,
            f"{self.baseStackName}-Bucket",
            access_control=s3.BucketAccessControl.BUCKET_OWNER_FULL_CONTROL,
            auto_delete_objects=False,  # would allow CDK to delete objects on stack deletion
            removal_policy=RemovalPolicy.RETAIN,
            # auto_delete_objects=True,             # for testing
            # removal_policy=RemovalPolicy.DESTROY, # for testing
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            bucket_name=self.bucketName,
            encryption=s3.BucketEncryption.S3_MANAGED,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_PREFERRED,
            public_read_access=False,
            versioned=False,
            lifecycle_rules=[
                # Abort incomplete multipart uploads after 2 days
                s3.LifecycleRule(
                    id="AbortIncompleteUploads",
                    enabled=True,
                    abort_incomplete_multipart_upload_after=Duration.days(2)
                ),
                # Delete expired object delete markers
                s3.LifecycleRule(
                    id="DeleteExpiredObjectDeleteMarkers",
                    enabled=True,
                    expired_object_delete_marker=True
                ),
                s3.LifecycleRule(
                    id="Expire hashes after 1 day",
                    enabled=True,
                    expiration=Duration.days(1), 
                    prefix="hashfiles/"
                ),
                s3.LifecycleRule(
                    id="Expire lz/ after 6months",
                    enabled=True,
                    expiration=Duration.days(180),
                    prefix="lz/"
                ),
                s3.LifecycleRule(
                    id="Expire audios/ after 6months",
                    enabled=True,
                    expiration=Duration.days(180),
                    prefix="audios/"
                ),
                s3.LifecycleRule(
                    id="Expire up/ after 7days",
                    enabled=True,
                    expiration=Duration.days(7),
                    prefix="up/"
                ),
                s3.LifecycleRule(
                    id="Expire aimpointStatus/ after 3days",
                    enabled=True,
                    expiration=Duration.days(3),
                    prefix="aimpointStatus/"
                ),
                s3.LifecycleRule(
                    id="Expire stillsLz/ after 6months",
                    enabled=True,
                    expiration=Duration.days(180),
                    prefix="stillsLz/"
                )
            ]
        )


    def _createFfmpegLayer(self) -> None:
        theLayer = _lambda.LayerVersion(
            self, "ffmpegLayer",
            code=_lambda.Code.from_asset(f"{projectPath}/projectResources/ffmpeg-lambdaLayer.zip"),
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_13],
            compatible_architectures=[_lambda.Architecture.X86_64],
            description="isolated ffmpeg",
            license="GPL-2.0-or-later",
            layer_version_name=f"{projectName}_ffmpeg"
        )   

    def _createFfprobeLayer(self) -> None:
        theLayer = _lambda.LayerVersion(
            self, "ffprobeLayer",
            code=_lambda.Code.from_asset(f"{projectPath}/projectResources/ffprobe-lambdaLayer.zip"),
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_13],
            compatible_architectures=[_lambda.Architecture.X86_64],
            description="isolated ffprobe",
            license="GPL-2.0-or-later",
            layer_version_name=f"{projectName}_ffprobe"
        )
