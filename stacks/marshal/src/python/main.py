"""
Module to manage the rounding up of a bunch of files, zip and upload them.
Code will look into an S3 bucket, get a listing of all the files, organize them
into each device (deviceID) then put the file list of each device into a queue.

This can be run as a stand-alone python script to test.
When run as stand-alone script, note that certain plumbing must be in place.
"""


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
    import superGlblVars as GLOBALS
    from superGlblVars import config
    from orangeUtils import auditUtils
    from utils import hPatrolUtils as hput
    from orangeUtils import timeUtils as tu
    from ec2_metadata import ec2_metadata as ec2
    from orangeUtils.auditUtils import AuditLogLevel

except ModuleNotFoundError as err:
    # These are for when running in a Lambda
    print(f"Loading module for lambda execution: {__name__}")
    from src.python import processInit
    from src.python.superGlblVars import config
    from src.python import systemSettings
    from src.python.orangeUtils import auditUtils
    from src.python import superGlblVars as GLOBALS
    from src.python.utils import hPatrolUtils as hput
    from src.python.orangeUtils import timeUtils as tu
    from src.python.orangeUtils.auditUtils import AuditLogLevel


logger = logging.getLogger()


def _checkValues(data):
    # Check and/or set default values
    data["wrkBucket"] = hput.pickBestBucket(data, "wrkBucket")
    data["dstBucket"] = hput.pickBestBucket(data, "dstBucket")

    try:
        checkValue = data["deliveryKey"]
        if not checkValue:
            checkValue = GLOBALS.deliveryKey
        else:
            logger.info(f"Using aimpoint-specified deliveryKey '{checkValue}'")
    except KeyError:
        checkValue = GLOBALS.deliveryKey
    data["deliveryKey"] = checkValue

    return data


def execute():
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Marshal"

    # Note that it is here where we determine which files to go after
    # By default, we will be picking up the files from "yesterday"
    now = time.time()
    dayToWorkOn = now - 24*60*60
    if not GLOBALS.onProd:
        logger.debug("NOT on prod; using 'now' timestamp")
        dayToWorkOn = now
        # year, month, day = ('2022', '08','16')

    year, month, day = tu.returnYMD(dayToWorkOn)

    stillsAimpoints = []
    mstllsAimpoints = []
    # First, select all stills aimpoints out of all available
    if GLOBALS.useTestData:
        # List all files in testResources including in subdirectories
        allFiles = [os.path.join(root, name) for root, dirs, files in os.walk("testResources/") for name in files]
        for aFile in allFiles:
            try:
                aJson = open(aFile, "r").read()
                aimpointData = json.loads(aJson)
            except:
                # Not JSON files
                continue
            try:
                if(aimpointData["collectionType"] == "STILLS"):
                    stillsAimpoints.append(aimpointData)
                elif(aimpointData["collectionType"] == "FSTLLS"):
                    mstllsAimpoints.append(aimpointData)
                elif(aimpointData["collectionType"] == "ISTLLS"):
                    stillsAimpoints.append(aimpointData)
                elif(aimpointData["collectionType"] == "IMAGEINJSON"):
                    stillsAimpoints.append(aimpointData)
            except:
                # Not aimpoints
                continue
    else:
        allFiles = GLOBALS.S3utils.getFilesAsStrList(config['defaultWrkBucket'], GLOBALS.targetFiles)
        for idx, aimpoint in enumerate(allFiles, start=1):
            # Don't go through everything if we're not on PROD
            if not GLOBALS.onProd and idx == 5:
                logger.debug(f"Not running on PROD; exiting before processing file #{idx}")
                break

            logger.info(f"Processing file '{aimpoint}'")
            try:
                contents = GLOBALS.S3utils.readFileContent(config["defaultWrkBucket"], aimpoint)
                aimpointData = json.loads(contents)
            except Exception as e:
                logger.warning(f"Error processing input file; skipping:::{e}")
                continue

            try:
                if not aimpointData["enabled"]:
                    logger.info("Aimpoint disabled; skipping")
                    continue
            except KeyError:
                pass

            if(aimpointData["collectionType"] == "STILLS"):
                stillsAimpoints.append(aimpointData)

            if(aimpointData["collectionType"] == "FSTLLS"):
                mstllsAimpoints.append(aimpointData)

            if(aimpointData["collectionType"] == "ISTLLS"):
                stillsAimpoints.append(aimpointData)

            if(aimpointData["collectionType"] == "IMAGEINJSON"):
                stillsAimpoints.append(aimpointData)

    logger.info(f"Stills aimpoints to work on: {len(stillsAimpoints)}")
    for aimpoint in stillsAimpoints:
        aimpoint = _checkValues(aimpoint)
        deviceID = aimpoint["deviceID"]

        # As default, the system uses the yr/mnth/day/filenameBase/ construct for the stills working area
        fnBase = hput.formatNameBase(aimpoint["filenameBase"], deviceID)
        logger.info(f"Sending: {fnBase}")
        filenameBase = f"{fnBase}.zip"

        defaultLz = "{year}/{month}/{day}/{fnBase}".format(year=year, month=month, day=day, fnBase=fnBase)
        bucketPrefix = aimpoint["bucketPrefixTemplate"].format(year=year, month=month, day=day, deviceID=deviceID)
        zipFileName = hput.formatNameSuffix(filenameBase, aimpoint["finalFileSuffix"], dayToWorkOn)

        # Handle single-string input in the deliveryKey field
        if type(aimpoint["deliveryKey"]) is str:
            aimpoint["deliveryKey"] = aimpoint["deliveryKey"].split()

        theMsg = {
            "bagAndZip": {
                "selected": fnBase,
                "zipFileName": zipFileName,
                "bucketPrefix" : bucketPrefix,
                "wrkBucket": aimpoint["wrkBucket"],
                "dstBucket": aimpoint["dstBucket"],
                "deliveryKey": aimpoint["deliveryKey"],
                "filesLocation": f"{GLOBALS.stillImages}/{defaultLz}"
                }
        }
        logger.debug(f"Message: {json.dumps(theMsg)}")
        resp = GLOBALS.sqsUtils.sendMessage(config['bagQueue'], theMsg)
        # logger.debug(f"SQS response: {resp}")

    logger.info(f"Multi-stills aimpoints to work on: {len(mstllsAimpoints)}")
    for aimpoint in mstllsAimpoints:
        aimpoint = _checkValues(aimpoint)
        stationId = aimpoint['deviceID']
        idList  = aimpoint["deviceIdList"]
        urlList = aimpoint['accessUrlList']
        fnbList = aimpoint['filenameBaseList']
        for id, url, fNamBas in zip(idList, urlList, fnbList):
            deviceID = id
            aimpoint['deviceID'] = id
            aimpoint['accessUrl'] = url
            aimpoint['filenameBase'] = fNamBas

            # As default, the system uses the yr/mnth/day/filenameBase/ construct for the stills working area
            fnBase = hput.formatNameBase(aimpoint["filenameBase"], deviceID)
            logger.info(f"Sending: {fnBase}")
            filenameBase = f"{fnBase}.zip"

            defaultLz = "{year}/{month}/{day}/{fnBase}".format(year=year, month=month, day=day, fnBase=fnBase)
            bucketPrefix = aimpoint["bucketPrefixTemplate"].format(year=year, month=month, day=day, deviceID=deviceID)
            zipFileName = hput.formatNameSuffix(filenameBase, aimpoint["finalFileSuffix"], dayToWorkOn)

            # Handle single-string input in the deliveryKey field
            if type(aimpoint["deliveryKey"]) is str:
                aimpoint["deliveryKey"] = aimpoint["deliveryKey"].split()

            theMsg = {
                "bagAndZip": {
                    "selected": fnBase,
                    "zipFileName": zipFileName,
                    "bucketPrefix" : bucketPrefix,
                    "wrkBucket": aimpoint["wrkBucket"],
                    "dstBucket": aimpoint["dstBucket"],
                    "deliveryKey": aimpoint["deliveryKey"],
                    "filesLocation": f"{GLOBALS.stillImages}/{defaultLz}"
                    }
            }
            logger.debug(f"Message: {json.dumps(theMsg)}")
            resp = GLOBALS.sqsUtils.sendMessage(config['bagQueue'], theMsg)
            # logger.debug(f"SQS response: {resp}")

        aimpoint['accessUrl'] = None
        aimpoint['filenameBase'] = None
        aimpoint['deviceID'] = stationId

    return True


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
        trueOrFalse = execute()
        exitMessage = "Normal execution"
        if not trueOrFalse:
            dataLevel = AuditLogLevel.WARN

    except Exception as e:
        logger.exception(f"UNHANDLED EXCEPTION CAUGHT:::{e}")
        systemLevel = AuditLogLevel.CRITICAL
        trueOrFalse = False
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

    execute()
    nownow = int(time.time())
    logger.info(f'Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}')

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
