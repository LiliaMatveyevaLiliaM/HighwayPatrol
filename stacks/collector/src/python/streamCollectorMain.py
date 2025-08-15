# External libraries import statements
import os
import time
import json
import logging
import argparse
import threading
import subprocess
import datetime as dt
from pathlib import Path
from subprocess import CalledProcessError


# This application's import statements
try:
    # These are for when running in an EC2
    import processInit
    import systemSettings
    from exceptions import *
    import superGlblVars as GLOBALS
    from superGlblVars import config
    from systemMode import SystemMode
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
    from src.python.systemMode import SystemMode
    from src.python.orangeUtils import auditUtils
    from src.python.orangeUtils import utils as ut
    from src.python import superGlblVars as GLOBALS
    from src.python.utils import hPatrolUtils as hput
    from src.python.orangeUtils import timeUtils as tu
    from src.python.orangeUtils.auditUtils import AuditLogLevel


logger = logging.getLogger()


def lambdaHandler(event, context):
    upSince = processInit.preFlightSetup()
    processInit.initSessionObject(config["sessionHeaders"])
    if not processInit.initialize():
        logger.error("Failed to initialize")
        exit(1)

    GLOBALS.myArn = context.invoked_function_arn

    try:
        dataLevel = AuditLogLevel.WARN
        systemLevel = AuditLogLevel.WARN
        exitMessage = "Exit with errors"

        exitMessage, trueOrFalse = execute(event)

        dataLevel = AuditLogLevel.INFO
        systemLevel = AuditLogLevel.INFO

        if not trueOrFalse:
            dataLevel = AuditLogLevel.INFO

    except Exception as e:
        logger.exception(f"UNHANDLED EXCEPTION CAUGHT:::{e}")
        trueOrFalse = False
        dataLevel = AuditLogLevel.CRITICAL
        systemLevel = AuditLogLevel.CRITICAL

    finally:
        nowNow = int(time.time())
        logger.info(f"Process clocked at {str(dt.timedelta(seconds=nowNow-upSince))}")

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
            leaveDatetime=dt.datetime.fromtimestamp(nowNow),
            location=event["longLat"],
        )

    # Need to reset; lambdas can keep memory
    config["proxy"] = False

    toPrint = "Exiting Process"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")

    return {"status": trueOrFalse}


def _createDefaultFFMPEGCommand(ap: dict, fileLocation: str) -> list:
    logger.info("Creating simple FFMPEG command; no transcodeOptions key")

    # '-f segment' means to use segment mode, saving each segment as a separate file
    command = f"-i {ap['accessUrl']} -strftime 1 -f segment -t {ap['pollFrequency']} {fileLocation}_%s.ts"

    if GLOBALS.useTestData:
        command = f"-i ./testResources/testVideo.ts -f segment -t {ap['pollFrequency']} {fileLocation}_%s.ts"

    aProxy = None
    # Do we have an aimpoint proxy specified?
    try:
        aProxy = ap["proxy"]
    except KeyError:
        pass
    # If on EC2, default to the one specified on systemSettings
    if not systemSettings.onLambda:
        aProxy = config["proxy"]

    if aProxy:
        # FIXME: Found possible bug in ffmpeg
        # It appears that when STREAM is used to pull a video, the proxy is ignored
        # It seems that to fix it, it just needs to be an environment variable before executing the command
        command = f"-http_proxy {aProxy} {command}"

    command = f"ffmpeg -hide_banner -loglevel error {command}"
    return command.split(" ")


def _createAdvancedFFMPEGCommand(ap: dict, outFileLocation: str) -> list:
    logger.info("Creating FFMPEG command for transcodeOptions key")
    """
    Command order:
        ffmpeg
        <input-options>
        <accessUrl>
        <output-options>
        <duration>
        <output-filename>
    """

    if GLOBALS.useTestData:
        testFile = "./testResources/testVideo.ts"
        logger.debug(f"Using testFile '{testFile}'")
        ap["accessUrl"] = testFile

    # TODO: ffmpeg options for the stream collector should come from separate aimpoint value
    #       rather than using the "transcodeOptions" as they could be different
    aimpointOptions = {**ap["transcodeOptions"]}
    aProxy = None
    # Do we have an aimpoint proxy specified?
    try:
        aProxy = ap["proxy"]
    except KeyError:
        pass
    # If on EC2, default to the one specified on systemSettings
    if not systemSettings.onLambda:
        aProxy = config["proxy"]

    if aProxy:
        addProxy = {"input": {"-http_proxy": aProxy}}
        aimpointOptions = {**ap["transcodeOptions"], **addProxy}

    finalCommand = (
        ["ffmpeg"]
        + ["-hide_banner"]
        + hput.selectOptions(aimpointOptions, "input")
        + ["-i", ap["accessUrl"]]
        + hput.selectOptions(aimpointOptions, "output")
        + ["-t", str(ap["pollFrequency"])]
        + ["-strftime", "1", f"{outFileLocation}_%s.ts"]
    )

    # logger.debug(finalCommand)
    return finalCommand


def _getPrefixBase(jsonConfig: dict) -> str:
    """Set S3 prefix"""
    deviceID = jsonConfig["deviceID"]
    year, month, day = tu.returnYMD(time.time())
    resolvedTemplate = jsonConfig["bucketPrefixTemplate"].format(
        year=year, month=month, day=day, deviceID=deviceID
    )
    return f"{GLOBALS.landingZone}/{resolvedTemplate}"


def _pushThenDelete(file: Path, prefixBase: str, jsonConfig: dict) -> None:
    """Push file to S3, then delete from Lambda"""

    # TODO: Move this pickBestBucket() higher so it only happens once
    bucketName = hput.pickBestBucket(jsonConfig, "wrkBucket")
    newHash = ut.getHashFromFile(config["workDirectory"], file)

    if GLOBALS.S3utils.isFileInS3(
        bucketName, f"{GLOBALS.s3Hashfiles}/{newHash}.md5"
    ):
        logger.info(f"Ignored; segment previously captured ({newHash})")
        return

    if GLOBALS.S3utils.pushToS3(
        str(file),
        prefixBase,
        bucketName,
        s3BaseFileName=Path(file).name,
        deleteOrig=True
    ):
        if newHash:
            if not GLOBALS.S3utils.createEmptyKey(
                bucketName, f"{GLOBALS.s3Hashfiles}/{newHash}.md5"
            ):
                logger.warning("Could not create MD5 file, ignoring its creation")
            return


def _sendToS3(jsonConfig: dict, fnBase: str) -> None:
    """Send files to the landing zone"""
    try:
        doConcat = True == jsonConfig["concatenate"]
    except KeyError:
        doConcat = False

    outFiles = Path(config["workDirectory"])
    files = list(outFiles.glob(f"{fnBase}*.ts"))
    prefixBase = _getPrefixBase(jsonConfig)

    if doConcat:
        concatedFile = ut.concatFiles(
            files, config["workDirectory"], GLOBALS.onProd
        )
        os.rename(concatedFile, files[0])       
        finalList = [files[0]]

    else:
        finalList = files

    [_pushThenDelete(file, prefixBase, jsonConfig) for file in finalList]
    logger.info(f"Done sending {len(finalList)} segments")


def _runCommand(command: list) -> bool:
    """Run the FFMPEG command; return output success/failure"""
    try:
        logger.debug(f"Running command `{' '.join(command)}`")
        subprocess.run(command, check=True)
        logger.info(f"Successfully ran ffmpeg command")

    except CalledProcessError as cmdError:
        logger.error("Error with ffmpeg execution")
        logger.error(cmdError)
        raise HPatrolError("Ffmpeg error")


def execute(ap: dict) -> bool:
    """Capture stream through FFMPEG"""
    GLOBALS.taskName = "Stream Collector"

    # Set path for output files
    fnBase = hput.formatNameBase(ap["filenameBase"], ap["deviceID"])
    fileLocation = f"{config['workDirectory']}/{fnBase}"

    try:
        if ap["transcodeOptions"]:
            command = _createAdvancedFFMPEGCommand(ap, fileLocation)
    except KeyError as err:
        command = _createDefaultFFMPEGCommand(ap, fileLocation)

    try:
        logger.info(
            f"Executing for filenameBase '{hput.formatNameBase(ap['filenameBase'], ap['deviceID'])}'"
        )
    except KeyError as err:
        logger.error(f"{err} not specified in input configuration")
        logger.error(f"Be sure to specify {err} in JSON file")
        return "Exit with errors", False

    logger.info("Type selected: Stream")
    try:
        _runCommand(command)
        _sendToS3(ap, fnBase)
        GLOBALS.sqsUtils.sendMessage(config["statusQueue"], {"aimpoint": ap, "isCollecting": True})
    except HPatrolError:
        GLOBALS.sqsUtils.sendMessage(config["statusQueue"], {"aimpoint": ap, "isCollecting": False})
        return "Exit with errors", False

    return "Normal execution", True


if __name__ == "__main__":
    # Obtain test file name, if given
    # Defaults to testResources/aimpoint-stream.json otherwise
    parser = argparse.ArgumentParser(prog="StreamCollector",
                                     description="Test the collector on an EC2 instance",
                                     formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("-f",
                        help=(
                            "Aimpoint file for testing\n"\
                            "default: testResources/aimpoint-stream.json"
                        ),
                        dest="testFile",
                        default="testResources/aimpoint-stream.json")
    args = parser.parse_args()
    testFile = args.testFile
    # print(args)

    upSince = processInit.preFlightSetup()
    processInit.initSessionObject(config["sessionHeaders"])
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
    arn = f"arn:aws:ec2:{region}:{accountId}:instance/{instanceId}"
    GLOBALS.myArn = arn

    logger.debug(f"Reading from test file '{testFile}'")
    with open(testFile, "r") as f:
        testEvent = json.loads(f.read())
    # logger.debug(f"AIMPOINT:\n'{json.dumps(testEvent)}'")

    try:
        trueOrFalse = execute(testEvent)
    except ConnectionError as err:
        logger.info(f"Caught exception: {err}")

    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
