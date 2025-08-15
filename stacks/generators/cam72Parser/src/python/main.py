"""
Module to create the JSON aimpoints for the cam72.su site

Function retrieves the main page, parses it and creates subsequent JSON files

Can be run as a stand-alone python script
"""

# External libraries import statements
import os
import re
import time
import logging
import threading
import datetime as dt
from bs4 import BeautifulSoup


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
    from utils import hPatrolUtils as hput
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
    from src.python.utils import hPatrolUtils as hput
    from src.python.orangeUtils.auditUtils import AuditLogLevel


logger = logging.getLogger()


# Constants
DOMAIN = "cam72.su"
START_URL = "https://im72.su/cam"


def lambdaHandler(event, context):
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
        if execute():
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


def execute():
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Parser"
    GLOBALS.subtaskName = "Cam72Parser"

    try:
        idsSelectionFile = f"selected-{DOMAIN}.json"
        selection = hput.getSelection(idsSelectionFile)
        idTokenMap = _getTokens(selection)
    except HPatrolError as err:
        logger.exception(f"Error getting token:::{err}")
        return False

    # TODO: Add comparitor metadata reporting

    logger.info("Processing for videos")
    try:
        _doVideos(idTokenMap, selection)
    except HPatrolError:
        return False

    return True


def _doVideos(idTokenMap: dict, selection: dict) -> None:
    """Create aimpoints from the selections file"""

    theKey = f"{DOMAIN}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    # logger.info(f"Found {len(selection)} selected devices!")
    for aSel, theToken in idTokenMap.items():
        configTemplate = {
              "deviceID": aSel
            , "enabled": "SETONMERGEBELOW"
            , "decoy": "SETONMERGEBELOW"
            , "collRegions": ["United States (N. Virginia)"]
            , "vpn": "az.hpatrol.dom:8080"
            , "collectionType": "M3U"
            , "accessUrl": f"https://streams.cam72.su/{aSel}-72/tracks-v1/index.fmp4.m3u8?token={theToken}"
            , "pollFrequency": 32
            # On 02.22.24 we tried pollFrequency at 30 w/multiCollectors but saw 6s holes
            # On 06.12.24 had 24s pollFrequency saw 12s holes; target is now at 8s (4segments of 2s)
            # On 02.25.25 target is now at 32s (4segments of 8s)
            , "concatenate": False
            , "transcodeExt": "mp4"
            , "filenameBase": "{deviceID}"
            , "finalFileSuffix": "_{year}-{month}-{day}-{hour}-{mins}-{secs}"
            # Note that for GPS coordinates, we are using a generic Tyumen location
            # As of 09.26.22 these devices don't report coordinates
            , "longLat": [57.1553, 65.5619]
            , "bucketPrefixTemplate": f"ru/cam72su/{aSel}/{{year}}/{{month}}/{{day}}"
            , "headers": {
                  "Host": "streams.cam72.su"
                , "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"
                , "Accept": "*/*"
                , "Accept-Language": "en-US,en;q=0.5"
                , "Accept-Encoding": "gzip, deflate, br, zstd"
                , "Sec-Fetch-Dest": "empty"
                , "Sec-Fetch-Mode": "cors"
                , "Sec-Fetch-Site": "same-origin"
                , "Connection": "keep-alive"
                , "DNT": "1"
                , "Sec-GPC": "1"
                , "Referer": f"https://streams.cam72.su/1500-1039-72/embed.html?token={theToken}"
            }
            , "devNotes": {
                  "givenURL": "https://cam72.su"
                , "startedOn": "September 2022"
                , "missionTLDN": "ru"
                , "setBy": "reynaldn"
                , "notes": "From automatic parser; GPS coordinates are a generic Tyumen location"
            }
        }

        newAimpoint = hput.mergeSelections(
            selected=selection[aSel], baseTemplate=configTemplate
        )
        logger.info(f"Created aimpoint for ID: {newAimpoint['deviceID']}")

        s3Dir = aimpointDir
        if selection[aSel] in ["monitor", "monitor-mp4", "monitor-decoy"]:
            s3Dir = monitoredDir

        hput.pushAimpointToS3(newAimpoint, s3Dir)


def _getTokens(ids, startUrl=START_URL):
    idTokenMap = {}
    page = 1
    if GLOBALS.useTestData:
        testFile = "testResources/im72.su.html"
        # 2025.07.17 Site changed; previous site here in testResources
        # Keeping for historical research; delete if not useful anymore
        # testFile = "testResources/cam72.su.html"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(testFile, "r") as f:
            pageContent = f.read()
    else:
        logger.info(f"Getting page for token identification '{startUrl}'")
        try:
            reqHeaders = config["sessionHeaders"]
            reqHeaders.update({
                  "Host": "im72.su"
                , "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                , "DNT": "1"
                , "Sec-GPC": "1"
                , "Connection": "keep-alive"
                , "Upgrade-Insecure-Requests": "1"
                , "Sec-Fetch-Dest": "document"
                , "Sec-Fetch-Mode": "navigate"
                , "Sec-Fetch-Site": "none"
                , "Sec-Fetch-User": "?1"
                , "Priority": "u=0, i"
            })
            r = GLOBALS.netUtils.get(startUrl, headers=reqHeaders)
        except:
            raise ConnectionError(
                f"URL access failed from {GLOBALS.perceivedIP} attempting {startUrl}"
            ) from None
        pageContent = r.text

    soup = BeautifulSoup(pageContent, 'html.parser')
    lastPage = parsePagination(soup)
    streamDivs = soup.find_all('div', {'class': 'stream', 'onclick': True})
    if not streamDivs:
        if not idTokenMap:
            raise HPatrolError(f"No tokens found for selected IDs: {ids}")
        return idTokenMap

    for div in streamDivs:
        onclick = div['onclick']
        pattern = r"\((.*)\)"
        match = re.search(pattern, onclick)
        onclickArgs = match.group(1)
        argsList = onclickArgs.split(",")
        camId = argsList[0].strip("'")
        token = argsList[2].strip("'")
        if camId not in ids.keys():
            continue
        if camId not in idTokenMap:
            idTokenMap[camId] = token

    page += 1
    # Only one page of test data
    if GLOBALS.useTestData:
        return idTokenMap
    
    while page <= lastPage:
        # If we found all the tokens we need, return
        if len(idTokenMap) == len(ids):
            return idTokenMap

        pageUrl = f"{startUrl}/?v={(page-1)*10}"
        logger.info(f"Getting page for token identification '{pageUrl}'")
        # Pause to reduce risk of getting blocked
        ut.randomSleep(floor=2, ceiling=10)
        try:
            r = GLOBALS.netUtils.get(pageUrl, headers=reqHeaders)
        except:
            raise ConnectionError(
                f"URL access failed from {GLOBALS.perceivedIP} attempting {pageUrl}"
            ) from None
        pageContent = r.text
        soup = BeautifulSoup(pageContent, 'html.parser')

        streamDivs = soup.find_all('div', {'class': 'stream', 'onclick': True})
        if not streamDivs:
            if not idTokenMap:
                raise HPatrolError(f"No tokens found for selected IDs: {ids}")
            break
        for div in streamDivs:
            onclick = div['onclick']
            pattern = r"\((.*)\)"
            match = re.search(pattern, onclick)
            onclickArgs = match.group(1)
            argsList = onclickArgs.split(",")
            camId = argsList[0].strip("'")
            token = argsList[2].strip("'")
            if camId not in ids.keys():
                continue
            if camId not in idTokenMap:
                idTokenMap[camId] = token
        page += 1

    return idTokenMap


def parsePagination(html):
    menuBlocksDiv = html.find_all('div', class_="menuBLOCK")

    if menuBlocksDiv:
        paginationDiv = menuBlocksDiv[1] if len(menuBlocksDiv) > 1 else menuBlocksDiv[0]
    else:
        return 1

    pages = []
    for page in paginationDiv.find_all('u'):
        try:
            num = int(page.get_text())
            pages.append(num)
        except ValueError:
            continue
    if pages:
        return max(pages)
    else:
        return 1


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

    execute()

    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
