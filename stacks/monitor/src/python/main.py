# External libraries import statements
import os
import time
import json
import math
import random
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
        totalAimpoints = execute()
        exitMessage = "Normal execution"

    except Exception as e:
        logger.exception(f"UNHANDLED EXCEPTION CAUGHT:::{e}")
        systemLevel = AuditLogLevel.CRITICAL
        dataLevel = None
        trueOrFalse = False
        totalAimpoints = None

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
            totalAimpoints=totalAimpoints,
            # **collectionSummaryArgs
            # collectionSummaryArgs1="some",
            # collectionSummaryArgs2="additional",
            # collectionSummaryArgs3="info"
            )

    toPrint = "Exiting Process"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")

    return {"status": trueOrFalse}


def execute():
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Monitor"

    # Obtain current time before we start looping and processing files,
    # so we get an accurate time of the "now" on the targets
    now = dt.datetime.now()

    # Select aimpoints that are currently being monitored
    s3Dir = GLOBALS.monitorTrgt
    logger.info(f"Looking for files in S3: '/{s3Dir}'")
    fileList = GLOBALS.S3utils.getFilesAsStrList(config["defaultWrkBucket"], s3Dir)
    # logger.debug(f"fileList:{fileList}")
    try:
        logger.info(f"Total aimpoints found:{len(fileList)}")
    except TypeError:
        return 0

    monitorOneDomains = set()
    processedDomains = set()
    # For each aimpoint file, check its status
    for idx, aFile in enumerate(fileList, start=1):
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and idx == 2:
            logger.debug(f"Not running on PROD; exiting before processing file #{idx}")
            break

        # If an entire domain is down, only monitor one device per run
        # from that domain; otherwise monitor all devices per domain
        monitorDomain = os.path.dirname(aFile)
        if monitorDomain in monitorOneDomains:
            # Already looked into this domain as being fully down; ignore the rest
            continue
        if monitorDomain not in processedDomains:
            activeDomain = monitorDomain.replace(GLOBALS.monitorTrgt, GLOBALS.targetFiles)
            activeDevices = GLOBALS.S3utils.getFilesAsStrList(config["defaultWrkBucket"], activeDomain)
            if not activeDevices:
                # None found as active; therefore all are in monitoring status
                monitorOneDomains.add(monitorDomain)
                disabledDevices = GLOBALS.S3utils.getFilesAsStrList(config["defaultWrkBucket"], monitorDomain)
                aFile = random.choice(disabledDevices)  # select one aimpoint to test at random
            else:
                processedDomains.add(monitorDomain)

        logger.info(f"Processing file '{aFile}'")
        contents = GLOBALS.S3utils.readFileContent(config["defaultWrkBucket"], aFile)
        try:
            targetConfig = json.loads(contents)
        except Exception as e:
            logger.warning(f"Error processing input file; skipping:::{e}")
            continue

        try:
            if not targetConfig["enabled"]:
                logger.info("Aimpoint disabled; skipping")
                continue
        except KeyError:
            pass

        try:
            monitorFrequency = targetConfig["monitorFrequency"]
        except KeyError:
            monitorFrequency = GLOBALS.monitorFrequency
        currentHour = now.hour
        # Process aimpoint based on its monitorFrequency
        if not currentHour % monitorFrequency == 0:
            continue
    
        _processAndTaskIt(now, targetConfig)

    return len(fileList)


def _processAndTaskIt(now, targetConfig):
    systemPeriodicity = config['systemPeriodicity'] * 60  # convert to seconds
    systemTimeLimit = systemPeriodicity + 30
    # We add 30secs of overlap to the queue orders so as to not lose anything
    # Video may jump and repeat frames, but we prefer that than to lose feed

    # Sometimes we want just one Collector to be spawned during our systemPeriodicity
    # One Collector sometimes is better instead of spawning and re-spawning multiples
    # i.e.: Had a case where the pollFrequency was 2 seconds for stills
    try:
        singleCollector = True == targetConfig["singleCollector"]
    except KeyError:
        singleCollector = False

    try:
        notUsed, theRanges = tu.getWorkHours(now, targetConfig['hours'])
    except KeyError:
        # There is no working hours specified in the aimpoint; go for default
        theRanges = ['0000-2359']

    # This determines how often Collectors are spawned
    for aRange in theRanges:
        # Requests are sent for the same target within the systemPeriodicity, spaced out by the frequency
        # Rounding down instead of up because we rather overlap than have gaps in transmission
        # Overlaps are handled later using file hashes
        # Example: (round down)
        #          if pollFrequency = 28, frequency = 20
        # FIXME: YouTube single requests do not use pollFrequency so aimpoint may not have this; errors out
        pollFrequency = targetConfig["pollFrequency"]
        if pollFrequency < 10:
            frequency = 10
            logger.warning(f"Poll frequency < 10; rounded to 10s")
        elif pollFrequency >= systemPeriodicity:
            # If the target's poll frequency is larger than system frequency, do just one
            frequency = systemTimeLimit
        else:
            frequency = int(math.floor(pollFrequency / 10.0)) * 10

        # delayList indicates the delays which the task messages will have on the queue
        delayList = list(range(0, systemTimeLimit, frequency))
        try:
            # Initial delayList is further reduced to the target's working hours
            delayList = tu.getReducedSegmentsRange(delayList, now, targetConfig['hours']['tz'], aRange)
        except KeyError:
            # No Working Hours specified
            pass

        if delayList != []:
            if singleCollector:
                delayList = [delayList[0]]
                logger.info(f"SingleCollector requested")
            else:
                addPlural = 's' if len(delayList) > 1 else ''                
                logger.info(f"Will request every {frequency} seconds; {len(delayList)} request{addPlural} total")

            _sendTasks(now, delayList, targetConfig)


def _sendTasks(now, delayList, targetConfig):
    for idx, theDelay in enumerate(delayList, start=1):
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and idx == 5:
            logger.debug(f"Not running on PROD; exiting at request #{idx}")
            break

        baseName = hput.formatNameBase(targetConfig["filenameBase"], targetConfig["deviceID"])
        logger.info(f"Sending '{baseName}' "
            f"to {config['disQueue']} queue "
            f"with a delay of {str(dt.timedelta(seconds=theDelay))}, "
            f"to run at {(now + dt.timedelta(seconds=theDelay)).strftime('%m/%d %H:%M:%S')}"
        )
        # logger.debug(f"Message: {json.dumps(targetConfig)}")
        GLOBALS.sqsUtils.sendMessage(config["disQueue"], targetConfig, theDelay)


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
        execute()
    except HPatrolError as err:
        logger.info(f"Caught exception: {err}")

    nownow = int(time.time())
    logger.info(f'Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}')

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
