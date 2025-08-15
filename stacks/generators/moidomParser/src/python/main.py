"""
Note that many things are hardcoded because this handles a specific domain

Can use these command line parameters when run as a script
    STILLS - to generate the still image aimpoints
    VIDEOS - to generate the video aimpoints
    BOTH - to generate both

When you specify any of the above parameters, this will force the
rewrite of the aimpoints of that type (whether STILLS, VIDEOS or
BOTH). In this case, there is no comparison with the master list.

If you do not specify a parameter, this script will behave like the
lambda version. If run as a lambda, the code behaves as follows:

This code will compare the current list of IDs (and other info) with a
master list in the metadata folder on S3. If there is no master list,
it will create one and store it in the metadata folder.

For still images, if an ID is added, deleted, or the URL is modified,
this script will also re-write the aimpoints. 
For videos, if an ID is added, deleted, or the URL is modified,
this script will also re-write the aimpoints, provided
one of the IDs in the selections file was affected.
"""

# External libraries import statements
import os
import time
import json
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
BOTH = "both"
STILLS = "stills"
VIDEOS = "videos"
DOMAIN = "moidom-stream.ru_Videos"


def execute(argVal, upSince, isScript):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Parser"
    GLOBALS.subtaskName = "moidomStreamParser"

    mainUrl = "https://moidom.citylink.pro"
    idsSelectionFile = f"selected-{DOMAIN}.json"

    try:
        population = _getPopulation(mainUrl)
    except HPatrolError as err:
        return False
    if not population:
        logger.error("No population found")
        return False

    # If running as a script, comparison is NOT done but aimpoints creation is
    # If run as a lambda, comparison *is* done, and the master file and the
    # aimpoint files are re-created as necessary

    # Get the list of selected IDs - needed for VIDEOS. This generator is
    # mostly run as a Lambda, which always processes BOTH.
    try:
        videosSelection = hput.getSelection(idsSelectionFile)
    except HPatrolError as err:
        return False

    # Do we need to re-write STILLS, VIDEOS or BOTH?
    writeStills = False
    writeVideos = False
    if isScript:
        if argVal == STILLS or argVal == BOTH:
            writeStills = True
        if argVal == VIDEOS or argVal == BOTH:
            writeVideos = True
    else:
        # Handle the STILLS first - no selectedList as all IDs are processed
        structTitles = (
              "ID"
            , "City"
            , "Name"
            , "ImageURL"
            , "Longitude"
            , "Latitude"
            )
        structKeys = (
              "id"
            , "cityName"
            , "name"
            , "imageUrl"
            , "longitude"
            , "latitude"
            )
        stillsConfigTemplate = _getStillsConfigTemplate()
        stillsDomainFolder = _getStillsDomainFolder(stillsConfigTemplate)
        try:
            writeStills = comp.writeAPs(
                    upSince,
                    population,
                    (structKeys, structTitles),
                    stillsDomainFolder,
                    "rptMoidomStillsMasterIdList")
        except HPatrolError:
            logger.exception("Unable to do ID comparison for STILLS")
            return False

        # Now handle the VIDEOS - use videosSelection list
        structTitles = (
              "ID"
            , "City"
            , "Name"
            , "HasAudio"
            , "PlaylistURL"
            , "Longitude"
            , "Latitude"
            )
        structKeys = (
              "id"
            , "cityName"
            , "name"
            , "audio"
            , "videoUrl"
            , "longitude"
            , "latitude"
            )
        videosConfigTemplate = _getVideosConfigTemplate()
        vidsDomainFolder = _getVidsDomainFolder(videosConfigTemplate)
        try:
            writeVideos = comp.writeAPs(
                    upSince,
                    population,
                    (structKeys, structTitles),
                    vidsDomainFolder,
                    "rptMoidomVideosMasterIdList",
                    selectedList=videosSelection)
        except HPatrolError:
            logger.exception("Unable to do ID comparison for VIDEOS")
            return False

    if writeStills:
        try:
            _doStillCams(population, stillsConfigTemplate)
        except HPatrolError as err:
            return False

    if writeVideos:
        try:
            _doVideoCams(population, videosSelection, videosConfigTemplate)
        except HPatrolError as err:
            return False

    return True


def _getCitiesUrls(url):
    # First, touch the main site (https://moidom.citylink.pro)
    if not GLOBALS.useTestData:
        # Only do this if we're actually going out on the net
        try:
            throwAway = GLOBALS.netUtils.get(url, headers=config["sessionHeaders"])
        except:
            raise ConnectionError(f"URL access attempt failed for: {url}")
        ut.randomSleep(floor=10, ceiling=60)    # faking human interaction

    # Go to the cameras section (https://moidom.citylink.pro/web/api/cities/)
    try:
        if GLOBALS.useTestData:
            testFile = f"testResources/apiCities.json"
            logger.debug(f"Reading from test file '{testFile}'")
            with open(testFile, 'r') as f:
                citiesJson = f.read()
        else:
            resp = GLOBALS.netUtils.get(f"{url}/web/api/cities/", headers=config["sessionHeaders"])
            citiesJson = resp.text
    except:
        raise ConnectionError(f"URL access attempt failed for: {url}")

    allCities = json.loads(citiesJson)
    theUrl = "https://moidom.citylink.pro/web/api/public_cameras/{key}?"
    citiesUrls = {}
    for aCity in allCities:
        logger.info(f"Composing URL for '{aCity['key']}': {aCity['name']}")
        # Notice we are using "key" instead of "public_key"
        # Requestor asked for no underscores on the ID
        modifiedKey = aCity["key"].replace("_", "-")
        citiesUrls[modifiedKey] = theUrl.format(key=aCity["key"])
    
    # logger.debug(f"citiesUrls:\n{citiesUrls}")
    return citiesUrls


def _getPopulation(url):
    logger.info("Getting target population")
    allCitiesDict = _getCitiesUrls(url)

    camsDictList = []
    if GLOBALS.useTestData:
        testFile = "testResources/moidomPetrozavodskRegion.json"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(testFile, 'r') as f:
            cityRespText = f.read()
        camsList = json.loads(cityRespText)

        try:
            camsDictList.extend(_extractValues("ptz", camsList))
        except HPatrolError:
            pass

    else:
        for idx, cityKey in enumerate(allCitiesDict, start=1):
            # Don't go through everything if we're not on PROD
            if not GLOBALS.onProd and idx == 4:
                logger.debug(f"Not running on PROD; exiting at region #{idx}")
                break

            cityUrl = allCitiesDict[cityKey]
            ut.randomSleep(floor=2, ceiling=4)

            try:
                resp = GLOBALS.netUtils.get(cityUrl, headers=config["sessionHeaders"])
            except:
                logger.warning(f"URL access failed for: {cityUrl}")
                continue
            cityRespText = resp.text

            try:
                camsList = json.loads(cityRespText)
                # logger.debug(json.dumps(camsList))
            except Exception:
                logger.debug(f"Content received is:\n{cityRespText}")
                continue

            try:
                camsDictList.extend(_extractValues(cityKey, camsList))
            except HPatrolError:
                pass

    logger.info(f"Total IDs in all cities: {len(camsDictList)}")
    return camsDictList


def _extractValues(cityKey, tgtList):
    logger.info(f"Getting values for '{cityKey}'")

    camsList = []
    for aCam in tgtList:
        try:
            id = f"{aCam['id']}-{cityKey}"
            # logger.debug(f"******id {id}")
        except (KeyError, TypeError):
            logger.info(f"Invalid data on '{aCam}'; continuing")
            continue

        try:
            cityName = aCam["city"]["name"]
        except KeyError:
            cityName = "None"

        try:
            name = aCam["name"]
            if name == "":
                name = "None"
        except KeyError:
            name = "None"

        try:
            imageUrl = aCam["img"].split('?')[0]
        except KeyError:
            continue

        try:
            videoUrl = aCam["src"]
        except KeyError:
            continue

        try:
            audio = aCam["audio"]
        except KeyError:
            audio = None

        try:
            longitude = str(aCam["longitude"])
        except KeyError:
            longitude = "0"

        try:
            latitude = str(aCam["latitude"])
        except KeyError:
            latitude = "0"

        camDict = {
            "id": id,
            "cityName": cityName,
            "name": name,
            "imageUrl": imageUrl,
            "videoUrl": videoUrl,
            "audio": audio,
            "longitude": longitude,
            "latitude": latitude
        }
        camsList.append(camDict)

    if not camsList:
        # If in the end, there are no cams to be had
        logger.warning("No devices found")
        raise HPatrolError("No devices found")
    return camsList


def _doStillCams(allCamsDict, configTemplate):
    s3Dir = f"{GLOBALS.targetFiles}/moidom-stream.ru_Stills-autoParsed"

    GLOBALS.S3utils.deleteEntireKey(config['defaultWrkBucket'], s3Dir)

    # Loop through the cams
    for idx, aCamDict in enumerate(allCamsDict, start=1):
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and idx == 6:
            logger.debug(f"Not running on PROD; exiting at population device #{idx}")
            break

        theID = aCamDict["id"]
        theImageUrl  = aCamDict["imageUrl"]
        theImageLat  = aCamDict["latitude"]
        theImageLong = aCamDict["longitude"]

        logger.info(f"Creating JSON file for ID:{theID}")
        configTemplate["deviceID"] = theID
        configTemplate["longLat"] = [float(theImageLong), float(theImageLat)]
        configTemplate["accessUrl"] = theImageUrl

        outFile = os.path.join(config['workDirectory'], f"{theID}.json")
        try:
            ut.writeJsonDataToFile(configTemplate, outFile)
        except Exception as err:
            logger.exception(f"Error creating aimpoint file:::{err}")
            return False

        result = GLOBALS.S3utils.pushToS3(outFile,
                                s3Dir,
                                config['defaultWrkBucket'],
                                s3BaseFileName=f"{theID}.json",
                                deleteOrig=GLOBALS.onProd,
                                extras={'ContentType': 'application/json'})
    return True


def _doVideoCams(allCamsDict, selection, configTemplate):
    theKey = f"{DOMAIN}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    # Loop through the cams
    for idx, aCamDict in enumerate(allCamsDict, start=1):
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and idx == 6:
            logger.debug(f"Not running on PROD; exiting at population device #{idx}")
            break

        theID = aCamDict["id"]
        if theID in selection:
            theImageName = aCamDict["name"]
            theImageLat  = aCamDict["latitude"]
            theManifestUrl = aCamDict["videoUrl"]
            theImageLong = aCamDict["longitude"]

            logger.info(f"Creating JSON file for ID:{theID}")
            configTemplate["deviceID"] = theID
            configTemplate["accessUrl"] = theManifestUrl
            configTemplate["longLat"] = [float(theImageLong), float(theImageLat)]
            if selection[theID] == "decoy" or selection[theID] == "monitor-decoy":
                configTemplate["decoy"] = True
            else:
                configTemplate["decoy"] = False

            configTemplate["transcodeExt"] = None
            if selection[theID] == "mp4" or selection[theID] == "monitor-mp4":
                configTemplate["transcodeExt"] = "mp4"

            configTemplate["devNotes"]["name"] = theImageName

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


def _getStillsConfigTemplate():
    stillsConfigTemplate = {
          "deviceID": "SETLATER"
        , "enabled": True
        , "decoy": False
        , "collRegions": ["Europe (Stockholm)"]
        , "collectionType": "STILLS"
        , "accessUrl": "SETLATER"
        , "pollFrequency": 600
        , "filenameBase": "moidomStream-{deviceID}"
        , "finalFileSuffix": "_{year}-{month}-{day}"
        , "bucketPrefixTemplate": "ru/moidomStreamStills/{year}/{month}/{deviceID}"
        , "longLat": "SETLATER"
        , "headers": {
              "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0"
            , "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        	, "Accept-Encoding": "gzip, deflate"
            , "Accept-Language": "en-US,en;q=0.5"
            , "Cache-Control": "max-age=0"
            , "Connection": "keep-alive"
        }
        , "devNotes": {
              "givenURL": "https://moidom.citylink.pro/pz"
            , "startedOn": "November 2022"
            , "name": "SETLATER"
            , "setBy": "edward22"
            , "missionTLDN": "ru"
        }
    }
    return stillsConfigTemplate


def _getVideosConfigTemplate():
    videosConfigTemplate = {
          "deviceID": "SETLATER"
        , "enabled": True
        , "decoy": "SETLATER"
        , "collRegions": ["Europe (Stockholm)"]
        , "collectionType": "M3U"
        , "accessUrl": "SETLATER"
        , "pollFrequency": 30
        , "waitFraction": 0.6
        , "singleCollector": True
        , "concatenate": False
        , "transcodeExt": "SETLATER"
        , "filenameBase": "{deviceID}"
        , "finalFileSuffix": "_{year}-{month}-{day}-{hour}-{mins}-{secs}"
        , "bucketPrefixTemplate": "ru/moidomStreamVids/{deviceID}/{year}/{month}/{day}"
        , "longLat": "SETLATER"
        , "headers": {
              "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0"
            , "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        	, "Accept-Encoding": "gzip, deflate"
            , "Accept-Language": "en-US,en;q=0.5"
            , "Cache-Control": "max-age=0"
            , "Connection": "keep-alive"
            }
        , "devNotes": {
              "givenURL": "https://moidom.karelia.pro"
            , "startedOn": "November 2022"
            , "name": "SETLATER"
            , "setBy": "edward22"
            , "missionTLDN": "ru"
            }
        }
    return videosConfigTemplate


def _getStillsDomainFolder(ap):
    countryDomain = ap["bucketPrefixTemplate"].split("/{year}")[0]
    domainPrefix = f"{GLOBALS.deliveryKey}/{countryDomain}"
    return domainPrefix


def _getVidsDomainFolder(ap):
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
        if execute("", upSince, False):
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
        description='Aimpoint generator for videos or stills',
        formatter_class=argparse.RawTextHelpFormatter
    )

    theChoices = [STILLS, VIDEOS, BOTH]
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
        execute(argVal, upSince, True)
    else:
        execute("", upSince, False)

    nownow = int(time.time())
    logger.info(f'Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}')

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
