"""
Module to round up of a bunch of files, zip, and upload them.

This can be run as a stand-alone python script to test.

Can use the following event for testing as a lambda:
{
  "Records": [
    {
	  "body": "{\"bagAndZip\": {\"selected\": \"xenon_SW103F\",\"zipFileName\": \"xenon_SW103F_2023_04_20.zip\",\"wrkBucket\": \"hpatrol-ch-test\",\"dstBucket\": \"xenon-ch-test\",\"bucketPrefix\": \"stills/2023/04/20/xenon_SW103F\",\"deliveryKey\": [\"data\", \"norData\"]}}"
    }
  ]
}
"""


# External libraries import statements
import os
import time
import uuid
import json
import zipfile
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
    from src.python.orangeUtils.auditUtils import AuditLogLevel


logger = logging.getLogger()


def _sendToBucket(bucketKeyPickup, localFile, s3fileName, deleteOrig, dstBucket):
    logger.info("Sending file to S3")

    fileNamePath = os.path.join(config['workDirectory'], localFile)

    try:
        if os.path.isfile(fileNamePath):
            result = GLOBALS.S3utils.pushToS3(fileNamePath,
                                                bucketKeyPickup,
                                                dstBucket,
                                                s3BaseFileName=s3fileName,
                                                deleteOrig=deleteOrig)
            if result:
                logger.info(f"Pushed {localFile} as {bucketKeyPickup}/{s3fileName}")
                # Success...exit function
                return
            else:
                logger.error(f"File {localFile} was not pushed to S3!")
        else:
            logger.warning(f"Unable to push {localFile}; file not found: {fileNamePath}")
    except:
        logger.warning(f"Unknown error trying to push {localFile}: {fileNamePath}")

    raise HPatrolError("S3 push error")


def _bagNZip(wrkBucket, fileList, wantedExt):
    logger.info("Getting files from S3 to zip")
    downloadedList = []
    wantedExt = wantedExt.upper()
    try:
        for f in fileList:
            # logger.debug(f)
            fileName = f.split(os.path.sep)[-1]
            # Filter the file list by the wanted extension
            if os.path.splitext(f)[1].upper() == wantedExt:
                # logger.debug(f"would be dowloading:{fileToGet}")
                GLOBALS.S3utils.getFileFromS3(wrkBucket, f, os.path.join(config['workDirectory'], fileName))
                downloadedList.append(fileName)
    except Exception as e:
        logger.exception(e)
        raise HPatrolError("Error getting files to zip")
    # logger.debug(f"downloadedList:{downloadedList}")

    # Compose a random filename as input to zip
    logger.info("Zipping up the files")
    zipFilename = str(uuid.uuid4()) + ".zip"
    try:
        with zipfile.ZipFile(os.path.join(config['workDirectory'], zipFilename), "w") as zipObj:
            for fName in downloadedList:
                zipObj.write(os.path.join(config['workDirectory'], fName), fName)
    except Exception as e:
        logger.exception(e)
        raise HPatrolError("Error zipping files")

    # Cleanup file from working area; important for when in lambda execution
    logger.info("Deleting downloaded files...")
    for f in downloadedList:
        os.remove(os.path.join(config['workDirectory'], f))

    return zipFilename


def execute(selection):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Minion"

    wrkBucket = selection["wrkBucket"]
    dstBucket = selection["dstBucket"]
    dstKey = selection["bucketPrefix"]
    srcKey = selection["filesLocation"]
    selectionID = selection["selected"]
    s3fileName = selection["zipFileName"]
    deliveryKeys = selection["deliveryKey"]

    # logger.info(f"Looking for files in {srcKey}")
    fileList = GLOBALS.S3utils.getFilesAsStrList(wrkBucket, srcKey)
    if not fileList:
        return False
    # logger.debug(f"fileList:{fileList}")

    # Process each selection in turn
    logger.info(f"Received order to process '{selectionID}' to bucket '{dstBucket}' for {deliveryKeys}")

    # In this minion we're only interested in .JPG files
    filterBy = ".JPG"
    for idx, aDeliveryKey in enumerate(deliveryKeys, start=1):
        # Get the data only once
        if idx == 1:
            try:
                zipName = _bagNZip(wrkBucket, fileList, filterBy)
            except HPatrolError:
                return False

        logger.info(f"Processing '{selectionID}' to '{aDeliveryKey}'")
        s3filePath = os.path.join(f"{aDeliveryKey}/{dstKey}", s3fileName)
        if GLOBALS.S3utils.isFileInS3(dstBucket, s3filePath):
            logger.warning(f"File already exists in S3: {s3filePath}; NOT over-writing it.")
            continue

        try:
            # Don't delete the zip file until we've gone through all deliveryKeys
            if idx == len(deliveryKeys):
                _sendToBucket(f"{aDeliveryKey}/{dstKey}", zipName, s3fileName, GLOBALS.onProd, dstBucket)
            else:
                _sendToBucket(f"{aDeliveryKey}/{dstKey}", zipName, s3fileName, False, dstBucket)

        except HPatrolError:
            # Ignore; error has been printed; keep trying the others
            pass

    return True


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
        test = body['bagAndZip']
    except KeyError as err:
            logger.error(f"Invalid message received:::{err}")
            logger.debug(f"Message received is: {event}")
            return {"status": False}

    try:
        # Pre-set values in case execution is interrupted
        dataLevel = AuditLogLevel.INFO
        systemLevel = AuditLogLevel.INFO
        exitMessage = "Exit with errors"

        # Execute!
        trueOrFalse = execute(body["bagAndZip"])
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

    if GLOBALS.useTestData:
        testFile = "testResources/minionQueueMessages.json"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(testFile, 'r') as f:
            queue = json.load(f)
        for msg in queue:
            execute(msg['bagAndZip'])
    else:
        logger.error("Not using test data; need to specify message queue data to execute")

    nownow = int(time.time())
    logger.info(f'Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}')

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
