# External libraries import statements
import os
import time
import json
import boto3
import logging
import argparse
import threading
import datetime as dt
from random import sample


# This application's import statements
try:
    # These are for when running in an EC2
    import processInit
    import systemSettings
    import superGlblVars as GLOBALS
    from superGlblVars import config
    from orangeUtils import auditUtils
    from orangeUtils import utils as ut
    from utils import hPatrolUtils as hput
    from ec2_metadata import ec2_metadata as ec2
    from orangeUtils.auditUtils import AuditLogLevel

except ModuleNotFoundError as err:
    # These are for when running in a Lambda
    print(f"Loading module for lambda execution: {__name__}")
    from src.python import processInit
    from src.python import systemSettings
    from src.python.superGlblVars import config
    from src.python.orangeUtils import auditUtils
    from src.python.orangeUtils import utils as ut
    from src.python import superGlblVars as GLOBALS
    from src.python.utils import hPatrolUtils as hput
    from src.python.orangeUtils.auditUtils import AuditLogLevel


logger = logging.getLogger()


def lambdaHandler(event, context):
    upSince = processInit.preFlightSetup()
    processInit.initSessionObject(config['sessionHeaders'])
    if not processInit.initialize():
        logger.error("Failed to initialize")
        exit(1)

    # Capture our ARN for later use
    GLOBALS.myArn = context.invoked_function_arn

    # Grab input
    try:
        body = json.loads(event['Records'][0]['body'])
        test = body['collRegions']
    except KeyError as err:
            logger.error(f'Invalid message received: {err}')
            logger.debug(f"Message received is:{event}")
            return {"status": False}

    try:
        # Pre-set values in case execution is interrupted
        dataLevel = AuditLogLevel.INFO
        systemLevel = AuditLogLevel.INFO
        exitMessage = "Exit with errors"

        # Execute!
        trueOrFalse = execute(body)
        exitMessage = "Normal execution"

    except Exception as e:
        logger.exception(f"UNHANDLED EXCEPTION CAUGHT:::{e}")
        systemLevel = AuditLogLevel.CRITICAL
        dataLevel = None

    finally:
        nownow = int(time.time())
        logger.info(f'Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}')

        auditUtils.logFromLambda(
            event=event,
            msg=exitMessage,
            arn=GLOBALS.myArn,
            dataLevel=dataLevel,
            lambdaContext=context,
            ip=GLOBALS.perceivedIP,
            systemLevel=systemLevel,
            taskName=GLOBALS.taskName,
            stackName=GLOBALS.projectName,
            subtaskName=GLOBALS.subtaskName,
            enterDatetime=dt.datetime.fromtimestamp(upSince),
            leaveDatetime=dt.datetime.fromtimestamp(nownow),
            # **collectionSummaryArgs
            # collectionSummaryArgs1="some",
            # collectionSummaryArgs2="additional",
            # collectionSummaryArgs3="info"
            )

    toPrint = "Exiting Process"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")

    return {"status": trueOrFalse}


def execute(targetConfig):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Dispatcher"

    # Extract our account ID and region from our ARN
    accntId = GLOBALS.myArn.split(":")[4]
    # Notice that ourRegion may be different than the target region
    # This line is not used; just kept here for info
    # ourRegion = GLOBALS.myArn.split(":")[3]

    # To VPN or not pn...that is the question
    proxyStr = ""
    if targetConfig.get("vpn"):
        proxyStr = "VPN"
    elif targetConfig.get("proxy"):
        # Determine which proxy to use
        # Right now only includes whirl, but more may be added
        if "whirl" in targetConfig["proxy"]:
            proxyStr = "VPC"

    # Compose the name of the function to call
    if(
        targetConfig["collectionType"] == "IMAGEINJSON" or 
        targetConfig["collectionType"] == "STILLS" or 
        targetConfig["collectionType"] == "FSTLLS" or 
        targetConfig["collectionType"] == "ISTLLS"
        ):
        funcToCall = f"{GLOBALS.baseStackName}_Stills{proxyStr}"
    elif(
        targetConfig["collectionType"] == "PLAYWRIGHT"
        ):
        funcToCall = f"{GLOBALS.baseStackName}_Playwright"
    else:
        funcToCall = f"{GLOBALS.baseStackName}_Videos{proxyStr}"

    # Randomly select just one of any stated Collectors in the region for this aimpoint
    # It was confirmed that if the order is sent to 2 regions we don't get duplicate
    # data but they both attempt the same at the same time; we just don't want that
    aRegion = sample(targetConfig["collRegions"], 1)[0]
    aRegion = ut.getRegionCode(aRegion)

    # Create the ARN for the Collector lambda
    collectorArn = 'arn:aws:lambda:' + aRegion + ':' + accntId + ':function:' + funcToCall

    # Create a lambda client
    logger.info(f"Creating boto3 lambda client on '{aRegion}'")
    awsLambda = boto3.client(service_name='lambda', region_name=aRegion)

    logger.info(f"Invoking lambda '{collectorArn}' "
        f"for '{hput.formatNameBase(targetConfig['filenameBase'], targetConfig['deviceID'])}'"
    )
    # logger.debug(f"Payload:{targetConfig}")

    try:
        resp = awsLambda.invoke(FunctionName=collectorArn,
                                InvocationType='Event',
                                Payload=json.dumps(targetConfig))
    except Exception as e:
        logger.critical(f'Caught Exception attempting to invoke lambda ::{e}')
        return False

    if 200 <= resp['ResponseMetadata']['HTTPStatusCode'] < 300:
        # logger.debug(f"Invoke response: {resp}")
        pass
    else:
        logger.warning(f"Invocation failed: {resp}")
        return False

    return True


if __name__ == '__main__':
    # Obtain test file name, if given
    parser = argparse.ArgumentParser(prog="Dispatcher", 
                                     description="Test the Dispatcher on an EC2 instance",
                                     formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("-f",
                        help=(
                            "Aimpoint file for testing\n"\
                            "default: testResources/aimpoint-m3u8.json"
                        ),
                        dest="testFile",
                        default="testResources/aimpoint-m3u8.json")

    args = parser.parse_args()
    testFile = args.testFile

    upSince = processInit.preFlightSetup()
    processInit.initSessionObject(config['sessionHeaders'])
    if not processInit.initialize():
        logger.error("Failed to initialize")
        exit(1)

    # Don't use proxy for AWS metadata; will timeout if proxy is tried
    # This is the equivalent of doing: $ export no_proxy=169.254.169.254
    try:
        os.environ["no_proxy"] = f"{os.environ['no_proxy']},169.254.169.254"
    except KeyError:
        os.environ["no_proxy"] = "169.254.169.254"

    # Create our ARN for later use
    region = ec2.region
    accountId = ec2.account_id
    instanceId = ec2.instance_id
    arn = f'arn:aws:ec2:{region}:{accountId}:instance/{instanceId}'
    GLOBALS.myArn = arn

    logger.debug(f"Using testFile '{testFile}'")
    with open(testFile, "r") as f:
        aimpoint = json.loads(f.read())
    trueOrFalse = execute(aimpoint)

    nownow = int(time.time())
    logger.info(f'Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}')

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
