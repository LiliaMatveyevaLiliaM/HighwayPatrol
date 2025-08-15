"""
Module to create aimpoints.

Can be run as a stand-alone python script

The code will compare the current list of IDs (and other info) with a
"Master" list in the "metadata" folder on S3. If there is no master list,
one will be created and stored in the metadata folder.
A date-stamped version is also created and stored under this generator's
folder name in that same metadata folder.
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
DOMAIN = "baza.net"


def execute(upSince, forceCreate):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Parser"
    GLOBALS.subtaskName = "BazanetParser"

    mainUrl = "https://baza.net"
    idsSelectionFile = f"selected-{DOMAIN}.json"

    try:
        population = _getPopulation(mainUrl)
    except Exception as err:
        logger.exception(f"Error getting target list:::{err}")
        return False

    try:
        selection = hput.getSelection(idsSelectionFile)
        # logger.debug(f"selection={selection}")
    except HPatrolError as err:
        # logger.error(err)
        return False

    structTitles = (
          "ID"
        , "Title"
        , "Location"
        , "Location Title"
        , "Latitude"
        , "Longitude"
        , "Angle"
        , "Server"
        , "Token"
        )    
    structKeys = (
          "id"
        , "title"
        , "location"
        , "locationTitle"
        , "latitude"
        , "longitude"
        , "angle"
        , "server"
        , "token"
        )

    configTemplate = _getConfigTemplate()
    domainFolder = _getDomainFolder(configTemplate)

    shouldWriteAimpoints = False
    if forceCreate:
        shouldWriteAimpoints = True
    else:
        try:
            shouldWriteAimpoints = comp.writeAPs(
                upSince,
                population,
                (structKeys, structTitles),
                domainFolder,
                "rptBazanetMasterIdList",
                selectedList=selection)
        except HPatrolError:
            logger.exception("Unable to do ID comparison")
            return False

    if shouldWriteAimpoints:
        try:
            _doVideos(population, selection, configTemplate)
        except HPatrolError:
            return False

    return True


def _queryTargetSite(url):
    # First, touch the main site (https://baza.net/)
    try:
        throwAway = GLOBALS.netUtils.get(url, headers=config['sessionHeaders'])
    except:
        raise ConnectionError(f"URL access attempt failed for: {url}")

    ut.randomSleep(floor=10, ceiling=60)    # faking human interaction
    
    # Go to the cameras section (https://baza.net/camera)
    try:
        throwAway = GLOBALS.netUtils.get(f"{url}/camera", headers=config['sessionHeaders'])
    except:
        raise ConnectionError(f"URL access attempt failed for: {url}")

    # Grab the cams data (https://baza.net/users_app_api.php?method=public/list_of_city_cameras)
    try:
        resp = GLOBALS.netUtils.get(f"{url}/users_app_api.php?method=public/list_of_city_cameras", headers=config['sessionHeaders'])
    except:
        raise ConnectionError(f"URL access attempt failed for: {url}")

    # Retrieve the HTML text containing ID info
    return resp.text


def _getPopulation(url):
    logger.info("Getting target population")

    if GLOBALS.useTestData:
        mainPageForTesting = "testResources/listOfCityCameras.json"
        logger.debug(f"Reading from test file '{mainPageForTesting}'")
        with open(mainPageForTesting, 'r') as f:
            pageContent = f.read()
    else:
        pageContent = _queryTargetSite(url)

    try:
        devicesDict = json.loads(pageContent)
        logger.debug("Obtained and parsed population data")
    except Exception as err:
        logger.error(err)
        logger.debug(f"Content received is:\n{pageContent}")
        raise HPatrolError("Data error")

    popList = []
    citycams = devicesDict.get("citycam")
    for key in citycams.keys():
        # logger.debug(f"camID: {citycams[key].get('title')}")
        try:
            # Using two different access types cause we want to break for critical values
            # The most critical values are those used to form the URL
            aDict = {
                  "token": citycams[key]["dvr"]["token"]
                , "server": citycams[key]["dvr"]["server"]
                , "id": citycams[key]["dvr"]["camera_name"]
                , "title": citycams[key].get("title", "No Title")
                , "angle": str(citycams[key].get("marker").get("angle"))
                , "latitude": citycams[key].get("marker").get("latitude")
                , "longitude": citycams[key].get("marker").get("longitude")
                , "location": citycams[key].get("additional").get("location", "No location")
                , "locationTitle": citycams[key].get("additional").get("location_title", "No location")
            }
        except Exception as err:
            logger.warning(f"Keyword {err} not found while parsing: {citycams[key]}")
            continue

        popList.append(aDict)

    # logger.debug(f"population={popList}")
    logger.info(f"Total IDs: {len(popList)}")
    return popList


def _doVideos(allCamsDict, selection, apTemplate):
    theKey = f"{DOMAIN}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    counter = 1
    # Loop goes through the population, so not using enumerate() 
    # if we used enumerate(), we wouldn't go through the entire file
    for aCamDict in allCamsDict:
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and counter == 5:
            logger.debug(f"Not running on PROD; exiting at device #{counter}")
            break

        # camName is the official ID which is very long (e.g. "klubova.-.lunacharskogo-98f8a098d3")
        # so we shorten it on theID for better human referencing (e.g. "98f8a098d3")
        camName = aCamDict["id"]
        theID = camName.split("-")[-1]
        # logger.debug(f"camName:{camName}")
        if camName in selection:
            # Skip if the cam isn't selected
            if selection[camName] == "off":
                continue
            logger.info(f"Creating JSON file for ID:{theID}")

            if selection[camName] == "decoy" or selection[camName] == "monitor-decoy":
                apTemplate["decoy"] = True
            else:
                apTemplate["decoy"] = False

            apTemplate["transcodeExt"] = None
            if selection[camName] == "mp4" or selection[camName] == "monitor-mp4":
                apTemplate["transcodeExt"] = "mp4"

            # When used as "collectionType": "M3U" this is needed
            # But it's better to use "collectionType": "BAZNET" because the link is resolved 
            # on collection time instead of the generator time
            # theToken = aCamDict["token"]
            # theServer = aCamDict["server"]
            # urlTemplate = "https://{server}/{name}/index.fmp4.m3u8?token={token}"
            # apTemplate["accessUrl"] = urlTemplate.format(server=theServer, name=camName, token=theToken)

            theLat = aCamDict["latitude"]
            theLong = aCamDict["longitude"]

            apTemplate["deviceID"] = theID
            apTemplate["devNotes"]["cameraName"] = camName
            apTemplate["longLat"] = [float(theLong), float(theLat)]
            apTemplate["devNotes"]["location"] = aCamDict["location"]
            apTemplate["accessUrl"] = f"https://baza.net/camera/{camName}"

            outFile = os.path.join(config['workDirectory'], f"{theID}.json")
            try:
                ut.writeJsonDataToFile(apTemplate, outFile)
            except Exception as err:
                logger.exception(f"Error creating aimpoint file:::{err}")
                return False
            
            s3Dir = aimpointDir
            if selection[camName] in ["monitor", "monitor-mp4", "monitor-decoy"]:
                s3Dir = monitoredDir

            result = GLOBALS.S3utils.pushToS3(outFile,
                                    s3Dir,
                                    config['defaultWrkBucket'],
                                    s3BaseFileName=f"{theID}.json",
                                    deleteOrig=GLOBALS.onProd,
                                    extras={'ContentType': 'application/json'})
            counter += 1

    return True


def _getConfigTemplate():
    configTemplate = {
          "deviceID": "SETLATER"
        , "enabled": True
        , "decoy": "SETLATER"
        , "collRegions": ["United States (N. Virginia)"]
        , "vpn": "ru.hpatrol.dom:8080"
        , "pollFrequency": 24
        , "collectionType": "BAZNET"
        , "accessUrl": "SETLATER"
        , "concatenate": False
        , "transcodeExt": "SETLATER"
        , "longLat": "SETLATER"
        , "filenameBase": "{deviceID}"
        , "finalFileSuffix": "_{year}-{month}-{day}-{hour}-{mins}-{secs}"
        , "bucketPrefixTemplate": "ru/bazanet/{deviceID}/{year}/{month}/{day}"
        , "headers": {
              "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/88.0.4324.150 Safari/537.36"
            , "Connection": "keep-alive"
            , "Accept": "*/*"
            , "Sec-Fetch-Site": "same-site"
            , "Sec-Fetch-Mode": "cors"
            , "Sec-Fetch-Dest": "empty"    
            , "X-Originator": "DvrPlayer"
            , "Origin": "https://dvr.baza.net"
            , "Referer": "https://dvr.baza.net/"  
            , "Accept-Encoding": "gzip, deflate, br"
            , "Accept-Language": "en-US,en;q=0.9"
            , "DNT": "1"
            }
        , "devNotes": {
              "cameraName": "SETLATER"
            , "location": "SETLATER"
            , "startedOn": "May 2023"
            , "givenUrl": "https://baza.net/camera/"
            , "missionTLDN": "ru"
            , "setBy": "reynaldn"
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
        description="Aimpoint generator for videos",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "-f",
        "--force",
        required=False,
        action='store_true',
        help=(
            "force the creation of aimpoints"
        ),
    )
    args = parser.parse_args()

    upSince = processInit.preFlightSetup()
    processInit.initSessionObject(config['sessionHeaders'])
    if not processInit.initialize():
        logger.error("Failed to initialize")
        exit(1)

    # Don't use proxy for AWS metadata; will timeout if proxy is tried
    # $ export no_proxy=169.254.169.254
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

    if args.force:
        logger.info("Forcing aimpoints creation; won't execute comparitor")
        execute(upSince, True)
    else:
        execute(upSince, False)

    nownow = int(time.time())
    logger.info(f'Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}')

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
