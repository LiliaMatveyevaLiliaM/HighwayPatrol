"""
Module to create JSON aimpoints for the lanta.me site

Function retrieves the population of cameras, stores it as a JSON object,
then creates an aimpoint config for each selection.
"""

# External libraries import statements
import os
import re
import time
import json
import logging
import threading
import datetime as dt
from bs4 import BeautifulSoup


# This application's import statements
try:
    # Running on an EC2
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
    # Uses these modules when running in a Lambda
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
DOMAIN = "lanta.me"


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


def execute(upSince, writeAimpoints = False):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Parser"
    GLOBALS.subtaskName = "lantaMeParser"

    try:
        # This URL makes an API call retrieving a list of all URLs. This call is
        # made on the home page as well as each of the cameras.
        if (GLOBALS.useTestData):
            populationUrl = "getoverview.html"
        else: 
            # The site's API is out in the open, so we're actually able to ask
            # for all the site's cameras, but it returns HTML:
            populationUrl = "https://cam.lanta.me/cameras/getoverview"
        population = _getPopulation(populationUrl)

        selectionFile = f"selected-{DOMAIN}.json"
        selection = hput.getSelection(selectionFile)
        # logger.debug(f"Selection = {selection}")

    except HPatrolError as err:
        logger.error(f"HPatrolError: {err}")
        return False

    structTitles = (
          "ID"
        , "ID2"
        , "Title"
        , "Coordinates"
        , "Playlist URL"
        , "Preview Link"
        )
    structKeys = (
          "client_id"
        , "id"
        , "title"
        , "coords"
        , "link_video"
        , "link_preview"
        )

    configTemplate = _getConfigTemplate()
    domainFolder = _getDomainFolder(configTemplate)

    # Always run the comparitor - we've already hit the site for the current cam list
    try:
        logger.info("Running the comparitor")
        writeAimpoints = comp.writeAPs(
            upSince,
            population,
            (structKeys, structTitles),
            domainFolder,
            "lantaMeMasterIdList",
            selectedList=selection
        )
        if writeAimpoints:
            logger.info("Changes detected, will write new aimpoints")
    except HPatrolError as err:
        logger.error(f"HPatrolError: {err}")
        return False

    if writeAimpoints:
        _doVideos(population, selection, configTemplate)

    return True


def _getSelections(selectionFile):
    """Read a JSON file of cameras to generate aimpoints"""
    if GLOBALS.useTestData:
        selectionFile = f"testResources/{selectionFile}"
        try:
            logger.info(f"Reading test selection file '{selectionFile}'")
            with open(selectionFile, "r", encoding="utf-8") as f:
                respJson = json.load(f)
        except FileNotFoundError as err:
            logger.error(err)
            raise HPatrolError("No selected list of targets found")
    else: 
        idsS3FileAndPath = f"{GLOBALS.selectTrgts}/{selectionFile}"
        logger.info(f"Using Bucket: '{config['defaultWrkBucket']}'")
        logger.info(f'Reading selection file from bucket {idsS3FileAndPath}')
        respJson = GLOBALS.S3utils.readFileContent(config['defaultWrkBucket'], idsS3FileAndPath)
        respJson = json.loads(respJson)
        if not respJson:
            logger.error("No selected list of targets found")
            raise HPatrolError("No selected list of targets found")

    try:
        selections = respJson["selections"]
        logger.info(f"Total IDs in selection: {len(selections)}")
        return selections
    except KeyError: 
        logger.info("Error finding key 'selections', please check the selections JSON")
        logger.debug(f"JSON received: \n{respJson}")
        raise HPatrolError("Key not found in selections JSON")


def _getPopulation(anUrl):
    """ Get the entire population of possible devices.
        In test mode, read a file from the testResources directory
        otherwise, go to the URL.
        
        Below we're requesting the population URL representing a 
        Django API interface, from there we can get to HTML and JSON data. 
    """

    if GLOBALS.useTestData:
        testFile = f"testResources/{anUrl}"
        logger.info(f"Reading test population file: '{testFile}'")
        try:
            with open(testFile, "r", encoding="utf-8") as f:
                respText = _readResponse(f)
        except:
            logger.info(f"Error encountered reading the test resources")
            raise HPatrolError(f"Couldn't locate test resources in '{testFile}'")
    else:
        logger.info("Getting target population page")
        try:
            # Make calls to the endpoints the site normally makes instead of 
            # requesting a list of JSON objs from their API
            _navigateSite(anUrl)
            r = GLOBALS.netUtils.get(anUrl, headers=config["sessionHeaders"])
            # Instead of asking their API for JSON directly we're reading and 
            # parsing JSON from the HTML we receive
            respText = _readResponse(r.text)
        except Exception as e:
            logger.warning(f"URL access failed from {GLOBALS.perceivedIP} attempting {anUrl}")
            logger.error(f"Exception thrown:::{e}")
            raise HPatrolError(f"URL access failed from {GLOBALS.perceivedIP} attempting {anUrl}")

    if respText:
        population = respText
        logger.info(f"Total IDs in population: {len(population)}")
        return population
    else:
        logger.info("Population data not found; exiting")
        raise HPatrolError("Data not found")


def _readResponse(response):
    # We receive a response that looks like "getoverview.html" (in testResources)
    # Go through the soup and return an easy-to-use dictionary for the generator
    logger.info("Parsing response")

    # This parser isn't working - adapt it to the actual site
    soup = BeautifulSoup(response, "html.parser")
    rawJson = soup.text
    rawJson = rawJson.replace('\n', '')
    # polishedJson = rawJson[rawJson.find('Accept')+6:]

    return json.loads(rawJson)


def _navigateSite(anUrl):
    # Expecting to get https://cam.lanta.me/cameras/getoverview
    # Want to naturally find the content we're interested in
    try:
        GLOBALS.netUtils.get(anUrl[:anUrl.find("cameras")], headers=config["sessionHeaders"])
    except:
        logger.warning("Error navigating the webpage")


def _getHost(url):
    # Get metadata on the selected item based on the item's features
    # https://fl3.lanta.me:8443/90800/index.m3u8?token=d901cc237cc2782c9dcf58ccd5978127378d1766-5e7799de536f885c81c7c93cff2828e7-1738966386-1738955586
    urlPattern = r"(?P<host>https://.*\.me[^/]*)/.*"
    matches = re.search(urlPattern, url)

    # If a match is found, then return that, otherwise return an error that
    # no Host string was found
    if matches:
        matches = matches.groupdict()
        hostMatch = matches["host"]
    else:
        logger.info("Host match not found; returning default")
        logger.debug(f"Content received is:\n{url}")
        hostMatch = "https://fl3.lanta.me"

    return hostMatch


def _doVideos(camPopulation, selection, configTemplate):
    # S3 directory has the JSONs (this is the "prefix"), 
    # i.e. "<domain-parsed>" will be "<s3Dir>/<domain-parsed>/<camID>.json"
    theKey = f"{DOMAIN}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    counter = 1
    for camera in camPopulation:
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and counter == 3:
            logger.debug(f"Not running on PROD; exiting early at device #{counter}")
            break

        camID = str(camera["client_id"])
        camFilename = f"{camID}.json"
        if camID in selection:
            # Skip all this if the cam isn't selected
            if selection[camID] == "off":
                continue

            logger.info(f"Creating JSON file for ID #{camID}")
            configTemplate = _createAimpointConfig(configTemplate, selection, camera)

            outFile = os.path.join(config["workDirectory"], camFilename)
            # logger.info(f"Writing JSON to {outFile}")
            try:
                ut.writeJsonDataToFile(configTemplate, outFile)
            except Exception as error:
                logger.exception(f"Error creating aimpoint file:::{error}")
                logger.debug(f"JSON received:\n {camera}")

            s3Dir = aimpointDir
            if selection[camID] in ["monitor", "monitor-mp4", "monitor-decoy"]:
                s3Dir = monitoredDir

            GLOBALS.S3utils.pushToS3(
                outFile,
                s3Dir,
                config["defaultWrkBucket"],
                s3BaseFileName=camFilename,
                deleteOrig=GLOBALS.onProd,
                extras={"ContentType": "application/json"}
            )
            counter += 1


def _createAimpointConfig(configTemplate, selection, data):
    """
    Create the config from the base template
    """
    configTemplate["deviceID"] = data["client_id"]
    if selection[str(data["client_id"])] == "decoy":
        configTemplate["decoy"] = True
    else:
        configTemplate["decoy"] = False

    configTemplate["accessUrl"] = data["link_video"]
    # Assign the coordinate values and then put them into the
    # coordinate array on the aimpoint JSON
    latitude = data["coords"][0]
    longitude = data["coords"][1]
    configTemplate["longLat"] = [longitude, latitude]
    configTemplate["headers"]["Referer"] = _getHost(data["link_video"])

    return configTemplate


def _getConfigTemplate():
    configTemplate = {
          "deviceID": ""  # SETLATER
        , "enabled": True
        , "decoy": ""  # SETLATER
        , "collRegions": ["Europe (Frankfurt)"]
        , "collectionType": "M3U"
        , "accessUrl": ""  # SETLATER
        , "pollFrequency": 30
        , "concatenate": False
        , "transcodeExt": "mp4"
        , "longLat": ""  # SETLATER
        , "filenameBase": "{deviceID}"
        , "finalFileSuffix": "_{year}-{month}-{day}-{hour}-{mins}-{secs}"
        , "bucketPrefixTemplate": "ru/lanta.me/{deviceID}/{year}/{month}/{day}"
        , "headers": {
              "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0"
            , "Accept": "*/*"
            , "Referer": ""  # SETLATER
            , "Sec-Fetch-Site": "none"
            , "Sec-Fetch-Mode": "navigate"
            , "Connection": "keep-alive"
            , "Accept-Language": "en-US,en;q=0.9"
            , "Sec-Fetch-Dest": "document"
            , "Origin": "cam.lanta.me"
            , "Accept-Encoding": "gzip, deflate, br"
            , "DNT": "1"
        }
        , "devNotes": {
              "givenUrl": "https://cam.lanta.me"
            , "startedOn": "02.06.25"
            , "missionTLDN": "ru"
            , "setBy": "paul01"
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

    try:
        os.environ["no_proxy"] = f"{os.environ['no_proxy']},169.254.169.254"
    except KeyError:
        os.environ["no_proxy"] = "169.254.169.254"

    region = ec2.region
    accountId = ec2.account_id
    instanceId = ec2.instance_id
    arn = f"arn:aws:ec2:{region}:{accountId}:instance/{instanceId}"
    GLOBALS.myArn = arn

    execute(upSince, writeAimpoints=True)

    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")
    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
