"""
Module to create the aimpoints for the Cerium IDs

If run as a script, use the command line parameter:

STILLS - to generate the still image aimpoints

When you specify the above parameter, this will force the rewrite
of the STILLS aimpoints. In this case, there is no comparison with
the 'Master' list.

If you do not specify a parameter, this script will behave like the
lambda version. If run as a lambda, this code behaves as follows:

This code will compare the current list of IDs (and other info) with a
'Master' list in the 'metadata' folder on S3. If there is no master list
found, this code will create one and store it in the 'metadata' folder.
A date-stamped version is also created and stored under the 'cerium'
folder which is found in that same folder.

If an ID is added, deleted, or the imageURL for an ID is modified,
this script will also re-write the STILLS aimpoints.
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
THE_URL = "https://www.customs.gov.by/veb-kamery/"
IMG_PREFIX = "https://www.customs.gov.by"

MAIN_PAGE_XML_FILE = "ceriumMainPage.xml"
DOMAIN = "customs.gov.by"


def execute(upSince, isScript):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Parser"
    GLOBALS.subtaskName = "CeriumParser"

    # Get the URLs and other info for the critical IDs
    logger.info('Getting still image IDs dictionaries')
    try:
        imageIdsInfo = _getTargetList()
    except ConnectionError:
            return False

    if not imageIdsInfo:
        logger.exception("No image dictionaries returned")
        return False

    # Get the list of selected IDs
    try:
        selectionsFile = f"selected-{DOMAIN}.json"
        stillsSelection = hput.getSelection(selectionsFile)
    except HPatrolError as err:
        return False

    structTitles = (
          "ID"
        , "ImageURL"
        , "Name"
        , "Region"
        )
    structKeys = (
          "key"
        , "url"
        , "name"
        , "region"
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
        ceriumBucket = hput.pickBestBucket(configTemplate, "dstBucket")
        try:
            shouldWriteAimpoints = comp.writeAPs(
                    upSince,
                    imageIdsInfo,
                    (structKeys, structTitles),
                    domainFolder,
                    "rptCeriumMasterIdList",
                    bucketName=ceriumBucket,
                    selectedList=stillsSelection)
        except HPatrolError:
            logger.exception("Unable to do ID comparison")
            return False

    if shouldWriteAimpoints:
        try:
            _doStillCams(imageIdsInfo, stillsSelection, configTemplate)
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
            resp = GLOBALS.netUtils.get(THE_URL, headers=config['sessionHeaders'])
        except:
            raise ConnectionError(f"URL access attempt failed for: {THE_URL}") from None

        # Retrieve the HTML text containing ID info
        pageContent = resp.text

    # Extract the table from the page
    tableContent = _getStrBetween(pageContent, "<tbody>", "</tbody>")
    if not tableContent:
        logger.exception("No table content found")
        return None

    # Extract the rows from the table
    rowList = _getRepeatingStrBetween(tableContent, "<tr>", "</tr>")

    # We store the extracted info as a list of dictionaries
    dictList = []

    # Now extract the textual content we need from the rows
    region = ""
    names = []
    urls  = []
    counter = 0
    breakOuter = False
    for tRow in rowList:
        # If header, get the region
        if "<th" in tRow:
            region = _getStrBetween(tRow, "<u>", "</u>")
            if not region:
                logger.debug('A region was not found')
            continue
        
        # If URL present, get URLs
        if 'href="' in tRow:
            urls = _getRepeatingStrBetween(tRow, 'href="', '"')
            if not urls:
                logger.debug("Urls not found")

        # If we have region, names and urls, create and append tuples to list
        if region and names and urls:
            for nam, url in zip(names, urls):
                name = _finalCleanup(nam)
                id = _getStrBetween(url, "webcam/", ".jpg").replace("_", "-")
                if id.startswith("osh"):
                    id = id.upper()
                dictList.append({"key":id, "url": url, "name": name, "region": region})
                counter += 1
                # Don't go through everything if we're not on PROD
                if not GLOBALS.onProd and counter >= 4:
                    logger.debug(f"Not running on PROD; exiting at URL #{counter}")
                    breakOuter = True
                    break
            names = []
            urls  = []
            if not breakOuter:
                continue
            else:
                break

        # Otherwise, we have names
        names = _getRepeatingStrBetween(tRow, "<td>", "</td>")
        if not names:
            logger.debug("Names not found")

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
                return None
            startStr = startStr[pos1+len(lim1[:-1]):]
            pos1 = startStr.find(">")
            if pos1 == -1:
                return None
            startStr = startStr[pos1+1:]

    pos2 = startStr.find(lim2)
    if pos2 == -1:
        return None
    
    return startStr[:pos2]


# Extract repeating occurances of substring
# bounded by two substrings
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


# Remove any tags at beginning or ending of string
def _finalCleanup(inStr):
    wrkStr = inStr
    if wrkStr.startswith("<"):
        pos1 = wrkStr.find(">")
        if pos1 != -1:
            wrkStr = wrkStr[pos1+1:]

    if wrkStr.endswith(">"):
        pos2 = wrkStr.find("<")
        if pos2 != -1:
            wrkStr = wrkStr[:pos1]

    return wrkStr


def _allInputsValid(camSpec):
    if camSpec.get('key', "") == "":
        return False
    if camSpec.get('url', "") == "":
        return False
    if camSpec.get('name', "") == "":
        return False
    if camSpec.get('region', "") == "":
        return False

    return True


def _doStillCams(allCamsDict, selection, configTemplate):
    theKey = f"{DOMAIN}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    # Loop through the cams
    for aCam in allCamsDict:
        if not _allInputsValid(aCam):
            logger.info(f"Invalid data on '{aCam}'; continuing")
            continue

        theID = str(aCam["key"])
        if theID in selection:
            theImageUrl  = IMG_PREFIX + aCam["url"]
            theImageName = aCam["name"]
            theImageRegion = aCam["region"]

            logger.info(f"Creating JSON file for ID:{theID}")
            configTemplate["deviceID"] = theID
            configTemplate["accessUrl"] = theImageUrl
            if selection[theID] == "decoy" or selection[theID] == "monitor-decoy":
                configTemplate["decoy"] = True
            else:
                configTemplate["decoy"] = False

            configTemplate["devNotes"]["givenURL"] = THE_URL
            configTemplate["devNotes"]["region"] = theImageRegion
            configTemplate["devNotes"]["name"] = theImageName

            outFile = os.path.join(config["workDirectory"], f"{theID}.json")
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
                                    config["defaultWrkBucket"],
                                    s3BaseFileName=f"{theID}.json",
                                    deleteOrig=GLOBALS.onProd,
                                    extras={"ContentType": "application/json"})
    return True


def _getConfigTemplate():
    configTemplate = {
          "deviceID": "SETLATER"
        , "enabled": True
        , "decoy": "SETLATER"
        , "collRegions": ["Europe (Zurich)"]
        , "collectionType": "STILLS"
        , "accessUrl": "SETLATER"
        , "pollFrequency": 30
        , "hours": {
              "tz": "UTC"
            , "hrs": ["1000-1500"]
        }
        , "filenameBase": "cerium-{deviceID}"
        , "finalFileSuffix": "_{year}{month}{day}"
        , "dstBucket": "cerium-ch-prod-2"
        , "deliveryKey": "data"
        , "bucketPrefixTemplate": "customsgovby/{deviceID}/{year}/{month}"
        , "longLat": [37.6173, 55.7558]
        , "headers": {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0"
            , "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        	, "Accept-Encoding": "gzip, deflate"
            , "Accept-Language": "en-US,en;q=0.5"
            , "Cache-Control": "max-age=0"
            , "Connection": "keep-alive"
        }
        , "devNotes": {
              "givenURL": "https://www.customs.gov.by"
            , "startedOn": "November 2023"
            , "region": "SETLATER"
            , "name": "SETLATER"
            , "setBy": "edward22"
            , "missionTLDN": "by"
            , "freqNote": "Note working hours, along with a refresh rate of 30 seconds"
        }
    }
    return configTemplate


def _getDomainFolder(ap):
    domain = ap["bucketPrefixTemplate"].split("/")[0]
    deliveryKey = ap["deliveryKey"]
    return f"{deliveryKey}/{domain}"


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
        description='Aimpoint generator for stills',
        formatter_class=argparse.RawTextHelpFormatter
    )
    theChoices = ["stills"]
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

    # Create our ARN for later use
    # Don't use proxy for AWS metadata; will timeout if proxy is tried
    # $ export no_proxy=169.254.169.254
    os.environ["no_proxy"] = f"{os.environ['no_proxy']},169.254.169.254"
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
