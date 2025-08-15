"""
Module to create the JSON aimpoints

The aimpoint needs the following:
    "accessUrl": <read tsv file, grab camera links (for different angles)>
    "longLat": <read tsv file and write>

"""

# External libraries import statements
import os
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
DOMAIN = "141.ir"


def _getPopulation(url):
    logger.info("Getting target population")

    if GLOBALS.useTestData:
        camsFile = f"testResources/allIodineCameras.json"
        logger.debug(f"Reading from test file '{camsFile}'")
    else:
        filename = GLOBALS.netUtils.downloadFile(url)
        camsFile = f"{config['workDirectory']}/{filename}"

    with open(camsFile) as f:
        loaded = json.load(f)
    try:
        allCams = loaded["data"]
    except KeyError as err:
        logger.error(f"Element '{err}' missing in received data")
        raise HPatrolError("Unexpected data received")

    logger.info(f"Cameras available to query: {len(allCams)}")
    allCams = [{"id":x[0], "lat":x[1], "lon":x[2]} for x in allCams]

    if not allCams:
        logger.error("Could not grab all cameras available!")
        raise HPatrolError("No cameras available found")
    # logger.info(f"POPULATION: {allCams}")

    return allCams


def execute(upSince):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Parser"
    GLOBALS.subtaskName = "IodineParser"

    idsSelectionFile = f"selected-{DOMAIN}.json"
    populationUrl = "https://141.ir/cameras"

    try:
        population = _getPopulation(populationUrl)
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
        , "Latitude"
        , "Longitude"
        )
    structKeys = (
          "id"
        , "lat"
        , "lon"
        )
    configTemplate = _getConfigTemplate()
    domainFolder = _getDomainFolder(configTemplate)
    try:
        shouldWriteAimpoints = comp.writeAPs(
            upSince,
            population,
            (structKeys, structTitles),
            domainFolder,
            "rptIodineMasterIdList",
            selectedList=selection)
    except HPatrolError:
        logger.exception("Unable to do ID comparison")
        return False

    if shouldWriteAimpoints:
        try:
            _doStillCams(population, selection, configTemplate)
        except HPatrolError as err:
            # logger.error(err)
            return False

    return True


def _allInputsValid(camSpec):
    if camSpec["id"] == "":
        return False
    if camSpec["lat"] == "":
        return False
    if camSpec["lon"] == "":
        return False

    return True


def _makeQueryIdList(selection):
    # Because ipinfo was included with camera id in selectedIodineIPStills.json
    # make a list with just cameraId for querying
    queryIdList = []
    for item in selection:
        index = item.find("-", 0)
        if index != -1:
            values = item.split("-")
            queryId = str(values[1])
        else:
            queryId = item
        queryIdList.append(queryId)

    logger.error("*********************")
    logger.error("*********************")
    logger.error(queryIdList)
    logger.error("*********************")
    logger.error("*********************")

    return queryIdList
   

def _checkForIPInfo(deviceId):
    result = ""
    substring = "-" + deviceId
    ipList = ["89.32-245", "29.31-70", "29.16-56", "97.23-128", "93.16-575", "89.33-255", "29.3-69", "29.17-57", "29.32-71", "29.1-43", "29.2-912"]
    for item in ipList:
        if substring in item:
            result = item
    return result


def _doStillCams(allCams, selection, configTemplate):
    theKey = f"{DOMAIN}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    idList = _makeQueryIdList(selection)
    counter = 1
    for aCam in allCams:
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and counter == 5:
            logger.debug(f"Not running on PROD; exiting at device #{counter}")
            break

        if _allInputsValid(aCam):
            cameraId = str(aCam["id"])

            if cameraId in idList:
                resultId = _checkForIPInfo(cameraId)
                logger.debug(f"Result ID is '{resultId}'")
                if resultId != "":
                    tempId = resultId
                else:
                    tempId = cameraId
                logger.debug(f"tempId is '{tempId}'")

                if GLOBALS.useTestData:
                    camsFile = "testResources/iodineSample245.json"
                else:
                    url = f"https://141.ir/cameras/{cameraId}"
                    try:
                        response = GLOBALS.netUtils.downloadFile(url)
                    except:
                        # raise ConnectionError(f"URL access attempt failed for: {url}")
                        continue

                # cameraAngles = response.json()
                logger.debug(f"Camsfile is '{camsFile}'")
                with open(camsFile, 'r') as f:
                    cameraAngles = json.load(f)
                # Janice: need to use index or something to grab each link, otherwise, it will overwrite each other resulting in a single aimpoint
                index = 0
                for cameraAngle in cameraAngles["data"]["camera_links"]:
                    index = index + 1
                    i = str(index)
                    theID = tempId + "-" + i
                    logger.info(f"Creating JSON file for ID:{theID}")
                    configTemplate["deviceID"] = theID.zfill(5)
                    configTemplate["longLat"] = [float(aCam["lon"]), float(aCam["lat"])]
                    configTemplate["accessUrl"] = cameraAngle["link"]
                    configTemplate["pollFrequency"] = 120
                    if selection[theID] == "decoy":
                        configTemplate["decoy"] = True
                    else:
                        configTemplate["decoy"] = False

                    outFile = os.path.join(config["workDirectory"], f"{theID}.json")

                    try:
                        ut.writeJsonDataToFile(configTemplate, outFile)
                    except Exception as err:
                        logger.exception(f"Error creating aimpoint file:::{err}")
                        raise HPatrolError("Error creating aimpoint file")
                    
                    s3Dir = aimpointDir
                    if selection[theID] in ["monitor", "monitor-mp4", "monitor-decoy"]:
                        s3Dir = monitoredDir
                    result = GLOBALS.S3utils.pushToS3(outFile,
                                            s3Dir,
                                            config["defaultWrkBucket"],
                                            s3BaseFileName=f"{theID}.json",
                                            deleteOrig=GLOBALS.onProd,
                                            extras={"ContentType": "application/json"})
                counter += 1


def _getConfigTemplate():
    configTemplate = {
          "deviceID": "SETLATER"
        , "enabled": True
        , "decoy": "SETLATER"
        , "collRegions": ["South America (Sao Paulo)"]
        , "collectionType": "ISTLLS"
        , "accessUrl": "SETLATER"
        , "pollFrequency": 180
        , "filenameBase": "iodine-{deviceID}"
        , "finalFileSuffix": "_{year}{month}{day}"
        , "bucketPrefixTemplate": "ir/141ir/{deviceID}/{year}/{month}"
        , "longLat": "SETLATER"
        , "headers": {
              "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:102.0) Gecko/20100101 Firefox/102.0"
            , "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
            , "Accept-Language": "en-US,en;q=0.5"
            , "Accept-Encoding": "gzip, deflate, br"
            , "DNT": "1"
            , "Connection": "keep-alive"
            , "Host": "cdn.141.ir"
            , "Sec-Fetch-Dest": "document"
            , "Sec-Fetch-Mode": "navigate"
            , "Sec-Fetch-Site": "none"
	        , "Sec-Fetch-User": "?1"
            , "Upgrade-Insecure-Requests": "1"
        }
        , "devNotes": {
              "givenURL": "https://cdn.141.ir/"
            , "startedOn": "February 2025 on HP; overall 2014 under task Iodine"
            , "missionTLDN": "ir"
            , "setBy": "reynaldn"
            }
    }
    return configTemplate


def _getDomainFolder(ap):
    countryCode = ap["bucketPrefixTemplate"].split("/{deviceID}")[0]
    domainPrefix = f"{GLOBALS.deliveryKey}/{countryCode}"
    return domainPrefix


def lambdaHandler(event, context):
     upSince = processInit.preFlightSetup()
     processInit.initSessionObject(config['sessionHeaders'])
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
     logger.info(f'= {toPrint} =')
     logger.info(f'=={"=" * len(toPrint)}==')

     return {"status": trueOrFalse}


if __name__ == '__main__':
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
    arn = f"arn:aws:ec2:{region}:{accountId}:instance/{instanceId}"
    GLOBALS.myArn = arn

    execute(upSince)

    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
