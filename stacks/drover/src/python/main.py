"""
Module to manage the tasking to the transcoder.

This can be run as a stand-alone python script to test.
When run as stand-alone script, note that certain plumbing must be in place.
"""

# External libraries import statements
import os
import time
import json
import logging
import argparse
import threading
import datetime as dt
from enum import IntEnum


# This application's import statements
try:
    # These are for when running in an EC2
    import processInit
    import systemSettings
    from exceptions import *
    import superGlblVars as GLOBALS
    from superGlblVars import config
    from orangeUtils import auditUtils
    from utils import hPatrolUtils as hput
    from collectionTypes import CollectionType
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
    from src.python.utils import hPatrolUtils as hput
    from src.python.collectionTypes import CollectionType
    from src.python.orangeUtils.auditUtils import AuditLogLevel


class DroverTask(IntEnum):
    """
    Doing this only to speed up comparison statements (ints instead of strings)
    It may also help in the future if we ever get to refactoring
    """
    TRANSCODE = 1
    TIMELAPSE = 2
    TAKEAUDIO = 3


class DroverInterval(IntEnum):
    """
    Allowed intervals
    """
    ONE     = 1
    TWO     = 2
    THREE   = 3
    FOUR    = 4
    FIVE    = 5
    SIX     = 6
    TEN     = 10
    TWELVE  = 12
    FIFTEEN = 15
    TWENTY  = 20
    THIRTY  = 30
    SIXTY   = 60


logger = logging.getLogger()
DEFAULT_INTERVAL = DroverInterval.FIFTEEN


def _shouldTranscode(interval:DroverInterval, minute):
    return minute % interval == 0


# Works when interval is a factor of 60 (1,2,3,4,5,6,10,12,15,20,30,60)
def _getLastIntervalSpan(vMin, interval:DroverInterval):
    vMinStart = int(vMin / interval) * interval - interval
    if vMinStart < 0:
        vMinStart = vMinStart + 60
    vMinEnd = vMinStart + interval
    return vMinStart, vMinEnd


def _calculateMinRange(now, interval):    
    nowInEpoch = int(now.timestamp())
    pMinStart, pMinEnd = _getLastIntervalSpan(now.minute, interval)
    logger.info(f"Will get time range from :{pMinStart:02} to :{pMinEnd}")

    if now.minute < interval:
        targetDay = nowInEpoch - 1*60*60
    else:
        targetDay = nowInEpoch

    tDt = dt.datetime.fromtimestamp(targetDay)
    # Notice we're using the pMinStart identified above
    tDtStr = f"{tDt.year} {tDt.month} {tDt.day} {tDt.hour} {pMinStart}"
    fromTime = dt.datetime.strptime(tDtStr, '%Y %m %d %H %M')
    fromTimeInEpoch = int(fromTime.timestamp())

    # Make clips at interval x seconds; ex. if 15m then video clips=900s, if 10m then video clips=600s
    toTime = interval * 60

    logger.info(f"Going from '{fromTimeInEpoch}' to '{fromTimeInEpoch + toTime}'")
    return tDt, fromTimeInEpoch, toTime


### DEPRECATED ###
def _getLast15mSpan(vMin):
    if vMin < 15:
        vMinStart = 45
        vMinEnd = 60
    elif vMin < 30:
        vMinStart = 0
        vMinEnd = 15
    elif vMin < 45:
        vMinStart = 15
        vMinEnd = 30
    elif vMin < 60:
        vMinStart = 30
        vMinEnd = 45
    return vMinStart, vMinEnd

### DEPRECATED ###
def _calculate15minRange(now):
    # This time-range function and complex hoop-jumps is being used because
    # we want 15minute-on-the-clock chunks, not just any arbitrary 15minute chunks

    # now = dt.datetime(2022,1,31,1,3)  # Test for new hour
    # now = dt.datetime(2022,1,31,0,3)  # Test for new day
    # now = dt.datetime(2022,1,1,0,3)   # Test for new year
    # now = dt.datetime(2022,2,1,0,3)   # Test for new month

    nowInEpoch = int(now.timestamp())
    pMinStart, pMinEnd = _getLast15mSpan(now.minute)
    logger.info(f"Will get time range from :{pMinStart:02} to :{pMinEnd}")

	# For every start of a new hour, use the previous hour
    if now.minute < 15:
        targetDay = nowInEpoch - 1*60*60
    else:
        targetDay = nowInEpoch

    tDt = dt.datetime.fromtimestamp(targetDay)
    # Notice we're using the pMinStart identified above
    tDtStr = f"{tDt.year} {tDt.month} {tDt.day} {tDt.hour} {pMinStart}"
    fromTime = dt.datetime.strptime(tDtStr, '%Y %m %d %H %M')
    fromTimeInEpoch = int(fromTime.timestamp())

    # logger.debug(f"NOW IS  : {now.strftime('%Y-%m-%d %H:%M:%S')} ({nowInEpoch})")
    # logger.debug(f"PREVIOUS: {fromTime} ({fromTimeInEpoch})")
    toTime = 900     # Make 15min video clips (900s)

    logger.info(f"Going from '{fromTimeInEpoch}' to '{fromTimeInEpoch + toTime}'")
    return tDt, fromTimeInEpoch, toTime


def lambdaHandler(event, context):
    upSince = processInit.preFlightSetup()
    processInit.initSessionObject(config['sessionHeaders'])
    if not processInit.initialize():
        logger.error("Failed to initialize")
        exit(1)

    # Capture our ARN for later use
    GLOBALS.myArn = context.invoked_function_arn

    # Test input correctness
    try:
        test = event['task']
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
        trueOrFalse = True
        execute(event)
        exitMessage = "Normal execution"

    except Exception as e:
        logger.exception(f"UNHANDLED EXCEPTION CAUGHT:::{e}")
        systemLevel = AuditLogLevel.CRITICAL
        dataLevel = None
        trueOrFalse = False

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


def execute(taskConfig, s3Dir=None):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Drover"

    # If no specific folder is requested, select all current aimpoints
    if not s3Dir:
        s3Dir = GLOBALS.targetFiles
    logger.info(f"Looking for files in '{s3Dir}'")
    fileList = GLOBALS.S3utils.getFilesAsStrList(config['defaultWrkBucket'], s3Dir)
    # logger.debug(f"fileList:{fileList}")
    try:
        logger.info(f"Total aimpoints found:{len(fileList)}")
    except TypeError:
        raise HPatrolError("No aimpoints found")

    # Capture the current time in order to determine intervals
    now = dt.datetime.now()

    try:
        # For when using CLI, or if we want to test lambda event
        now = dt.datetime.fromtimestamp(int(taskConfig["epoch"]))
        logger.info(f"Now-time manually specified ({int(now.timestamp())}) {now}")
    except KeyError:
        pass

    if taskConfig["task"] == "transcode":
        theTask = DroverTask.TRANSCODE
    elif taskConfig["task"] == "timelapse":
        theTask = DroverTask.TIMELAPSE
    elif taskConfig["task"] == "audio":
        theTask = DroverTask.TAKEAUDIO

    _sendTaskings(theTask, fileList, now)


def _sendTaskings(theTask, fileList, now:dt.datetime):
    # Set default transcoder interval
    # Notice we start to focus on files as if we were "15 minutes ago".
    # This is because the Collector can overlap 15minute segments, and may still be collecting
    # The easiest way to think of this is by the following time-continuum (using Consolas font):
    #  <------STABLE FILES-----> | <----MAY BE CURRENTLY BEING COLLECTED----> Now
    # ┌──────────────────────────┬─────────────────────────┬───────────────────┐⇨⇨⇨
    # n                         30                         15                  0
    # ⇦═════GRAB THESE FILES═════╩══════════════════════SKIPPED════════════════╝
    #
    nowDtime = now - dt.timedelta(minutes=DEFAULT_INTERVAL)
    # Please note that this causes a delay for *new* feeds processed to appear downstream
    # So even though data will be collected, it won't show up immediately until after about 30mins

    if theTask == DroverTask.TRANSCODE or theTask == DroverTask.TAKEAUDIO:
        defaultTgtDay, defaultFromTimeInEpoch, defaultClipLen = _calculateMinRange(nowDtime, DEFAULT_INTERVAL)
    elif theTask == DroverTask.TIMELAPSE:
        tgtDay = nowDtime
        fromTimeInEpoch = int(nowDtime.timestamp())
        clipLen = 0     # Calculated later since it's aimpoint specific
    else:
        raise HPatrolError("No known task specified")

    # For each aimpoint file, read to see if transcoding is requested
    for idx, aFile in enumerate(fileList, start=1):
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and idx == 2:
            logger.debug(f"Not running on PROD; exiting at file #{idx}")
            break

        logger.debug(f"Processing file '{aFile}'")
        contents = GLOBALS.S3utils.readFileContent(config["defaultWrkBucket"], aFile)
        try:
            targetConfig = json.loads(contents)
        except Exception as e:
            logger.warning(f"Error processing input file; skipping:::{e}")
            logger.warning(f"Processing file '{contents}'")
            continue

        # Check if transcodeExt is null
        # Some aimpoints may be enabled=False and still have
        # data collected elsewhere and needing our post-processing
        try:
            if not targetConfig["transcodeExt"]:
                logger.info("Aimpoint disabled; skipping")
                continue
        except KeyError as e:
            # Not all aimpoints request transcoding
            # logger.info(f"Convert request not in aimpoint; continuing")
            continue

        try:
            if targetConfig["decoy"]:
                logger.info("Decoy aimpoint; skipping")
                continue
        except KeyError as e:
            pass

        try:
            if targetConfig["transcodeOptions"]:
                transcodeOptions = targetConfig["transcodeOptions"]
        except KeyError as e:
            transcodeOptions = {}

        # Determine if transcoder interval was given
        if theTask == DroverTask.TRANSCODE or theTask == DroverTask.TAKEAUDIO:
            try:
                # Capture transcoderInterval parameter to determine the proper transcoding window
                interval = DroverInterval(targetConfig["transcoderInterval"])
                # Determine if we are at the proper minute mark for transcoding
                if _shouldTranscode(interval, now.minute):
                    customNowDtime = now - dt.timedelta(minutes=interval)
                    tgtDay, fromTimeInEpoch, clipLen = _calculateMinRange(customNowDtime, interval)
                else:
                    # logger.debug("Not transcoding yet")
                    continue    
            except:
                # Determine if we are at minute 0, 15, 30, or 45 for default behavior
                if _shouldTranscode(DroverInterval.FIFTEEN, now.minute):
                    tgtDay = defaultTgtDay
                    clipLen = defaultClipLen
                    fromTimeInEpoch = defaultFromTimeInEpoch
                else:
                    # logger.debug("Not transcoding yet")
                    continue

        if theTask == DroverTask.TAKEAUDIO:
            try:
                if not targetConfig["extractAudio"]["enabled"]:
                    logger.info("Audio extraction disabled; skipping")
                    continue
            except KeyError as e:
                # We are requesting TAKEAUDIO but this aimpoint doesn't
                # logger.info(f"Take audio request not in aimpoint; continuing")
                continue

        wrkBucketName = hput.pickBestBucket(targetConfig, "wrkBucket")
        dstBucketName = hput.pickBestBucket(targetConfig, "dstBucket")

        if theTask == DroverTask.TAKEAUDIO:
            try:
                deliveryKey = targetConfig["extractAudio"]["deliveryKey"]
                if not deliveryKey:
                    deliveryKey = GLOBALS.audiosPlace
                else:
                    logger.info(f"Using aimpoint-specified audio deliveryKey '{deliveryKey}'")
            except KeyError:
                deliveryKey = GLOBALS.audiosPlace
        else:
            try:
                deliveryKey = targetConfig["deliveryKey"]
                if not deliveryKey:
                    deliveryKey = GLOBALS.deliveryKey
                else:
                    logger.info(f"Using aimpoint-specified deliveryKey '{deliveryKey}'")
            except KeyError:
                deliveryKey = GLOBALS.deliveryKey

        try:
            ffmpegDedup = targetConfig["ffmpegDedup"]
        except:
            ffmpegDedup = GLOBALS.ffmpegDedup

        # Handle single-string input in the deliveryKey field
        if type(deliveryKey) is str:
            deliveryKey = deliveryKey.split()

        try:
            videoBuffer = int(targetConfig["transcodedBuffer"])
        except Exception:
            videoBuffer = 10
        if videoBuffer > 30: videoBuffer = 30   # Cap the buffer at 30seconds

        # Add a buffer to the front and back of the calculated timeframe
        # Notice that theFilename(s) will still retain the original fromTimeInEpoch time
        startTime = fromTimeInEpoch - videoBuffer
        stopTime = clipLen + videoBuffer * 2    # x2 because we just cut the startTime

        filenameBase = hput.formatNameBase(targetConfig['filenameBase'], targetConfig['deviceID'])
        theFilename = hput.formatNameSuffix(f"{filenameBase}.{targetConfig['transcodeExt']}",
                                            targetConfig['finalFileSuffix'],
                                            fromTimeInEpoch)
        logger.info(f"Processing '{filenameBase}'")

        # Prepare the task name for putting it on the queue
        # Want to keep the Transcoder independent so it can be used by other projects
        if theTask == DroverTask.TRANSCODE:
            taskWord = "transcode"
        elif theTask == DroverTask.TIMELAPSE:
            taskWord = "timelapse"
        elif theTask == DroverTask.TAKEAUDIO:
            taskWord = "takeaudio"

        theMsg = {
            "task": taskWord,
            "filenameBase": filenameBase,
            "outFilename": theFilename,
            "wrkBucket": wrkBucketName,
            "dstBucket": dstBucketName,
            "srcPrefix": "SETLATER",
            "dstPrefix": "SETLATER",
            "clipStart": str(startTime),
            "clipLengthSecs": stopTime,
            "ffmpegDedup": ffmpegDedup,
            "transcodeOptions": transcodeOptions
        }

        # Notice we want to zero-pad the numbers in the path
        # Also notice we may be using a modified date from _calculate15minRange, not "now"
        # This is necessary for the S3 files path because we may be looking at "yesterday"
        resolvedTemplate = targetConfig['bucketPrefixTemplate'].format(
            year=tgtDay.year,
            month=f"{tgtDay.month:02}",
            day=f"{tgtDay.day:02}",
            deviceID=targetConfig["deviceID"]
            )

        if targetConfig["collectionType"] == "STILLS":
            collType = CollectionType.STILLS
        else:
            # For this we only really care about stills right now; irrelevant if anything else
            collType = None

        if theTask == DroverTask.TRANSCODE or theTask == DroverTask.TAKEAUDIO:
            if collType == CollectionType.STILLS:
                # We don't transcode stills
                continue
            theMsg["srcPrefix"] = f"{GLOBALS.landingZone}/{resolvedTemplate}"

        elif theTask == DroverTask.TIMELAPSE:
            if collType != CollectionType.STILLS:
                # We don't do timelapse on non-stills
                continue
            stillsLzTemplate = "{year}/{month}/{day}/{deviceID}".format(
                year=tgtDay.year,
                month=f"{tgtDay.month:02}",
                day=f"{tgtDay.day:02}",
                deviceID=targetConfig["deviceID"]
                )
            theMsg["srcPrefix"] = f"{GLOBALS.stillImages}/{stillsLzTemplate}"

        for aDeliveryKey in deliveryKey:
            theMsg["dstPrefix"] = f"{aDeliveryKey}/{resolvedTemplate}"
            if theTask == DroverTask.TRANSCODE or theTask == DroverTask.TAKEAUDIO:
                logger.debug(f"Message: {json.dumps(theMsg)}")
                GLOBALS.sqsUtils.sendMessage(config['tcdQueue'], theMsg)

            elif theTask == DroverTask.TIMELAPSE:
                _sendTimelapseMessages(theMsg, targetConfig, videoBuffer)


def _sendTimelapseMessages(theMessage, targetConfig, videoBuffer):
    systemPeriodicity = config['systemPeriodicity'] * 60

    # Add timelapse parameter (not used for transcoding)
    theMessage["timelapseFPS"] = targetConfig["timelapseFPS"]

    # clipLen determines the time range of files to grab that make up the timelapse
    clipLen = targetConfig["timelapseLen"]

    delayList = list(range(0, systemPeriodicity, clipLen))
    # delayList indicates the delay intervals which the message will have on the queue
    logger.info(f"Will request every {clipLen} seconds; {len(delayList)} requests total")

    # Add a buffer to the back of the calculated timeframe; # x2 because we also cut startTime
    clipLen = clipLen + videoBuffer * 2
    theMessage["clipLengthSecs"] = clipLen

    now = dt.datetime.now()
    clipStart = int(theMessage["clipStart"])
    for delayIdx, theDelay in enumerate(delayList, start=1):
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and delayIdx == 5:
            logger.debug(f"Not running on PROD; exiting at request #{delayIdx}")
            break

        newClipStart = clipStart + theDelay
        theMessage["clipStart"] = str(newClipStart)

        theFilename = hput.formatNameSuffix(f"{theMessage['filenameBase']}.{targetConfig['transcodeExt']}",
                                            targetConfig['finalFileSuffix'],
                                            newClipStart)
        theMessage["outFilename"] = theFilename

        logger.info(f"Sending '{theMessage['filenameBase']}' "
            f"to {config['tcdQueue']} queue "
            f"with a delay of {str(dt.timedelta(seconds=theDelay))}, "
            f"to run at {(now + dt.timedelta(seconds=theDelay)).strftime('%m/%d %H:%M:%S')}"
        )
        logger.debug(f"Message: {theMessage}")
        GLOBALS.sqsUtils.sendMessage(config['tcdQueue'], theMessage, theDelay)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Drover for the Transcoder:\n'\
            'To send tasking to the Transcoder function',
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument('task',
                        help='task to execute',
                        choices=["transcode", "timelapse", "audio"],
                        )
    parser.add_argument('epoch',
                        help='start epoch suffix of the files on which to operate')
    args = parser.parse_args()

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

    # vvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvv
    # Can use this portion here if we ever need to reprocess a bunch
    # s3Dir = "aimpoints/orionnet.online"
    # humanTimeFrom = "2025-01-21 20:00:00"
    # humanTimeTo   = "2025-01-30 16:30:00"
    # 
    # dtObj = dt.datetime.strptime(humanTimeFrom, "%Y-%m-%d %H:%M:%S")
    # fromHere = int(dtObj.timestamp())
    # fromHere = fromHere + 1800  # fix to start time on the requested
    # dtObj = dt.datetime.strptime(humanTimeTo, "%Y-%m-%d %H:%M:%S")
    # toHere = int(dtObj.timestamp())
    # toHere = toHere + 2700  # fix to end time on the requested
    # while fromHere < toHere:
    #     args.epoch = str(fromHere)
    #     event = {"task": args.task, "epoch": args.epoch}
    #     try:
    #         execute(event, s3Dir)
    #     except HPatrolError as err:
    #         logger.error(err)
    #     fromHere = fromHere + 900
    # Be sure to comment out the portion below that calls execute()
    # and change the Transcoding queue (tcdQueue) on systemSettings
    # ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

    event = {"task": args.task, "epoch": args.epoch}
    try:
        execute(event)
    except HPatrolError as err:
        logger.error(err)

    nownow = int(time.time())
    logger.info(f'Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}')

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
