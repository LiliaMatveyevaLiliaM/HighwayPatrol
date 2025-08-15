"""
Module to create the JSON aimpoints for the saferegion.net site

Function retrieves the main page, parses the device population JSON, then for each
selected device, has to visit another URL to then from there create the subsequent aimpoints

"""

# External libraries import statements
import os
import re
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
    import comparitor as comp
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
    from src.python.exceptions import *
    from src.python import systemSettings
    from src.python import comparitor as comp
    from src.python.superGlblVars import config
    from src.python.orangeUtils import auditUtils
    from src.python.orangeUtils import utils as ut
    from src.python import superGlblVars as GLOBALS
    from src.python.utils import hPatrolUtils as hput
    from src.python.orangeUtils.auditUtils import AuditLogLevel


logger = logging.getLogger()
DOMAIN = "saferegion.net"


def lambdaHandler(event: dict, context: dict) -> dict:
    upSince = processInit.preFlightSetup()
    processInit.initSessionObject(config["sessionHeaders"])
    if not processInit.initialize():
        logger.error("Failed to initialize")
        exit(1)

    # Capture our ARN for later use
    GLOBALS.myArn = context.invoked_function_arn

    try:
        # Pre-set values in case execution is interrupted
        trueOrFalse = False
        dataLevel = AuditLogLevel.INFO
        systemLevel = AuditLogLevel.INFO
        exitMessage = "Exit with errors"

        # Execute!
        if execute(upSince):
            trueOrFalse = True
            exitMessage = "Normal execution"

    except Exception as e:
        logger.exception(f"UNHANDLED EXCEPTION CAUGHT:::{e}")
        systemLevel = AuditLogLevel.CRITICAL
        trueOrFalse = False
        dataLevel = None

    finally:
        nownow = int(time.time())
        logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

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


def execute(upSince: int) -> bool:
    """Main generator execution flow"""
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Parser"
    GLOBALS.subtaskName = "SafeRegionNetParser"

    selectionFile = f"selected-{DOMAIN}.json"
    populationUrl = "https://saferegion.net/city/yar/public/"

    try:
        population = _getPopulation(populationUrl)
    except Exception as err:
        logger.exception(f"Error getting target list:::{err}")
        return False

    try:
        selection = hput.getSelection(selectionFile)
        # logger.debug(f"Selection = {selection}")
    except HPatrolError:
        return False

    structTitles = (
          "ID"
        , "Name"
        , "NameInTheUrl"
        , "Type"
        , "Longitude"
        , "Latitude"
        , "PTZ"
        , "Rotate"
        , "PreviewUrl"
    )
    structKeys = (
          "id"
        , "name"
        , "name_url"
        , "type"
        , "lng"
        , "lat"
        , "ptz"
        , "rotate"
        , "preview"
    )
    configTemplate = _getConfigTemplate()
    domainFolder = _getDomainFolder(configTemplate)

    try:
        comp.writeAPs(
            upSince,
            population,
            (structKeys, structTitles),
            domainFolder,
            "rptSafeRegionMasterIdList",
            selectedList=selection
        )
    except HPatrolError:
        logger.exception("Unable to do ID comparison")

    try:
        _doVideos(population, selection, configTemplate)
    except HPatrolError:
        return False

    return True


def _getPopulation(anUrl: str) -> dict:
    logger.info("Getting target population")

    if GLOBALS.useTestData:
        testFile = "testResources/saferegionNetPopulation.html"
        logger.info(f"Reading from test file '{testFile}'")
        with open(testFile, "r", encoding="utf-8") as f:
            respText = f.read()

    else:
        try:
            r = GLOBALS.netUtils.get(anUrl, headers=config["sessionHeaders"])
        except:
            raise HPatrolError(
                f"URL access failed from {GLOBALS.perceivedIP} attempting {anUrl}"
            )
        respText = r.text

    regex = r"<div id=\"cams_json\" style=\"display: none;\">(.*)</div>"
    matches = re.search(regex, respText)
    if matches:
        # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
        # for groupNum in range(0, len(matches.groups())):
        #     groupNum = groupNum + 1
        #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

        # logger.debug(f"\nGROUP1: \n{matches.group(1)}\n")
        theMatch = matches.group(1)
        # logger.debug(f"theMatch: {theMatch}")

    else:
        logger.info("Population data not found; exiting")
        logger.debug(f"Content received is:\n{respText}")
        raise HPatrolError("Data not found")

    population = json.loads(theMatch)
    logger.info(f"Total IDs in population: {len(population)}")
    return population


def _getIframeData(anUrl: str) -> dict:
    """Get the specific device's connection data"""
    logger.info("Getting iFrame data")

    if GLOBALS.useTestData:
        testFile = "testResources/iFrameData.html"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(testFile, "r", encoding="utf-8") as f:
            respText = f.read()

    else:
        try:
            r = GLOBALS.netUtils.get(anUrl, headers=config["sessionHeaders"])
        except:
            raise HPatrolError(
                f"URL access failed from {GLOBALS.perceivedIP} attempting {anUrl}"
            )
        respText = r.text

    regex = r"<div class=\"iframe_cam_json\" style=\"display: none;\">(.*)</div>"
    matches = re.search(regex, respText)
    if matches:
        # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
        # for groupNum in range(0, len(matches.groups())):
        #     groupNum = groupNum + 1
        #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

        # logger.debug(f"\nGROUP1: \n{matches.group(1)}\n")
        theMatch = matches.group(1)
        # logger.debug(f"theMatch: {theMatch}")

    else:
        logger.info("iFrame data not found; exiting")
        logger.debug(f"Content received is:\n{respText}")
        raise HPatrolError("Data not found")

    iframeData = json.loads(theMatch)
    return iframeData


def _doVideos(allCams, selection, configTemplate):
    theKey = f"{DOMAIN}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    urlTemplate = "https://{server}/{stream}/{hlsType}.m3u8?token={session}"
    iframeTemplate = "https://saferegion.net/cams/iframe/{nameUrl}/{iframeHash}/hls/"

    counter = 1
    # Loop goes through the population, so not using enumerate()
    # if we used enumerate(), we wouldn't go through the entire file
    for aCam in allCams:
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and counter == 5:
            logger.debug(f"Not running on PROD; exiting at device #{counter}")
            break

        theID = str(aCam["id"])
        # logger.debug(f"theID:{theID}")
        if theID in selection:
            # Ignore disabled IDs
            if selection[theID] == "off":
                continue

            logger.info(f"Creating JSON file for ID: {theID}")
            nameUrl = aCam["name_url"]
            iframeHash = aCam["iframe_hash"]
            iframeUrl = iframeTemplate.format(nameUrl=nameUrl, iframeHash=iframeHash)
            iframe = _getIframeData(iframeUrl)

            try:
                server = iframe["server"]
                stream = iframe["stream"]
                session = iframe["session"]
                hlsType = iframe["hls_type"]
            except KeyError as err:
                logger.warning(f"Can't create aimpoint::Missing key {err}")
                logger.debug(f"JSON received is:\n{json.dumps(iframe)}")
                continue

            configTemplate["accessUrl"] = urlTemplate.format(
                server=server, stream=stream, hlsType=hlsType, session=session
            )

            configTemplate["deviceID"] = theID
            if selection[theID] == "decoy" or selection[theID] == "monitor-decoy":
                configTemplate["decoy"] = True
            else:
                configTemplate["decoy"] = False

            configTemplate["transcodeExt"] = None
            if selection[theID] == "mp4" or selection[theID] == "monitor-mp4":
                configTemplate["transcodeExt"] = "mp4"

            configTemplate["longLat"] = [aCam["lng"], aCam["lat"]]
            configTemplate["headers"]["Host"] = server

            # logger.debug(configTemplate)
            outFile = os.path.join(config["workDirectory"], f"{theID}.json")
            try:
                ut.writeJsonDataToFile(configTemplate, outFile)
            except Exception as err:
                logger.exception(f"Error creating aimpoint file:::{err}")
                continue

            s3Dir = aimpointDir
            if selection[theID] in ["monitor", "monitor-mp4", "monitor-decoy"]:
                s3Dir = monitoredDir

            result = GLOBALS.S3utils.pushToS3(
                outFile,
                s3Dir,
                config["defaultWrkBucket"],
                s3BaseFileName=f"{theID}.json",
                deleteOrig=GLOBALS.onProd,
                extras={"ContentType": "application/json"}
            )
            counter += 1


def _getConfigTemplate():
    configTemplate = {
          "deviceID": "SETLATER"
        , "enabled": True
        , "decoy": "SETLATER"
        , "collRegions": ["United States (N. Virginia)"]
        , "vpn": "ru.hpatrol.dom:8080"
        , "collectionType": "M3U"
        , "accessUrl": "SETLATER"
        , "pollFrequency": 24
        , "concatenate": False
        , "transcodeExt": "SETLATER"
        , "longLat": "SETLATER"
        , "filenameBase": "{deviceID}"
        , "finalFileSuffix": "_{year}-{month}-{day}-{hour}-{mins}-{secs}"
        , "bucketPrefixTemplate": "ru/saferegionNet/{deviceID}/{year}/{month}/{day}"
        , "headers": {
              "User-Agent": "User-Agent: Mozilla/5.0 (X11; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0"
            , "Accept": "*/*"
            , "Host": "SETLATER"
            , "Sec-Fetch-Site": "none"
            , "Sec-Fetch-Mode": "cors"
            , "Connection": "keep-alive"
            , "Accept-Language": "en-US,en;q=0.9"
            , "Sec-Fetch-Dest": "empty"
            , "Origin": "https://saferegion.net"
            , "Referer": "https://saferegion.net/"
            , "Accept-Encoding": "gzip, deflate, br"
            , "DNT": "1"
        }
        , "devNotes": {
              "givenUrl": "https://yaroslavl-76.ru/kamery-online"
            , "startedOn": "June 6, 2023"
            , "missionTLDN": "ru"
            , "setBy": "edward22"
        }
    }
    return configTemplate


def _getDomainFolder(ap):
    countryDomain = ap["bucketPrefixTemplate"].split("/{deviceID}")[0]
    domainPrefix = f"{GLOBALS.deliveryKey}/{countryDomain}"
    return domainPrefix


if __name__ == "__main__":
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

    execute(upSince)

    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
