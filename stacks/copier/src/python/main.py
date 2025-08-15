# External libraries import statements
import os
import time
import uuid
import zipfile
import logging
import threading
import datetime as dt
from pathlib import Path


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
    from src.python.orangeUtils.auditUtils import AuditLogLevel


logger = logging.getLogger()


def _sendToBucket(localFile, s3filePath):
    logger.info("Sending file to S3")
    # logger.debug(f"================>localFile='{localFile}'")
    # logger.debug(f"================>s3filePath='{s3filePath}'")

    s3Dir = Path(s3filePath).parent
    s3fileName = Path(s3filePath).name
    fileNamePath = os.path.join(config['workDirectory'], localFile)
    try:
        if os.path.isfile(fileNamePath):
            result = GLOBALS.S3utils.pushToS3(fileNamePath,
                                                s3Dir,
                                                config['defaultDstBucket'],
                                                s3BaseFileName=s3fileName,
                                                deleteOrig=GLOBALS.onProd)
            if result:
                logger.info(f"Pushed file {localFile} as {s3Dir}/{s3fileName}")
            else:
                logger.error(f"File {localFile} was not pushed to S3!")
                raise HPatrolError("Error pushing to S3")
        else:
            logger.warning(f"Unable to push {localFile}; file not found: {fileNamePath}")
            raise HPatrolError("Error pushing to S3")
    except Exception as err:
        logger.warning(f"Error trying to push {localFile}: {fileNamePath} ::{err}")
        raise HPatrolError("Error pushing to S3")


# OLDTODO: Make Copier aimpoint aware  (OBE: Copier removed)
#       The Copier has no access to the aimpoint settings so it can't pickup for ex. dstBucket
#       Could be fixed by queues...or just have it read the aimpoints and figure out what's what
def _bagNZip(m3u8File, fileList):
    logger.info("Getting files from S3 to zip")
    downloadedList = []

    # Get the M3U file
    fileName1 = Path(m3u8File).name
    filePath = os.path.join(config['workDirectory'], fileName1)
    try:
        logger.debug(f"Getting: {m3u8File}")
        if not GLOBALS.S3utils.getFileFromS3(config['defaultWrkBucket'], m3u8File, os.path.join(config['workDirectory'], filePath)):
            raise HPatrolError("Error accesing S3")
    except Exception as e:
        logger.exception(f"{e}")
        raise HPatrolError("Error accesing S3")
    downloadedList.append(fileName1)

    # Download all ts files
    for s3FileKey in fileList:
        aTsFile = Path(s3FileKey).name
        filePath = os.path.join(config['workDirectory'], aTsFile)
        try:
            logger.debug(f"Getting: {s3FileKey}")
            if not GLOBALS.S3utils.getFileFromS3(config['defaultWrkBucket'], s3FileKey, os.path.join(config['workDirectory'], filePath)):
                raise HPatrolError("Error accesing S3")
        except Exception as e:
            logger.exception(f"{e}")
            raise HPatrolError("Error accesing S3")
        downloadedList.append(aTsFile)
        # logger.debug(f"downloadedList:{downloadedList}")

    # Compose a random filename as input to zip
    logger.info("Zipping up the files")
    zipFilename = str(uuid.uuid4()) + ".zip"
    try:
        with zipfile.ZipFile(os.path.join(config['workDirectory'], zipFilename), "w") as zipObj:
            for fName in downloadedList:
                zipObj.write(os.path.join(config['workDirectory'], fName), fName)
    except Exception as e:
        logger.exception(f"{e}")
        raise HPatrolError("Error zipping files")

    # Cleanup downloaded files from working area; important for when in lambda execution
    logger.info("Deleting downloaded files...")
    for f in downloadedList:
        try:
            os.remove(os.path.join(config['workDirectory'], f))
        except FileNotFoundError:
            pass

    return zipFilename


def execute(event):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Copier"

    outputExtention = ".m3u8"

    try:
        # Notice we are changing GLOBALS.landingZone to GLOBALS.deliveryKey on destinations
        srcObjectKey = event["s3"]["object"]["key"]
        srcBucketName = event["s3"]["bucket"]["name"]
        p = Path(srcObjectKey)  # For filename replace later
    except KeyError:
        logger.exception("Exception caught")
        return False

    if not srcObjectKey.endswith(outputExtention):
        logger.warning(f"Strange; lambda woken up on a non-m3u8 file: '{srcObjectKey}'")
        return False

    logger.info(f"Reading file contents from '{srcBucketName}/{srcObjectKey}'")
    m3u8Str = GLOBALS.S3utils.readFileContent(srcBucketName, srcObjectKey)
    # logger.debug(f"\n{m3u8Str}")

    if not m3u8Str:
        logger.warning(f"Contents not found; exiting")
        return False
    # logger.debug(f"Contents:\n{m3u8Str}")

    logger.info(f"Extracting file list from playlist")
    tsFilesList = []
    m3u8List = m3u8Str.split('\n')
    for aLine in m3u8List:
        if aLine.endswith('.ts'):
            # Obtain the full s3 path of the ts file
            tsFilesList.append(srcObjectKey.replace(p.name, aLine))

    # OLDTODO: (OBE) Add logic to determine files handling for different scenarios
    #       At some point we should figure out how to choose between different final file states
    #       Right now, we're focusing on making a .zip but previously we were sending raw .ts
    #       Solving this requires some other form of lambda triggering than the current .m3u8 trigger
    #       Probably best to use a queueing mechanism
    if False:
        _straightMove(tsFilesList, srcBucketName)
        _uploadM3u(m3u8List, True)
    else:
        try:
            zipName = _bagNZip(srcObjectKey, tsFilesList)
        except HPatrolError:
            logger.warning(f"Unable to create zipfile")
            return False

        # Prepare the output S3 key
        # OLDFIXME: (OBE) Go to queues
        # Temporary hack to separate special outputs; will fix implementing queues for Copier
        if "lz/nor/" in srcObjectKey:
            zippedKey = srcObjectKey.replace(GLOBALS.landingZone, "norData")
        else:
            zippedKey = srcObjectKey.replace(GLOBALS.landingZone, GLOBALS.deliveryKey)
        zippedKey = zippedKey.replace(outputExtention, ".zip")
        # OLDTODO: (OBE) Implement "finalFileSuffix" functionality; need access to aimpoint JSON for this
        zippedKey = ut.dashify(zippedKey)
        # logger.debug(f"Wouldave pushed:  {zippedKey}")

        _sendToBucket(zipName, zippedKey)

    return True


def _straightMove(fileList, srcBucketName):
    # Move the individual files found in the playlist
    logger.info(f"Copying files named inside the m3u to destination")
    for srcTsKey in fileList:
        dstTsKey = srcTsKey.replace(GLOBALS.landingZone, GLOBALS.deliveryKey)

        if not GLOBALS.S3utils.copyFileToDifferentBucket(
            srcBucketName=srcBucketName,
            srcObjKey=srcTsKey,
            dstBucketName=config['defaultDstBucket'],
            dstObjKey=dstTsKey
        ):
            # Maybe file hasn't appeared yet; try again after delay
            logger.info("Trying again")
            time.sleep(1)
            GLOBALS.S3utils.copyFileToDifferentBucket(
                srcBucketName=srcBucketName,
                srcObjKey=srcTsKey,
                dstBucketName=config['defaultDstBucket'],
                dstObjKey=dstTsKey
            )
        # Ignore any files not there
    return


def _uploadM3u(m3u8List, obfuscate=False):
    if obfuscate:
        logger.info(f"Transforming file contents for network transfer")
        x = 0
        while x < len(m3u8List):
            if m3u8List[x].startswith('#EXTM3U'):
                m3u8List[x] = '!zz19900630qqM'
            elif m3u8List[x].startswith('#EXT-X-'):
                m3u8List[x] = m3u8List[x].replace('#EXT-X-', '!zz19900630qqX')
            elif m3u8List[x].startswith('#EXTINF'):
                m3u8List[x] = m3u8List[x].replace('#EXTI', '!zz19900630qqI')
            x += 1

        dstObjectKey = dstObjectKey.replace('.m3u8', '.txt')

    logger.info(f"Uploading updated m3u8 file to {config['defaultDstBucket']}/{dstObjectKey}")
    retVal = GLOBALS.S3utils.pushDataToS3(
        config['defaultDstBucket'],
        dstObjectKey,
        '\n'.join(m3u8List)
    )

    return


def lambdaHandler(event, context):
    upSince = processInit.preFlightSetup()
    processInit.initSessionObject(config['sessionHeaders'])
    if not processInit.initialize():
        logger.error("Failed to initialize")
        exit(1)

    # Capture our ARN for later use
    GLOBALS.myArn = context.invoked_function_arn

    # Test input
    try:
        test = event['Records'][0]['s3']['object']['key']
    except KeyError:
            logger.error('Invalid message received')
            logger.debug(f"Message received is:{event}")
            return {"status": False}

    try:
        # Pre-set values in case execution is interrupted
        dataLevel = AuditLogLevel.INFO
        systemLevel = AuditLogLevel.INFO
        exitMessage = "Exit with errors"

        # Execute!
        trueOrFalse = execute(event['Records'][0])
        exitMessage = f"Normal execution: Status '{trueOrFalse}'"

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

    testEvent = {
    	"Records": [{
            "s3":{
                "object": {"key": "lz/rtspme/rdk4h57D/2022/05/27/rdk4h57D.m3u8"},
                "bucket": {"name": "highwaypatrol-ch-test"},
                }
            }
        ]
    }
    trueOrFalse = execute(testEvent['Records'][0])

    nownow = int(time.time())
    logger.info(f'Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}')

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
