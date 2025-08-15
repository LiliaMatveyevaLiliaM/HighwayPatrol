# External libraries import statements
import os
import time
import json
import logging
import threading
import datetime as dt


# This application's import statements
try:
    # These are for when running in an EC2
    import processInit
    import systemSettings
    from exceptions import *
    import superGlblVars as GLOBALS
    from superGlblVars import config
    from orangeUtils import auditUtils
    from orangeUtils import timeUtils as tu
    from ec2_metadata import ec2_metadata as ec2
    from orangeUtils.auditUtils import AuditLogLevel

except ModuleNotFoundError as err:
    # These are for when running in a Lambda
    print(f"Loading module for lambda execution: {__name__}")
    from src.python import processInit
    from src.python.exceptions import *
    from src.python import systemSettings
    from src.python.superGlblVars import config
    from src.python.orangeUtils import auditUtils
    from src.python import superGlblVars as GLOBALS
    from src.python.orangeUtils import timeUtils as tu
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

    exitMessage = "Exit with errors: one or more messages was not processed successfully"
    encounteredError = False

    for record in event["Records"]:
        try:
            payload = json.loads(record["body"])
            aimpoint: dict = payload["aimpoint"]
            isCollecting: bool = payload["isCollecting"]
            timestamp: str = record["attributes"]["SentTimestamp"]
        except KeyError as err:
            logger.error(f"Invalid message received: {err}")
            logger.debug(f"Message received is:{event}")
            continue

        try:
            # Pre-set values in case execution is interrupted
            dataLevel = AuditLogLevel.INFO
            systemLevel = AuditLogLevel.INFO

            trueOrFalse = execute(aimpoint, isCollecting, timestamp)
            if not trueOrFalse:
                encounteredError = True

        except Exception as e:
            logger.exception(f"UNHANDLED EXCEPTION CAUGHT:::{e}")
            systemLevel = AuditLogLevel.CRITICAL
            dataLevel = None
            continue

    if not encounteredError:
        exitMessage = "Normal execution"
        trueOrFalse = True
    else:
        trueOrFalse = False
    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    # Don't want to clog up the logs with thousands of "message" events, so
    # just logging the number of events processed per cycle
    eventsCount = len(event["Records"])
    msgsCount = {"messagesProcessed": eventsCount}
    auditUtils.logFromLambda(
        event=msgsCount,
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
        leaveDatetime=dt.datetime.fromtimestamp(nownow)
        )

    toPrint = "Exiting Process"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")

    return {"status": trueOrFalse}


def execute(aimpoint: dict, isCollecting: bool, timestamp: str) -> bool:
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Historian"

    try:
        fileKey = _formatFileKey(aimpoint, isCollecting, timestamp)

        # logger.info(f"Recording collection result for {fileKey}")
        if not GLOBALS.S3utils.createEmptyKey(config["defaultWrkBucket"], fileKey):
            return False

    except HPatrolError as err:
        logger.error(f"Unexpected error in historian: {err}")
        return False

    return True


def _formatFileKey(aimpoint: dict, isCollecting: bool, timestamp: str) -> str:
    deviceID = aimpoint["deviceID"]
    epochSeconds = float(timestamp) / 1000
    epoch = int(epochSeconds)
    year, month, day, hour, mins, secs = tu.returnYMDHMS(epoch)
    filenameBase = aimpoint["filenameBase"].format(deviceID=deviceID)
    result = "success" if isCollecting else "failure"
    filename = f"{year}{month}{day}{hour}{mins}{secs}_{epoch}_{result}"
    fileKey = f"{GLOBALS.aimpointSts}/{filenameBase}/{filename}"
    return fileKey


if __name__ == '__main__':
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

    with open("testResources/aimpoint-m3u8.json", "r") as ap:
        aimpoint = json.loads(ap.read())

    # Successful collection of aimpoint
    # trueOrFalse = execute(aimpoint, True, str(time.time()))

    # Failed collection of monitored aimpoint
    trueOrFalse = execute(aimpoint, False, str(time.time()*1000))
    logger.info(f"Execution success: {trueOrFalse}")
    nownow = int(time.time())
    logger.info(f'Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}')

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
