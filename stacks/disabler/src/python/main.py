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
    from orangeUtils import utils as ut
    from utils import hPatrolUtils as hput
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
    from src.python.orangeUtils import utils as ut
    from src.python import superGlblVars as GLOBALS
    from src.python.utils import hPatrolUtils as hput
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

    try:
        # Pre-set values in case execution is interrupted
        dataLevel = AuditLogLevel.INFO
        systemLevel = AuditLogLevel.INFO
        exitMessage = "Exit with errors"

        # Execute!
        trueOrFalse = True
        aimpointsProcessed = execute(upSince)
        exitMessage = "Normal execution"

    except Exception as e:
        logger.exception(f"UNHANDLED EXCEPTION CAUGHT:::{e}")
        systemLevel = AuditLogLevel.CRITICAL
        dataLevel = None
        trueOrFalse = False
        aimpointsProcessed = None

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
            aimpointsProcessed=aimpointsProcessed
            )

    toPrint = "Exiting Process"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")

    return {"status": trueOrFalse}


def execute(timestamp: int):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Disabler"

    # Select aimpoints that are on collection
    s3Dir = GLOBALS.targetFiles
    logger.info(f"Looking for files in S3: '{s3Dir}/'")
    fileList = GLOBALS.S3utils.getFilesAsStrList(config['defaultWrkBucket'], s3Dir)

    try:
        logger.info(f"Total aimpoints found:{len(fileList)}")
    except TypeError:
        return 0

    for idx, aFile in enumerate(fileList, start=1):
        if not GLOBALS.onProd and idx == 2:
            logger.debug(f"Not running on PROD; exiting before processing file #{idx}")
            break

        logger.info(f"Processing file '{aFile}'")
        contents = GLOBALS.S3utils.readFileContent(config["defaultWrkBucket"], aFile)
        try:
            targetConfig = json.loads(contents)
        except Exception as e:
            logger.warning(f"Error reading content of file {aFile}; skipping:::{e}")
            continue

        try:
            if not targetConfig["enabled"]:
                logger.info("Aimpoint disabled; skipping")
                continue
        except KeyError:
            pass

        if _shouldDisable(targetConfig, timestamp):
            logger.info(f"Aimpoint {aFile} failed to collect for 30 minutes, switching to monitor status")
            try:
                _disableAimpoint(aFile)
            except HPatrolError as e:
                logger.error(f"Unexpected error copying aimpoint {aFile} from active to monitored:::{e}")
                continue

            # Compose the selection file's filename
            domainName = os.path.basename(os.path.dirname(aFile)).removesuffix("-autoParsed")
            selectedFileName = f"selected-{domainName}.json"
            try:
                _disableSelectedDevices(selectedFileName)
            except HPatrolError:
                pass

    return len(fileList)


def _disableAimpoint(aimpointKey: str):
    monitoredKey = aimpointKey.replace(GLOBALS.targetFiles, GLOBALS.monitorTrgt)
    if not GLOBALS.S3utils.moveFileToDifferentKey(config["defaultWrkBucket"], aimpointKey, monitoredKey):
        raise HPatrolError(f"Failed to move {aimpointKey} to {monitoredKey}")
    if not GLOBALS.S3utils.isFileInS3(config["defaultWrkBucket"], monitoredKey):
        raise HPatrolError(f"File {monitoredKey} not found in bucket {config['defaultWrkBucket']}")


def _disableSelectedDevices(selectionsFile: str):
    selectedFilesKey = f"{GLOBALS.selectTrgts}/{selectionsFile}"
    selectedFileExists: bool = GLOBALS.S3utils.isFileInS3(config["defaultWrkBucket"], selectedFilesKey)
    if not selectedFileExists:
        return

    logger.info(f"File found in selections directory, updating {selectionsFile}")
    selectionsStr = GLOBALS.S3utils.readFileContent(config["defaultWrkBucket"], selectedFilesKey)
    try:
        selectionsDict = json.loads(selectionsStr)
    except json.decoder.JSONDecodeError as err:
        logger.error(f"Unable to parse response: {err}")
        logger.info("Check if file is zero-bytes")
        raise HPatrolError("Response text is not JSON")

    selections = selectionsDict["selections"]
    for device in selections:
        status = selections[device]
        if status == "on":
            status = "monitor"
        elif status == "mp4":
            status = "monitor-mp4"
        elif status == "decoy":
            status = "monitor-decoy"
    tmpFile = os.path.join(config["workDirectory"], selectionsFile)
    ut.writeJsonDataToFile(selectionsDict, tmpFile)
    logger.info(f"Pushing updated selections file {selectedFilesKey} to S3")
    pushedToS3 = GLOBALS.S3utils.pushToS3(
                    tmpFile, 
                    GLOBALS.selectTrgts, 
                    config["defaultWrkBucket"], 
                    deleteOrig=GLOBALS.onProd, 
                    s3BaseFileName=selectionsFile,
                    extras={"ContentType": "application/json"})
    if not pushedToS3:
        raise HPatrolError(f"Error pushing the file {selectedFilesKey} to S3")


def _shouldDisable(aimpoint: dict, timestamp: int) -> bool:
    # Set aimpoint to monitor
    # if the last disablerLookBack seconds of collection is all failures
    filenameBase = hput.formatNameBase(aimpoint["filenameBase"], aimpoint["deviceID"])
    filePrefix = f"{GLOBALS.aimpointSts}/{filenameBase}"
    lookBack = timestamp - GLOBALS.disablerLookBack
    year, month, day, hour, mins, secs = tu.returnYMDHMS(lookBack)
    startAfterPrefix = f"{filePrefix}/{year}{month}{day}{hour}{mins}{secs}"

    collectionResults = GLOBALS.S3utils.getFilesAsStrList(config["defaultWrkBucket"], filePrefix, startAfter=startAfterPrefix)
    if not collectionResults:
        logger.info(f"No collection results for {filePrefix} in the past {GLOBALS.disablerLookBack} seconds")
        return False
    allFailed = all("failure" in result for result in collectionResults)
    if allFailed:
        return True
    else:
        return False


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

    try:
        execute(upSince)
    except HPatrolError as err:
        logger.info(f"Caught exception: {err}")

    nownow = int(time.time())
    logger.info(f'Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}')

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
