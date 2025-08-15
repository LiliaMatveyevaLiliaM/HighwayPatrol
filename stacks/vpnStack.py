"""
One file, two stacks:
vpnLambdas
vpnProxies

The only reason to have them separate is because destruction (and/or redeployment)
of either blocks for too long. This is because of AWS handling of AutoScaling Groups.
See note at end of this file.
"""

# Python libraries import statements
import os
import sys
import shutil
import pathlib
import logging
from distutils.dir_util import copy_tree


# AWS import statements
from constructs import Construct
from aws_cdk import SecretValue
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk.aws_lambda import Runtime
from aws_cdk import aws_servicediscovery
from aws_cdk import Duration, Stack, Size
from aws_cdk.aws_logs import RetentionDays
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_secretsmanager as secrets
from aws_cdk import aws_autoscaling as autoscaling
from aws_cdk.aws_lambda_python_alpha import PythonFunction
from aws_cdk.aws_iam import ManagedPolicy, PermissionsBoundary, Role, ServicePrincipal


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
from src.python.superGlblVars import proxyCaFile
from stacks.protonVpnProxy.protonVpnCountryCodes import countryCodes


logger = logging.getLogger()
logging.basicConfig(format="%(asctime)s - %(levelname)-8s - %(module)s:%(lineno)d - %(message)s")

# Get both sets of config settings (for the system and deployment)
executionMode = config["mode"]
cdkConfig = csf.initCdkSettings()
# Stupid DynaConf relies on environment variables
os.environ["ENV_FOR_DYNACONF"] = executionMode.name


class HPatrolVpnProxies(Stack):
    cwd = str(pathlib.Path.cwd())

    def __init__(self,
        profile: str,
        stackName: str,
        description: str,
        baseStackName: str,
        scope: Construct,
        **kwargs) -> None:
        super().__init__(scope, stackName, description=description, **kwargs)

        csf.loggerSetup()
        self.stackName = stackName
        self.baseStackName = baseStackName
        # self.region already set by super()
        # self.account already set by super()

        # Report out which configuration environment we are using (dev/test/prod) 
        logger.debug(f"Using configuration:  {executionMode.name}")

        # Apply the BoundaryPolicy to the entire stack
        boundaryPolicy = ManagedPolicy.from_managed_policy_name(
            self, 
            id="permissions_boundary",
            managed_policy_name=cdkConfig["PERMISSIONS_BOUNDARY"]
        )
        PermissionsBoundary.of(self).apply(boundaryPolicy)

        # Create a CloudWatch log group for the stack
        self.stackLogGroup = logs.LogGroup(
            self, id=f"{baseStackName}-Stack-Log-Group",
            retention=logs.RetentionDays.ONE_WEEK
        )

        # Secrets for VPN clients
        self._createSecrets()

        # Derive the VPC to be used
        self.stackVpc = ec2.Vpc.from_lookup(self,
            id=f"{baseStackName}-VPC",
            vpc_name=cdkConfig["VPC_NAME"],
            is_default=False
        )

        # Subnet for the VPC
        self.ecsVpcSubnet = ec2.SubnetSelection(
            subnet_filters=[                    
                ec2.SubnetFilter().by_ids([cdkConfig["ECS_SUBNET"]])
            ]
        )

        # Service Dicovery for DNS ip-to-service mapping
        #   Steps to expose a service using private DNS
        #   1) create a cloudmap namespace (e.g. example.local)
        #   2) create a cloudmap service (e.g. my-service) under that namespace
        #   3) link the service to the ECS service; the resulting record is my-service.example.local

        # Step 1: create a cloudmap namespace
        oneNamespace = aws_servicediscovery.PrivateDnsNamespace(self,
            f"{baseStackName}-Proxies",
            vpc=self.stackVpc,
            name=f"{projectName}.dom",
            description=f"{baseStackName}'s VPN service"
        )

        # Create the VPN clients as services
        # Role that ECS jobs will assume
        taskExecutionRole = _createEcsRoles(baseStackName, cdkConfig, self)

        # Security group for the cluster
        ecsSecurityGroup = ec2.SecurityGroup.from_security_group_id(
            self,
            id=f"{self.baseStackName}-ECSSecurityGroup",
            security_group_id=cdkConfig["SEC_GROUP"],
            allow_all_outbound=True
        )
        ecsCluster = self._createEcsCluster(ecsSecurityGroup, cdkConfig)

        # As of 11.25.24 ProtonVPN allows at most 10 connections per account
        # If more are needed, another account must be purchased
        proxyCountries = cdkConfig["PROXY_COUNTRIES"]
        thePort = cdkConfig["PROXY_LISTEN_PORT"]

        # Identify country codes from the country/code map
        countriesList = []
        for aCountry in proxyCountries:
            countryCode = [key for key, val in countryCodes.items() if val == aCountry]
            # logger.debug(f"countryCode: {countryCode}")
            try:
                countriesList.append(countryCode[0])
            except IndexError:
                logger.warning("Reading through deploymentSettings YAML file")
                logger.warning(f"Specified country '{aCountry}' doesn't have a corresponding code")
                logger.warning("Check country/code mapping file")
                exit(1)

        for aCountry in countriesList:
            # VPN task definition for ECS
            taskDefinition = ecs.Ec2TaskDefinition(
                self,
                f"useVpnTask{aCountry}",
                network_mode=ecs.NetworkMode.AWS_VPC,
                execution_role=taskExecutionRole,
                task_role=taskExecutionRole,
                family=self.stackName
            )
            contObj = self._createProtonContainers(taskDefinition, aCountry)

            # Step 2: create a cloudmap service
            # Step 3: link the service to the ECS service
            ecsSecurityGroup.add_ingress_rule(
                ec2.Peer.any_ipv4(),
                ec2.Port.tcp(int(thePort))
            )

            vpnService = ecs.Ec2Service(
                self, f"vpnService{aCountry}",
                cluster=ecsCluster,
                task_definition=taskDefinition,
                cloud_map_options=ecs.CloudMapOptions(
                    cloud_map_namespace=oneNamespace,
                    name=aCountry.lower(),  # e.g. ru.hpatrol.dom
                    container=contObj,
                    container_port=int(thePort),
                    dns_record_type=aws_servicediscovery.DnsRecordType.A,
                    dns_ttl=Duration.seconds(10)
                ),
                security_groups=[ecsSecurityGroup]
            )


    def _createSecrets(self) -> None:
        secretObjValue = {}
        # Note: the secrets file is loaded on initCdkSettings()
        try:
            for key, value in cdkConfig["SECRETS"].items():
                secretObjValue[key] = SecretValue.unsafe_plain_text(str(value))
        except KeyError:
            logger.warning("*****************WARNING******************")
            logger.warning("* cdkConfig secrets parameters not there *")
            logger.warning("******************************************")
            logger.warning("If this is an attempt to deploy VPN stack")
            logger.warning("make sure the file is available. If not")
            logger.warning("you can ignore this warning.")
            logger.warning("******************************************")
            raise

        secretName = f"{config['protonVpnSecretsName']}"
        secrets.Secret(self, 
            id=secretName,
            description=f"ProtonVPN for {self.baseStackName}",
            secret_name=secretName,
            secret_object_value=secretObjValue
        )

        logger.debug("Secrets!")


    def _createEcsCluster(self, ecsSecurityGroup, cdkConfig):
        logger.info("Building the ECS cluster")
        if executionMode == SystemMode.PROD:
            # Note that this is the PROD section
            # Using min_capacity=1 for now since we're only using one offRamp (RU)
            # On a C5.XLARGE, we can only have 2 tasks right now (RU and other)
            # When more are needed, make sure to increase min_capacity accordingly
            autoScalingGroup = autoscaling.AutoScalingGroup(
                self,
                "anASgroup",
                vpc=self.stackVpc,
                instance_type=ec2.InstanceType.of(ec2.InstanceClass.C5, ec2.InstanceSize.XLARGE),
                machine_image=ecs.EcsOptimizedImage.amazon_linux2023(),
                min_capacity=6,
                max_capacity=10,
                block_devices=[
                    autoscaling.BlockDevice(
                        device_name="/dev/sda1",
                        volume=autoscaling.BlockDeviceVolume.ebs(
                            50,
                            delete_on_termination=True,
                            volume_type=autoscaling.EbsDeviceVolumeType.GP3
                        )
                    )
                ],
                vpc_subnets=self.ecsVpcSubnet,
                security_group=ecsSecurityGroup
            )

        else:
            # On NON-PROD, set up a backdoor to access the EC2
            autoScalingGroup = autoscaling.AutoScalingGroup(
                self,
                "anASgroup",
                vpc=self.stackVpc,
                instance_type=ec2.InstanceType.of(ec2.InstanceClass.C5, ec2.InstanceSize.XLARGE),
                machine_image=ecs.EcsOptimizedImage.amazon_linux2023(),
                min_capacity=6,
                max_capacity=10,
                block_devices=[
                    autoscaling.BlockDevice(
                        device_name="/dev/sda1",
                        volume=autoscaling.BlockDeviceVolume.ebs(
                            50,
                            delete_on_termination=True,
                            volume_type=autoscaling.EbsDeviceVolumeType.GP3
                        )
                    )
                ],
                vpc_subnets=self.ecsVpcSubnet,
                security_group=ecsSecurityGroup,
                key_name=cdkConfig["EC2_KEYPAIR"]
            )

        capacityProvider = ecs.AsgCapacityProvider(
            self,
            config["asgCapacityProviderName"],
            capacity_provider_name=config["asgCapacityProviderName"],
            auto_scaling_group=autoScalingGroup
        )

        ecsCluster = ecs.Cluster(
            self,
            f"{self.baseStackName}-ecs-cluster",
            cluster_name=config["ecsClusterName"],
            vpc=self.stackVpc
        )

        ecsCluster.add_asg_capacity_provider(capacityProvider)

        return ecsCluster


    def _createProtonContainers(self, taskDefinition, aCountry):
        thePort = cdkConfig["PROXY_LISTEN_PORT"]

        # Verify we have the certificate file locally
        cwd = pathlib.Path.cwd()
        checkFile = cwd / f"stacks/protonVpnProxy/{proxyCaFile}"
        # Just checking...
        try:
            youThere = checkFile.resolve(strict=True)
        except FileNotFoundError as err:
            logger.error(f"File not found: {err.filename}")
            logger.error(f"The latest copy of '{proxyCaFile}' should be")
            logger.error(f"kept in the resources/ folder of the PROD bucket")
            logger.error(f"Make sure to copy it from there")
            logger.error(f"as this file is shared by both the")
            logger.error(f"proxy and the executing lambda")
            missing = os.path.basename(err.filename)
            raise FileNotFoundError(missing) from None

        containerLinuxParams = ecs.LinuxParameters(
            self, id=f"protonVpn-linux-params-{aCountry}"
        )

        containerLinuxParams.add_capabilities(
            ecs.Capability.NET_ADMIN, # Docker capability for protonVPN
            ecs.Capability.NET_RAW # Docker capability for protonVPN
        )
        containerLinuxParams.add_devices(
            ecs.Device(host_path="/dev/net/tun") # Special device for VPN clients in Linux
        )

        protonImageAsset = ecr_assets.DockerImageAsset(
            self, id=f"ProtonVpn-proxy-Docker-Asset{aCountry}",
            directory=f"{self.cwd}/stacks",
            build_args={
                "CONTAINER_PORT": thePort,
                "ENV_FOR_DYNACONF": executionMode.name
            },
            file="protonVpnProxy/protonDockerfile"
        )

        protonContainerImage = ecs.ContainerImage.from_docker_image_asset(
            protonImageAsset
        )

        return self._createAContainer(aCountry, taskDefinition, protonContainerImage, containerLinuxParams)


    def _createAContainer(self, country, taskDef, theImage, linuxParams) -> ecs.ContainerDefinition:
        thePort = cdkConfig["PROXY_LISTEN_PORT"]
        protonVpnProxyContainer = taskDef.add_container(
            f"{self.baseStackName}-protonVpnContainer{country}",
            container_name=f"{config['protonVpnProxyContainerName']}{country}",
            image=theImage,
            command=f"./bootstrapProtonVpnProxy.sh -secret {config['protonVpnSecretsName']} -country {country} -go yes".split(),
            environment={
                "AWS_REGION": self.region,
                "PROXY_LISTEN_PORT": thePort,
                "ENV_FOR_DYNACONF": executionMode.name
            },
            linux_parameters=linuxParams,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix=f"{self.baseStackName}-protonVpn-proxy",
                log_group=self.stackLogGroup,
                mode=ecs.AwsLogDriverMode.NON_BLOCKING
            ),
            privileged=True,
            memory_limit_mib=3072,
            cpu=2048,
            essential=True,
            port_mappings=[ecs.PortMapping(
                    container_port=int(thePort)
            )]
        )

        return protonVpnProxyContainer


def _createEcsRoles(baseStackName, cdkConfig, stackObjRef) -> Role:
    ecsRole = Role(stackObjRef,
        id=f"{baseStackName}-executionRole",
        role_name=f"{baseStackName}-ecs-execution-role",
        assumed_by=iam.CompositePrincipal(
            ServicePrincipal("ecs.amazonaws.com"),
            ServicePrincipal("ecs-tasks.amazonaws.com")
        ),
        permissions_boundary=ManagedPolicy.from_managed_policy_name(
            stackObjRef,
            f"{baseStackName}-Task-Permissions-Boundary",
            cdkConfig["PERMISSIONS_BOUNDARY"]
            ),
        managed_policies=[
            ManagedPolicy.from_aws_managed_policy_name("AmazonS3FullAccess"),
            ManagedPolicy.from_aws_managed_policy_name("AmazonEC2FullAccess"),
            ManagedPolicy.from_aws_managed_policy_name("AmazonSQSFullAccess"),
            ManagedPolicy.from_aws_managed_policy_name("AmazonECS_FullAccess"),
            ManagedPolicy.from_aws_managed_policy_name("CloudWatchFullAccess"),
            ManagedPolicy.from_aws_managed_policy_name("SecretsManagerReadWrite"),
            ManagedPolicy.from_aws_managed_policy_name("AWSCloudFormationReadOnlyAccess"),
            ManagedPolicy.from_aws_managed_policy_name("EC2InstanceProfileForImageBuilderECRContainerBuilds"),
            ManagedPolicy.from_managed_policy_name(
                stackObjRef, 
                f"{baseStackName}-Key-Access-Policy",
                managed_policy_name="KMS_Key_User")
        ]
    )
    logger.info("ECS roles defined")

    return ecsRole


class HPatrolVpnLambdas(Stack):
    cwd = str(pathlib.Path.cwd())

    def __init__(self,
        profile: str,
        stackName: str,
        description: str,
        baseStackName: str,
        scope: Construct,
        **kwargs) -> None:
        super().__init__(scope, stackName, description=description, **kwargs)

        csf.loggerSetup()
        self.stackName = stackName
        self.baseStackName = baseStackName
        # self.region already set by super()
        # self.account already set by super()

        # Report out which configuration environment we are using (dev/test/prod) 
        logger.debug(f"Using configuration:  {executionMode.name}")

        # Apply the BoundaryPolicy to the entire stack
        boundaryPolicy = ManagedPolicy.from_managed_policy_name(
            self, 
            id="permissions_boundary",
            managed_policy_name=cdkConfig["PERMISSIONS_BOUNDARY"]
        )
        PermissionsBoundary.of(self).apply(boundaryPolicy)

        # Create a CloudWatch log group for the stack
        self.stackLogGroup = logs.LogGroup(
            self, id=f"{baseStackName}-Stack-Log-Group",
            retention=logs.RetentionDays.ONE_WEEK
        )

        # Derive the VPC to be used
        self.stackVpc = ec2.Vpc.from_lookup(self,
            id=f"{baseStackName}-VPC",
            vpc_name=cdkConfig["VPC_NAME"],
            is_default=False
        )

        # Subnet for the VPC
        self.ecsVpcSubnet = ec2.SubnetSelection(
            subnet_filters=[                    
                ec2.SubnetFilter().by_ids([cdkConfig["ECS_SUBNET"]])
            ]
        )

        # The executing lambda needs to be inside the VPC
        self._lambdaRole = csf.createLambdaRoles(self, cdkConfig)
        self._createLambdas()


    def _createLambdas(self) -> None:
        # Construct the Lambdas
        self._createLambdasStructure()

        # Import/create the Lambda Layer for dependencies
        ytdlpLayer  = csf.createYtdlDependencyLayer(self)
        reqsFile = f"{self.cwd}/stacks/systemResources/lambdaRequirements.txt"
        self._dependsLayer = csf.createDependenciesLayerBin(self, reqsFile)
        ffprobeLayer = csf.addFfprobeLayer(self, self.account, self.region)        
        pycurlLayer = csf.addPycurlLayer(self)
        logger.info("Lambda layers created")

        # Notice both lambdas are created from the same collector code
        self._createStillsLambda(pycurlLayer)
        self._createVideosLambda(ffprobeLayer, ytdlpLayer, pycurlLayer)


    # Lambdas in a VPC have an Elastic Network Interface (ENI) attached, and deletion (cdk destroy) takes a lot more time
    #   https://stackoverflow.com/questions/47957820/lambda-in-vpc-deletion-takes-more-time
    # Can speed up the process somewhat manually deleting the ENI in the console
    # ENIs are visible in the EC2 console's left hand navigation pane under Network Interfaces
    def _createStillsLambda(self, pycurlLayer) -> None:
        theLambda = PythonFunction(self, "stillsLambda",
            description="Collects stills data through VPN",
            entry=self.buildDirs["collectorBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_StillsVPN",
            memory_size=256,
            ephemeral_storage_size=Size.mebibytes(512),
            # Using "unreserved account concurrency"
            timeout=Duration.minutes(15),
            retry_attempts=0,
            log_retention=RetentionDays.THREE_MONTHS,
            vpc=self.stackVpc,
            vpc_subnets=self.ecsVpcSubnet,
            layers=[
                self._dependsLayer,
                pycurlLayer
            ],
            environment={
                "stackName": self.stackName,
                "HPatrolStatusQueue": self.baseStackName + "_" + projectName + "Status"
            }
        )
        logger.info("Stills lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)


    def _createVideosLambda(self, ffprobeLayer, ytdlpLayer, pycurlLayer) -> None:
        theLambda = PythonFunction(self, "videosLambda",
            description="Collects video data through VPN",
            entry=self.buildDirs["collectorBuildDir"],
            index="src/python/main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            function_name=self.baseStackName + "_VideosVPN",
            memory_size=2048,
            ephemeral_storage_size=Size.mebibytes(10240),
            # Using "unreserved account concurrency"
            timeout=Duration.minutes(15),
            retry_attempts=0,
            log_retention=RetentionDays.THREE_MONTHS,
            vpc=self.stackVpc,
            vpc_subnets=self.ecsVpcSubnet,
            layers=[
                self._dependsLayer,
                ffprobeLayer,
                pycurlLayer,
                ytdlpLayer
            ],
            environment={
                "stackName": self.stackName,
                "HPatrolStatusQueue": self.baseStackName + "_" + projectName + "Status"
            }
        )
        logger.info("Videos lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)


    # This collates together the codes that will be uploaded to the lambdas
    # It's like this to save ourselves duplicate code management; reuse base code for different lambdas
    def _createLambdasStructure(self) -> None:
        commonSrcDir = f"{self.cwd}/stacks/common"
        collectorSrcDir = f"{self.cwd}/stacks/collector"

        logger.info("Constructing lambda build dirs")
        outputDir = f"{self.cwd}/.lambdaBuild/vpnCollector"

        # Clean out the output directory if already there
        outputPath = pathlib.Path(outputDir)
        if outputPath.exists():
            logger.info(f"Clearing output dir: {outputDir}")
            shutil.rmtree(outputPath)

        collectorDir = outputDir + "/vpnCollector"
        pathlib.Path(collectorDir).mkdir(parents=True, exist_ok=True)
        copy_tree(collectorSrcDir, collectorDir)
        copy_tree(commonSrcDir, collectorDir)

        logger.info("Lambda build dirs created")

        self.buildDirs = {
            "collectorBuildDir": collectorDir
        }


# Need to solve the hanging of CDK destroy because of the ASG
# Info here: https://github.com/aws/aws-cdk/issues/18179
# What follows is a sample CF code for this
# 
#   # Custom resource that force destroys the ASG. This cleans up EC2 instances that had
#   # managed termination protection enabled, but which are not yet released.
#   # This is necessary because ECS does not immediately release an EC2 instance from termination
#   # protection as soon as the instance is no longer running tasks. There is a cooldown delay.
#   # In the case of tearing down the CloudFormation stack, CloudFormation will delete the
#   # AWS::ECS::Service and immediately move on to tearing down the AWS::ECS::Cluster, disconnecting
#   # the AWS::AutoScaling::AutoScalingGroup from ECS management too fast, before ECS has a chance
#   # to asynchronously turn off managed instance protection on the EC2 instances.
#   # This will leave some EC2 instances stranded in a state where they are protected from scale-in forever.
#   # This then blocks the AWS::AutoScaling::AutoScalingGroup from cleaning itself up.
#   # The custom resource function force destroys the autoscaling group when tearing down the stack,
#   # avoiding the issue of protected EC2 instances that can never be cleaned up.
#   CustomAsgDestroyerFunction:
#     Type: AWS::Lambda::Function
#     Properties:
#       Code:
#         ZipFile: |
#           const { AutoScalingClient, DeleteAutoScalingGroupCommand } = require("@aws-sdk/client-auto-scaling");
#           const response = require('cfn-response');

#           exports.handler = async function(event, context) {
#             console.log(event);

#             if (event.RequestType !== "Delete") {
#               await response.send(event, context, response.SUCCESS);
#               return;
#             }

#             const autoscaling = new AutoScalingClient({ region: event.ResourceProperties.Region });

#             const input = {
#               AutoScalingGroupName: event.ResourceProperties.AutoScalingGroupName,
#               ForceDelete: true
#             };
#             const command = new DeleteAutoScalingGroupCommand(input);
#             const deleteResponse = await autoscaling.send(command);
#             console.log(deleteResponse);

#             await response.send(event, context, response.SUCCESS);
#           };
#       Handler: index.handler
#       Runtime: nodejs20.x
#       Timeout: 30
#       Role: !GetAtt CustomAsgDestroyerRole.Arn

#   # The role used by the ASG destroyer
#   CustomAsgDestroyerRole:
#     Type: AWS::IAM::Role
#     Properties:
#       AssumeRolePolicyDocument:
#         Version: 2012-10-17
#         Statement:
#           - Effect: Allow
#             Principal:
#               Service:
#                 - lambda.amazonaws.com
#             Action:
#               - sts:AssumeRole
#       ManagedPolicyArns:
#         # https://docs.aws.amazon.com/aws-managed-policy/latest/reference/AWSLambdaBasicExecutionRole.html
#         - arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
#       Policies:
#         - PolicyName: allow-to-delete-autoscaling-group
#           PolicyDocument:
#             Version: 2012-10-17
#             Statement:
#               - Effect: Allow
#                 Action: autoscaling:DeleteAutoScalingGroup
#                 Resource: !Sub arn:aws:autoscaling:${AWS::Region}:${AWS::AccountId}:autoScalingGroup:*:autoScalingGroupName/${ECSAutoScalingGroup}

#   CustomAsgDestroyer:
#     Type: Custom::AsgDestroyer
#     DependsOn:
#       - EC2Role
#     Properties:
#       ServiceToken: !GetAtt CustomAsgDestroyerFunction.Arn
#       Region: !Ref "AWS::Region"
#       AutoScalingGroupName: !Ref ECSAutoScalingGroup
