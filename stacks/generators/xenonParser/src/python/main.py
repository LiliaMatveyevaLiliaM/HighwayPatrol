"""
Module to create the JSON aimpoints.

Can be run as a stand-alone python script to test
"""

# External libraries import statements
import os
import time
import logging
import threading
import xmltodict
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
DOMAIN = "traffic.td.gov.hk"


def _getPopulation(url):
    logger.info("Getting target population")

    if GLOBALS.useTestData:
        camsFile = f"testResources/Traffic_Camera_Locations_En_20230315.xml"
        logger.debug(f"Reading from test file '{camsFile}'")

    else:
        filename = GLOBALS.netUtils.downloadFile(url)
        camsFile = f"{config['workDirectory']}/{filename}"

    xmlFile = open(camsFile, 'r').read()
    dataDict = xmltodict.parse(xmlFile)
    allCams = dataDict['image-list']['image']

    logger.info(f"Cameras available to query: {len(allCams)}")

    if not allCams:
        logger.error("Could not grab all cameras available!")
        raise HPatrolError("No cameras available found")

    # logger.info(f"POPULATION: {allCams}")
    return allCams


def execute(upSince):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Parser"
    GLOBALS.subtaskName = "XenonParser"

    idsSelectionFile = f"selected-{DOMAIN}.json"
    populationUrl = 'https://static.data.gov.hk/td/traffic-snapshot-images/code/Traffic_Camera_Locations_En.xml'

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
        , "ImageURL"
        , "Region"
        , "District"
        , "Longitude"
        , "Latitude"
        , "Description"
        )
    structKeys = (
          "key"
        , "url"
        , "region"
        , "district"
        , "longitude"
        , "latitude"
        , "description"
        )

    configTemplate = _getConfigTemplate()
    try:
        # TODO: Separate report creation from writing aimpoint decision
        #       shouldWriteAimpoints should be picked only once
        for mtdtKey in configTemplate["deliveryKey"]:
            shouldWriteAimpoints = comp.writeAPs(
                upSince,
                population,
                (structKeys, structTitles),
                mtdtKey,
                "rptXenonMasterIdList",
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
    if camSpec['key'] == "":
        return False
    if camSpec['region'] == "":
        return False
    if camSpec['district'] == "":
        return False
    if camSpec["easting"] == "":
        return False
    if camSpec["northing"] == "":
        return False
    if camSpec["latitude"] == "":
        return False
    if camSpec["longitude"] == "":
        return False
    if camSpec["url"] == "":
        return False

    return True


def _doStillCams(allCams, selection, configTemplate):
    theKey = f"{DOMAIN}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    # Loop through the cams
    for aCam in allCams:
        if _allInputsValid(aCam):
            theID = str(aCam["key"])
            if theID in selection:
                logger.info(f"Creating JSON file for ID:{theID}")
                configTemplate["deviceID"] = theID
                configTemplate["longLat"] = [float(aCam["longitude"]), float(aCam["latitude"])]
                configTemplate["accessUrl"] = aCam["url"]
                if selection[theID] == "decoy" or selection[theID] == "monitor-decoy":
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


def _getConfigTemplate():
    configTemplate = {
          "deviceID": "SETLATER"
        , "enabled": True
        , "decoy": "SETLATER"
        , "collRegions": ["Asia Pacific (Seoul)"]
        , "collectionType": "STILLS"
        , "accessUrl": "SETLATER"
        , "pollFrequency": 40
        , "filenameBase": "xenon{deviceID}"
        , "finalFileSuffix": "_{year}_{month}_{day}"
        , "bucketPrefixTemplate": "{year}/{month}/{day}"
        , "deliveryKey": ["xenon"]
        , "longLat": "SETLATER"
        , "headers": {
              "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0"
            , "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
            , "Accept-Encoding": "gzip, deflate, br"
            , "Accept-Language": "en-US,en;q=0.5"
            , "Host": "traffic.td.gov.hk"
            , "Sec-Fetch-Dest": "document"
            , "Sec-Fetch-Mode": "navigate"
            , "Sec-Fetch-Site": "none"
            , "Connection": "keep-alive"
            , "DNT": "1"
        }
        , "devNotes": {
              "givenURL": "https://traffic.td.gov.hk/"
            , "startedOn": "Dec 2023 on HP; overall Oct 1st, 2021 under task Xenon"
            , "country": "China"
            , "region": "Hong Kong"
            , "missionTLDN": "hk"
            , "setBy": "reynaldn"
            }
    }
    return configTemplate


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
     logger.info(f"= {toPrint} =")
     logger.info(f"=={'=' * len(toPrint)}==")

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
    arn = f'arn:aws:ec2:{region}:{accountId}:instance/{instanceId}'
    GLOBALS.myArn = arn

    execute(upSince)

    nownow = int(time.time())
    logger.info(f'Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}')

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
