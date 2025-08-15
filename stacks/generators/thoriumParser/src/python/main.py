"""
Module to create the aimpoints for the Thorium IDs

If run as a script, use the command line parameter:

VIDEOS - to generate the video aimpoints

When you specify the above parameter, this will force the rewrite
of the VIDEOS aimpoints. In this case, there is no comparison with
the 'Master' list.

If you do not specify a parameter, this script will behave like the
lambda version. If run as a lambda, this code behaves as follows:

This code will compare the current list of IDs (and other info) with a
'Master' list in the 'metadata' folder on S3. If there is no master list
found, this code will create one and store it in the 'metadata' folder.
A date-stamped version is also created and stored under the 'thorium'
folder which is found in that same folder.

If an ID is added, deleted, or the imageURL for an ID is modified,
this script will also re-write the VIDEOS aimpoints.
"""

# External libraries import statements
import os
import time
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
ORIGIN  = "https://cams.is74.ru"
IDS_URL = "https://cams.is74.ru/api/get-group/225"
PLY_URL = "https://cdn.cams.is74.ru/hls/playlists/ts.m3u8?quality=sub&uuid="

MAIN_PAGE_XML_FILE = "thoriumMainPage.xml"
DOMAIN = "cams.is74.ru"


def execute(upSince, isScript):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Parser"
    GLOBALS.subtaskName = "ThoriumParser"

    # Get the URLs and other info for the critical IDs
    logger.info("Getting video image IDs dictionaries")
    imageIdsInfo = _getTargetList()
    if not imageIdsInfo:
        logger.exception("No image dictionaries returned")
        return False

    # Get the list of selected IDs
    try:
        selectionsFile = f"selected-{DOMAIN}.json"
        videosSelection = hput.getSelection(selectionsFile)
    except HPatrolError as err:
        return False

    structTitles = (
          "ID"
        , "UUID"
        , "Name"
        , "Address"
        , "URL"
        , "Latitude"
        , "Longitude"
        , "Status"
        )
    structKeys = (
          "key"
        , "uuid"
        , "name"
        , "address"
        , "url"
        , "latitude"
        , "longitude"
        , "status"
        )

    configTemplate = _getConfigTemplate()
    domainFolder = _getDomainFolder(configTemplate)

    # If running as a script, comparison is NOT done but aimpoints creation is
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
                    "rptThoriumMasterIdList",
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
        mainPageForTesting = "testResources/" + MAIN_PAGE_XML_FILE
        logger.debug(f"Reading from test file '{mainPageForTesting}'")
        with open(mainPageForTesting, 'r') as f:
            pageContent = f.read()
    else:
        # First visit the main site
        try:
            resp = GLOBALS.netUtils.get(IDS_URL, headers=config['sessionHeaders'])
        except:
            raise ConnectionError(f"URL access attempt failed for: {IDS_URL}")

        # Retrieve the HTML text containing ID info
        pageContent = resp.text

    # All returned content should be between <response> and </response>
    tableContent = _getStrBetween(pageContent, "<response>", "</response>")
    if not tableContent:
        logger.exception("No response found")
        return None

    # Extract the items from the table
    itemList = _getRepeatingStrBetween(tableContent, "<item>", "</item>")

    # We store the extracted info as a list of dictionaries
    dictList = []

    # Now extract the textual content we need from the item
    for item in itemList:
        # The OBJECT has to be a CAMERA
        obj = _getStrBetween(item, "<OBJECT>", "</OBJECT>")
        if obj != "CAMERA":
            continue

        # Get the ID - is actually a string of digits, but we
        # pad with zeroes on the left and prepend with 'cam'
        idStr = _getStrBetween(item, "<ID>", "</ID>")
        if not idStr:
            continue
        id = f"cam{idStr:0>5}"

        # HLS is the url (there is also REALTIME_HLS)
        url = _getStrBetween(item, "<HLS>", "</HLS>")
        if not url:
            logger.info(f"No HLA found for ID: {id}")
        # There is also a SNAPSHOT which gives URL of still image

        # Get the UUID
        uuid = _getStrBetween(item, "<UUID>", "</UUID>")

        # Get the Name
        name = _getStrBetween(item, "<NAME>", "</NAME>")

        # ACCESS contains LIVE which contains STATUS which is True or False
        status = ""
        access = _getStrBetween(item, "<ACCESS>", "</ACCESS>")
        if access:
            live = _getStrBetween(access, "<LIVE>", "</LIVE>")
            if live:
                status = _getStrBetween(access, "<STATUS>", "</STATUS>")
        
        # Get the ADDRESS (sometimes it is blank)
        address = _getStrBetween(item, "<ADDRESS>", "</ADDRESS>")
        if not address:
            address = "None"

        # Get the POSITION which contains latitude and longitude
        latitude = ""
        longitude = ""
        position = _getStrBetween(item, "<POSITION>", "</POSITION>")
        if not position:
            logger.info(f"No POSITION found for ID: {id}")
        else:
            latitude = _getStrBetween(position, "<LATITUDE>", "</LATITUDE>")
            longitude = _getStrBetween(position, "<LONGITUDE>", "</LONGITUDE>")
        if not latitude:
            latitude = "0"
        if not longitude:
            longitude = "0"
        # There is also COORDINATES which contains the same info, but w/extra 0 padding

        # UNDER MEDIA are alternate urls for HLS and SNAPSHOT

        itemDict = {
            "key": id,
            "url": url,
            "uuid": uuid,
            "name": name,
            "status": status,
            "address": address,
            "latitude": latitude,
            "longitude": longitude
        }
        dictList.append(itemDict)

    logger.info(f"Total IDs: {len(dictList)}")
    return dictList


# Extract substring bounded by two substrings
def _getStrBetween(inStr, lim1, lim2):
    startStr = inStr
    pos1 = inStr.find(lim1)
    if pos1 != -1:
        startStr = startStr[pos1+len(lim1):]
    else:
        if lim1.endswith(">"):
            pos1 = startStr.find(lim1[:-1])
            if pos1 == -1:
                return ""
            startStr = startStr[pos1+len(lim1[:-1]):]
            pos1 = startStr.find(">")
            if pos1 == -1:
                return ""
            startStr = startStr[pos1+1:]

    pos2 = startStr.find(lim2)
    if pos2 == -1:
        return ""
    
    return startStr[:pos2]


# Extract repeating occurences of substring bounded by two substrings
def _getRepeatingStrBetween(inStr, lim1, lim2):
    pos = 0
    rptStrs = []
    maxPos = len(inStr)
    while True:
        if pos >= maxPos:
            break
        wrkStr = inStr[pos:]
        nextStr = _getStrBetween(wrkStr, lim1, lim2)
        if not nextStr:
            break
        rptStrs.append(nextStr)
        pos1 = wrkStr.find(nextStr)
        pos = pos + pos1 + len(nextStr) + len(lim2)
    
    return rptStrs


def _allInputsValid(camSpec):
    if camSpec.get('key', "") == "":
        return False
    if camSpec.get('url', "") == "":
        return False
    if camSpec.get('uuid', "") == "":
        return False
    if camSpec.get('name', "") == "":
        return False
    if camSpec.get('status', "") == "":
        return False
    if camSpec.get('address', "") == "":
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
            logger.info(f"Invalid data on '{aCam}'; skipping")
            continue

        theID = str(aCam["key"])
        if theID in selection:
            theUuid = aCam["uuid"]
            theName = aCam["name"]
            theStatus = aCam["status"]
            theAddress = aCam["address"]
            theLatitude = aCam["latitude"]
            theLongitude = aCam["longitude"]

            if not theStatus:
                logger.info(f"Skipping; {theID} has status '{theStatus}'")
                continue

            # logger.info(f"Creating JSON file for ID:{theID}")
            configTemplate["deviceID"] = theID
            # configTemplate["accessUrl"] = ORIGIN + aCam["url"]
            # Above is original Camera URL, but we always construct from it the following:
            configTemplate["accessUrl"] = PLY_URL + theUuid
            configTemplate["longLat"] = [theLongitude, theLatitude]

            configTemplate["decoy"] = False
            if selection[theID] == "decoy" or selection[theID] == "monitor-decoy":
                configTemplate["decoy"] = True

            configTemplate["transcodeExt"] = None
            configTemplate["singleCollector"] = False
            # Note that we are setting concatenate to False above, since we are going singleCollector on the important ones
            if selection[theID] == "mp4" or selection[theID] == "monitor-mp4":
                configTemplate["transcodeExt"] = "mp4"
                configTemplate["singleCollector"] = True

            configTemplate["devNotes"]["uuid"] = theUuid
            configTemplate["devNotes"]["name"] = theName
            configTemplate["devNotes"]["address"] = theAddress

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
        , "collRegions": ["Europe (Stockholm)"]
        , "collectionType": "M3U"
        , "accessUrl": "SETLATER"
        , "pollFrequency": 45
        , "waitFraction": 0.75
        , "singleCollector": "SETLATER"
        , "concatenate": False
        , "transcodeExt": "SETLATER"
        , "transcodedBuffer": 0
        , "filenameBase": "{deviceID}"
        , "finalFileSuffix": "_{year}-{month}-{day}-{hour}-{mins}-{secs}"
        , "longLat": "SETLATER"
        , "bucketPrefixTemplate": "ru/is74/{deviceID}/{year}/{month}/{day}"
        , "headers": {
              "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0"
            , "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        	, "Accept-Encoding": "gzip, deflate"
            , "Accept-Language": "en-US,en;q=0.5"
            , "Connection": "keep-alive"
            , "Cache-Control" : "max-age=0"
            # , "DNT" : "1"
            # , 'Host' : ORIGIN.split('/')[2]
            # , 'Origin' : ORIGIN
            # , 'Referer' : ORIGIN + '/'
            # , 'Sec-Fetch-Dest' : 'empty'
            # , 'Sec-Fetch-Mode' : 'cors'
            # , 'Sec-Fetch-Site' : 'same-site'
            }
        , "devNotes": {
              "startedOn": "February 2024 in HP; overall Aug 30, 2021 under task Thorium"
            , "name": "SETLATER"
            , "uuid": "SETLATER"
            , "address": "SETLATER"
            , "setBy": "edward22"
            , "missionTLDN": "ru"
            }
        }
    return configTemplate


def _getDomainFolder(ap):
    countryDomain = ap["bucketPrefixTemplate"].split("/{deviceID}")[0]
    domainPrefix = f"{GLOBALS.deliveryKey}/{countryDomain}"
    return domainPrefix


def lambdaHandler(event, context):
    upSince = processInit.preFlightSetup()
    processInit.initSessionObject(config["sessionHeaders"])
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
    parser.add_argument(
        "-d",
        "--dont",
        required=False,
        action="store_true",
        help=(
            "don\'t create aimpoints\n"\
            "By default, it will create the aimpoints"
        )
    )
    args = parser.parse_args()

    upSince = processInit.preFlightSetup()
    processInit.initSessionObject(config['sessionHeaders'])
    if not processInit.initialize():
        logger.error("Failed to initialize")
        exit(1)

    # Create our ARN for later use
    # Don't use proxy for AWS metadata; will timeout if proxy is tried
    # $ export no_proxy=169.254.169.254
    os.environ["no_proxy"] = f"{os.environ['no_proxy']},169.254.169.254"
    region = ec2.region
    accountId = ec2.account_id
    instanceId = ec2.instance_id

    arn = f'arn:aws:ec2:{region}:{accountId}:instance/{instanceId}'
    GLOBALS.myArn = arn

    if args.dont:
        logger.info("Requested to NOT create aimpoints")
        execute(upSince, False)
    else:
        execute(upSince, True)

    nownow = int(time.time())
    logger.info(f'Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}')

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
