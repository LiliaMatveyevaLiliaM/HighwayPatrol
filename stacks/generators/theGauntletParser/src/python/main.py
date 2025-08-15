"""
Aimpoints generator for the following site:

http://xzglwx.gandongyun.com/xz_video/jslwVideoList.jsp?code=021xyF100J4CnK1bDn200vJlKw2xyF1E&state=2000032000

It visits the target site and produces a TAB-delimited list of camera IDs and other pertinent information from the JSON found.
Pertinent data icludes camera ID, name, longitude, latitude, etc.

This list is actually the combination of a list of all the camera info found at the above site, along with
an additional input list of the critical camera IDs fed to this script. Only information on cameras whose
IDs are found on the second list are included in the final product.
"""


# External libraries import statements
import os
import time
import json
import copy
import logging
import argparse
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


# Constants
ORIGIN  = "http://xzglwx.gandongyun.com"
PLY_URL = "http://xzhglcplay.gandongyun.com/live/"
IDS_URL = "http://xzglwx.gandongyun.com/xz_video/video/queryJsCloudlVideo?fPubNumber=20000320000&mapLevel="
THE_URL = "http://xzglwx.gandongyun.com/xz_video/jslwVideoList.jsp?code=021xyF100J4CnK1bDn200vJlKw2xyF1E&state=20000320000"

MAP_LEVELS = ["7", "8", "11", "13"]

IDS_MAIN_PAGE_FILE = "theGauntletIdsPage.json"
DOMAIN = "xzglwx.gandongyun.com"


def execute(upSince, isScript):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Parser"
    GLOBALS.subtaskName = "TheGauntletParser"

    # Get the URLs and other info for the critical IDs
    logger.info('Getting video image IDs dictionaries')
    imageIdsInfo = _getTargetList()
    if not imageIdsInfo:
        logger.exception("No image dictionaries returned")
        return False

    logger.info(f"Number of IDs returned: {len(imageIdsInfo)}")

    # Get the list of selected IDs
    try:
        selectionsFile = f"selected-{DOMAIN}.json"
        videosSelection = hput.getSelection(selectionsFile)
    except HPatrolError as err:
        return False

    structTitles = (
          "ID"
        , "Name"
        , "Road"
        , "Longitude"
        , "Latitude"
        )
    structKeys = (
          "key"
        , "name"
        , "road"
        , "longitude"
        , "latitude"
        )

    configTemplate = _getConfigTemplate()
    domainFolder = _getDomainFolder(configTemplate)

    # If running as a script, comparison is NOT done but aimpoints creation is.
    # If run as a lambda, comparison *is* done, and the master file and the
    # aimpoint files are re-created as necessary
    shouldWriteAimpoints = False
    if isScript:
        shouldWriteAimpoints = True
    else:
        try:
            shouldWriteAimpoints = comp.writeAPs(
                    upSince,
                    imageIdsInfo,
                    (structKeys, structTitles),
                    domainFolder,
                    "rptTheGauntletMasterIdList",
                    selectedList=videosSelection)
        except HPatrolError:
            logger.exception("Unable to do ID comparison")
            return False

    if shouldWriteAimpoints:
        try:
            _doVideoCams(imageIdsInfo, videosSelection, configTemplate)
        except HPatrolError:
            return False

    return True


def _getTargetList():
    if GLOBALS.useTestData:
        mainPageForTesting = "testResources/" + IDS_MAIN_PAGE_FILE
        logger.debug(f"Reading from test file '{mainPageForTesting}'")
        with open(mainPageForTesting, 'r') as f:
            camIdsDictList = json.loads(f.read())
    else:
        # First visit the main site
        idsHost = THE_URL.split('/')[2]
        headersDict = copy.deepcopy(config['sessionHeaders'])
        headersDict['DNT'] = "1"
        headersDict['Host'] = idsHost
        headersDict['Upgrade-Insecure-Requests'] = "1"
        try:
            throwAway = GLOBALS.netUtils.get(THE_URL, headers=config['sessionHeaders'])
        except:
            raise ConnectionError(f"URL access failed for: {THE_URL}") from None

        # Visit the cameras IDs site and retrieve JSON containing the ID and related info
        headersDict.pop('Upgrade-Insecure-Requests', None)
        headersDict['Referer'] = THE_URL
        headersDict['X-Requested-With'] = "XMLHttpRequest"
        camIdsDictList = []
        for mapLevel in MAP_LEVELS:
            idsUrl = IDS_URL + mapLevel
            try:
                camIdsResp = GLOBALS.netUtils.get(idsUrl, headers=headersDict)
            except:
                raise ConnectionError(f"URL access failed for: {idsUrl}") from None

            siteDictList = json.loads(camIdsResp.text)
            camIdsDictList.extend(siteDictList)

    retDictList = []
    for idsDict in camIdsDictList:
        try:
            theID = idsDict["id"]
            theName = idsDict["cn"]
            theRoad = idsDict["ro"]
            theLat = idsDict["la"]
            theLong = idsDict["lo"]
        except KeyError:
            logger.warning(f"Key error in JSON for aCamDict: {idsDict}")
            continue

        camDict = {
            "key": theID,
            "name": theName,
            "road": theRoad,
            "longitude": str(theLong),
            "latitude": str(theLat)
        }
        retDictList.append(camDict)

    return retDictList


def _allInputsValid(camSpec):
    if camSpec.get('key', "") == "":
        return False
    if camSpec.get('name', "") == "":
        return False
    if camSpec.get('road', "") == "":
        return False
    if camSpec.get('latitude', "") == "":
        return False
    if camSpec.get('longitude', "") == "":
        return False

    return True


def _doVideoCams(allCamsDict, selection, configTemplate):
    theKey = f"{DOMAIN}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    # Loop through the cams
    for idx, aCam in enumerate(allCamsDict, start=1):
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and idx == 6:
            logger.debug(f"Not running on PROD; exiting at device #{idx}")
            break
        
        if not _allInputsValid(aCam):
            logger.info(f"Invalid data on '{aCam}'; continuing")
            continue

        theID = str(aCam["key"])
        if theID in selection:
            theName = aCam["name"]
            theRoad = aCam["road"]
            theLat  = aCam["latitude"]
            theLong = aCam["longitude"]

            logger.info(f"Creating JSON file for ID:{theID}")
            configTemplate["deviceID"] = theID
            configTemplate["longLat"] = [theLong, theLat]
            configTemplate["filenameBase"] = theID
            configTemplate["bucketPrefixTemplate"] = f"cn/ganDong/{theID}/{{year}}/{{month}}/{{day}}"

            configTemplate["decoy"] = False
            if selection[theID] == "decoy" or selection[theID] == "monitor-decoy":
                configTemplate["decoy"] = True

            configTemplate["transcodeExt"] = None
            if selection[theID] == "mp4" or selection[theID] == "monitor-mp4":
                configTemplate["transcodeExt"] = "mp4"

            configTemplate["devNotes"]["road"] = theRoad
            configTemplate["devNotes"]["name"] = theName

            outFile = os.path.join(config['workDirectory'], f"{theID}.json")
            try:
                ut.writeJsonDataToFile(configTemplate, outFile)
            except Exception as err:
                logger.exception(f"Error creating aimpoint file:::{err}")
                return False

            s3Dir = aimpointDir
            if selection[theID] in ["monitor", "monitor-mp4", "monitor-decoy"]:
                s3Dir = monitoredDir

            result = GLOBALS.S3utils.pushToS3(outFile,
                                    s3Dir,
                                    config['defaultWrkBucket'],
                                    s3BaseFileName=f"{theID}.json",
                                    deleteOrig=GLOBALS.onProd,
                                    extras={'ContentType': 'application/json'})

    return True


def _getConfigTemplate():
    configTemplate = {
          "deviceID": "SETLATER"
        , "enabled": True
        , "decoy": "SETLATER"
        , "collRegions": ["Asia Pacific (Singapore)"]
        , "collectionType": "GNDONG"
        , "accessUrl": THE_URL
        , "pollFrequency": 12
        , "waitFraction": 0.6
        , "singleCollector": True
        , "concatenate": False
        , "transcodeExt": "SETLATER"
        , "filenameBase": "SETLATER"
        , "finalFileSuffix": "_{year}-{month}-{day}-{hour}-{mins}"
        , "longLat": "SETLATER"
        , "bucketPrefixTemplate": "cn/ganDong/{deviceID}/{year}/{month}/{day}"
        , "headers": {
              "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0"
            , "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        	, "Accept-Encoding": "gzip, deflate"
            , "Accept-Language": "en-US,en;q=0.5"
            , "Connection": "keep-alive"
            , "DNT" : "1"
            , "Host" : PLY_URL.split('/')[2]
            , "Origin" : ORIGIN
            , "Referer" : ORIGIN + '/'
            }
        , "devNotes": {
              "startedOn": "March 2023"
            , "road": "SETLATER"
            , "name": "SETLATER"
            , "setBy": "edward22"
            , "missionTLDN": "cn"
            , "freqNote": "Playlist file points to about 12 - 15 seconds worth of data"
            , "singleNote": "playlist contains up to 3 .ts URLs each pointing to ~5 seconds of data"
            }
        }
    return configTemplate


def _getDomainFolder(ap):
    countryDomain = ap["bucketPrefixTemplate"].split("/{deviceID}")[0]
    domainPrefix = f"{GLOBALS.deliveryKey}/{countryDomain}"
    return domainPrefix


def lambdaHandler(event, context):
    upSince = processInit.preFlightSetup()
    processInit.initSessionObject(config['sessionHeaders'])
    if not processInit.initialize():
        logger.error("Failed to initialize")
        exit(1)

    # Capture our ARN for later use
    GLOBALS.myArn = context.invoked_function_arn

    # Pre-set values in case execution is interrupted
    trueOrFalse = False
    dataLevel = AuditLogLevel.INFO
    systemLevel = AuditLogLevel.INFO
    exitMessage = "Exit with errors"

    try:
        if execute(upSince, False):
            trueOrFalse = True
            exitMessage = "Normal execution"

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
    parser = argparse.ArgumentParser(
        description='Aimpoint generator for videos',
        formatter_class=argparse.RawTextHelpFormatter
    )
    theChoices = ["videos"]
    parser.add_argument('task',
                        help='task to execute',
                        choices=theChoices,
                        type=str.lower,
                        nargs='?',
                        const=''
                        )
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

    argVal = args.task
    if argVal:
        execute(upSince, True)
    else:
        execute(upSince, False)

    nownow = int(time.time())
    logger.info(f'Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}')

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
