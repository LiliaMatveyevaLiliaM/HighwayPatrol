"""
Aimpoint generator
Many things are hardcoded because this handles a specific domain

"""

# External libraries import statements
import os
import time
import json
import logging
import argparse
import datetime
import threading
from bs4 import BeautifulSoup


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
DOMAIN = "rdtc"


def lambdaHandler(event: dict, context: dict) -> dict:
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
        if execute(upSince):
            trueOrFalse = True
            exitMessage = "Normal Execution"

    except Exception as e:
        logger.exception(f"UNHANDLED EXCEPTION CAUGHT:::{e}")
        systemLevel = AuditLogLevel.CRITICAL
        trueOrFalse = False
        dataLevel = None

    finally:
        nownow = int(time.time())
        logger.info(f"Process clocked at {str(datetime.timedelta(seconds=nownow-upSince))}")
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
            enterDatetime=datetime.datetime.fromtimestamp(upSince),
            leaveDatetime=datetime.datetime.fromtimestamp(nownow)
        )

    toPrint = "Exiting Process"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")

    return {"status": trueOrFalse}


def _getPopulation():
    rdtcBaseUrl = "https://cam.hutor.ru/"
    logger.info("Getting target population")

    if GLOBALS.useTestData:
        testFile = "testResources/rdtc.html"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(testFile, "r") as f:
            pageContent = f.read()
    else:
        logger.info(f"Getting page '{rdtcBaseUrl}'")
        try:
            r = GLOBALS.netUtils.get(rdtcBaseUrl, headers=config["sessionHeaders"])
        except:
            raise HPatrolError(
                f"URL access failed from {GLOBALS.perceivedIP} attempting {rdtcBaseUrl}"
            )
        pageContent = r.text
        
    soup = BeautifulSoup(pageContent, "html.parser")
    cams = soup.find_all("td", class_="layout-table__cell_camLIst")
    camList = []
    for cam in cams:
        link = cam.find("a", class_="camList__link")
        if link:
            camId = link["href"].split("=")[1]  
            location = link.get_text(strip=True)  
            url = f"{rdtcBaseUrl}{link['href']}"  
            camList.append({
                "id": camId,
                "url": url,
                "location": location
            })
    jsonCamData = json.dumps(camList, ensure_ascii=False, indent=4)
    logger.info("Obtained camera JSON data")
    return jsonCamData


def _doVideos(allCams, selection, configTemplate):
    theKey = f"{DOMAIN}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    counter = 1
    for cam in allCams:
        if not GLOBALS.onProd and counter == 5:
            logger.debug(f"Not running on PROD; exiting at device #{counter}")
            break

        camId = str(cam["id"])
        if camId in selection:
            if selection[camId] == "off":
                continue

            logger.info(f"Creating JSON file for ID: {camId}")
            configTemplate["deviceID"] = camId
            accessUrl = f"https://ipcam.rdtc.ru/ipcam/ipcam_{camId}/index.fmp4.m3u8"
            refererUrl = f"https://ipcam.rdtc.ru/ipcam/ipcam_{camId}/embed.html?dvr=false"
            configTemplate["accessUrl"] = accessUrl
            configTemplate["headers"]["Referer"] = refererUrl
            if selection[camId] == "mp4" or selection[camId] == "monitor-mp4":
                configTemplate["transcodeExt"] = "mp4"
            if selection[camId] == "decoy" or selection[camId] == "monitor-decoy":
                configTemplate["decoy"] = True
            else:
                configTemplate["decoy"] = False

            outFile = os.path.join(config["workDirectory"], f"{camId}.json")
            try:
                ut.writeJsonDataToFile(configTemplate, outFile)
            except Exception as err:
                logger.exception(f"Error creating aimpoint file:::{err}")
                continue

            s3Dir = aimpointDir
            if selection[camId] in ["monitor", "monitor-mp4", "monitor-decoy"]:
                s3Dir = monitoredDir

            GLOBALS.S3utils.pushToS3(
                outFile,
                s3Dir,
                config["defaultWrkBucket"],
                deleteOrig=GLOBALS.onProd,
                s3BaseFileName=f"{camId}.json",
                extras={"ContentType": "application/json"}
            )
            counter += 1


def execute(upSince: int) -> bool:
    GLOBALS.taskName = "Parser"
    GLOBALS.subtaskName = "IpcamRdtcParser"
    try:
        rdtcCams = _getPopulation()
    except HPatrolError as err:
        logger.exception(f"Error getting target population:::{err}")
        return False
    camsSelectedFile = f"selected-{DOMAIN}.json"
    try:
        selection = hput.getSelection(camsSelectedFile)
    except HPatrolError:
        return False
    
    structTitles = ("ID"
                    , "Url"
                    , "Location")
    structKeys = ("id"
                    , "url"
                    , "location")
    configTemplate = _getConfigTemplate()
    domainFolder = _getDomainFolder(configTemplate)
    try:
        comp.writeAPs(
            upSince,
            json.loads(rdtcCams),
            (structKeys, structTitles),
            domainFolder,
            "rptRdtcMasterIdList",
            selectedList=selection
        )
    except HPatrolError:
        logger.exception("Unable to do ID comparison")
    try:
        _doVideos(json.loads(rdtcCams), selection, configTemplate)
    except HPatrolError:
        return False
    return True


def _getConfigTemplate():
    configTemplate = {
          "deviceID": "SETLATER"
        , "enabled": True
        , "decoy": "SETLATER"
        , "collRegions": ["Europe (Frankfurt)"]
        , "collectionType": "M3U"
        , "accessUrl": "SETLATER"
        , "pollFrequency": 20
        , "concatenate": False
        , "transcodeExt": "SETLATER"
        , "longLat": [0, 0]
        , "filenameBase": "rdtc{deviceID}"
        , "finalFileSuffix": "_{year}-{month}-{day}-{hour}-{mins}-{secs}"
        , "bucketPrefixTemplate": "ru/ipcamRdtc/rdtc{deviceID}/{year}/{month}/{day}"
        , "headers": {
              "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0"
            , "Accept": "*/*"
            , "Host": "ipcam.rdtc.ru"
            , "Sec-Fetch-Site": "none"
            , "Sec-Fetch-Mode": "navigate"
            , "Connection": "keep-alive"
            , "Accept-Language": "en-US,en;q=0.9"
            , "Sec-Fetch-Dest": "empty"
            , "Sec-Fetch-Mode": "cors"
            , "Sec-Fetch-Site": "same-origin"
            , "Referer": "SETLATER"
            , "Accept-Encoding": "gzip, deflate, br"
            , "DNT": "1"
            }
        , "devNotes": {
              "givenUrl": "https://city-n.ru/road_cam.html"
            , "startedOn": "June 6, 2023"
            , "missionTLDN": "ru"
            , "setBy": "christopher16"
        }
    }
    return configTemplate


def _getDomainFolder(ap):
    countryDomain = ap["bucketPrefixTemplate"].split("/{deviceID}")[0]
    domainPrefix = f"{GLOBALS.deliveryKey}/{countryDomain}"
    return domainPrefix


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Aimpoint generator for ipcam.rdtc domain"
    )
    args = parser.parse_args()

    upSince = processInit.preFlightSetup()
    processInit.initSessionObject(config["sessionHeaders"])
    if not processInit.initialize():
        logger.error("Failed to initialize")
        exit(1)

    # No proxy for AWS metadata
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
    now = int(time.time())
    logger.info(f"Process clocked at {str(datetime.timedelta(seconds=now-upSince))}")
    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
