"""
Aimpoint generator for cud59
Many things in this script are hardcoded because it's for a specific domain.

"""

# External libraries import statements
import os
import re
import json
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
DOMAIN = "cud59.ru"


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
        # Execute!
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


def  _getPopulationDataFromHtml(htmlFile):
    data = htmlFile.split("placemarks = ")[1]
    extractedJson = data.split(";")[0]
    readyJson=re.sub("'", '"', extractedJson)

    return readyJson


def _getPopulation():
    # Get the entire population of possible devices
    anUrl = "https://cud59.ru"

    if GLOBALS.useTestData:
        populationFile = "testResources/cud59CamsPopulation.json"
        logger.info(f"Reading population file '{populationFile}'")

        with open(populationFile, "r", encoding="utf-8") as f:
            fileContents = f.read()             
    else:
        logger.info(f"Getting page '{anUrl}'")
        try:
            r = GLOBALS.netUtils.get(anUrl, headers=config["sessionHeaders"])
        except:
            raise HPatrolError(f"URL access failed from {GLOBALS.perceivedIP} attempting {anUrl}")
        htmlFileContents = r.text
        fileContents = _getPopulationDataFromHtml(htmlFileContents)
  
    try:
        allCams = json.loads(fileContents)
        logger.info("Obtained camera population data")
        # logger.debug(allCams)

        dictList = []
        for cam in allCams:
            id = cam["id"]
            name = cam["name"]
            longitude = cam["coordPoint"][0]
            latitude = cam["coordPoint"][1]
            temp = cam["temp"]
            iblock = cam["iblock"]
            idjsonId = cam["idjson"]["ID"]
            timestampX = cam["idjson"]["TIMESTAMP_X"]
            iblockId = cam["idjson"]["IBLOCK_ID"]
            idjsonName = cam["idjson"]["NAME"]
            active = cam["idjson"]["ACTIVE"]
            sort = cam["idjson"]["SORT"]
            code = cam["idjson"]["CODE"]
            defaultValue = cam["idjson"]["DEFAULT_VALUE"]
            propertyType = cam["idjson"]["PROPERTY_TYPE"]
            rowCount = cam["idjson"]["ROW_COUNT"]
            colCount = cam["idjson"]["COL_COUNT"]
            listType = cam["idjson"]["LIST_TYPE"]
            multiple = cam["idjson"]["MULTIPLE"]
            xmlId = cam["idjson"]["XML_ID"]
            fileType = cam["idjson"]["FILE_TYPE"]
            multipleCnt = cam["idjson"]["MULTIPLE_CNT"]
            tmpId = cam["idjson"]["TMP_ID"]
            linkIblockId = cam["idjson"]["LINK_IBLOCK_ID"]
            withDescription = cam["idjson"]["WITH_DESCRIPTION"]
            searchable = cam["idjson"]["SEARCHABLE"]
            filtrable = cam["idjson"]["FILTRABLE"]
            isRequired = cam["idjson"]["IS_REQUIRED"]
            version = cam["idjson"]["VERSION"]
            userType = cam["idjson"]["USER_TYPE"]
            userTypeSettings = cam["idjson"]["USER_TYPE_SETTINGS"]
            hint = cam["idjson"]["HINT"]
            propertyValueId = cam["idjson"]["PROPERTY_VALUE_ID"]
            value = cam["idjson"]["VALUE"]
            description = cam["idjson"]["DESCRIPTION"]
            valueEnum = cam["idjson"]["VALUE_ENUM"]
            valueXmlId = cam["idjson"]["VALUE_XML_ID"]
            valueSort = cam["idjson"]["VALUE_SORT"]
            tildeValue = cam["idjson"]["~VALUE"]
            tildeDescription = cam["idjson"]["~DESCRIPTION"]
            tildeIdjsonName = cam["idjson"]["~NAME"]
            tildeDefaultValue = cam["idjson"]["~DEFAULT_VALUE"]

            camDict = {
                  "key": id
                , "name": name
                , "longitude": longitude
                , "latitude": latitude
                , "temp": temp
                , "iblock": iblock
                , "id": idjsonId
                , "timestampX": timestampX
                , "iblockId": iblockId
                , "idjsonName": idjsonName
                , "active": active
                , "sort": sort
                , "code": code
                , "defaultValue": defaultValue
                , "propertyType": propertyType
                , "rowCount": rowCount
                , "colCount": colCount
                , "listType": listType
                , "multiple": multiple
                , "xmlId": xmlId
                , "fileType": fileType
                , "multipleCnt": multipleCnt
                , "tmpId": tmpId
                , "linkIblockId": linkIblockId
                , "withDescription": withDescription
                , "searchable": searchable
                , "filtrable": filtrable
                , "isRequired": isRequired
                , "version": version
                , "userType": userType
                , "userTypeSettings": userTypeSettings
                , "hint": hint
                , "propertyValueId": propertyValueId
                , "value": value
                , "description": description
                , "valueEnum": valueEnum
                , "valueXmlId": valueXmlId
                , "valueSort": valueSort
                , "~value": tildeValue
                , "~description": tildeDescription
                , "~name": tildeIdjsonName
                , "~defaultValue": tildeDefaultValue
            }

            dictList.append(camDict)

        logger.info(f"Total IDs: {len(dictList)}")
        return dictList

    except ValueError as err:
        logger.error(f"Error getting target population:::{err}")
        logger.debug(f"Content received is:\n{fileContents}")
        raise HPatrolError("Error getting population")


def execute(upSince, isScript):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Parser"
    GLOBALS.subtaskName = "Cud59Parser"
    idsSelectionFile = f"selected-{DOMAIN}.json" 

    try:
        allCams = _getPopulation()
    except HPatrolError as err:
        logger.exception(f"Error getting target population:::{err}")
        return False

    try:
        selection = hput.getSelection(idsSelectionFile)
    except HPatrolError as err:
        # logger.error(err)
        return False

    structTitles = (
          "ID"
        , "PlaceName"
        , "Longitude"
        , "Latitude"
        , "Temperature"
        , "Iblock"
        , "SecondId"
        , "Timestamp"
        , "IblockId"
        , "IdJsonName"
        , "Active"
        , "Sort"
        , "Code"
        , "DefaultValue"
        , "PropertyType"
        , "RowCount"
        , "ColCount"
        , "ListType"
        , "Multiple"
        , "XmlId"
        , "FileType"
        , "MultipleCnt"
        , "TempId"
        , "LinkIblockId"
        , "WithDescription"
        , "Searchable"
        , "Filtrable"
        , "IsRequired"
        , "Version"
        , "UserType"
        , "UserTypeSettings"
        , "Hint"
        , "PropertyValueId"
        , "Value"
        , "Description"
        , "ValueEnum"
        , "ValueXmlId"
        , "ValueSort"
        , "~Value"
        , "~Description"
        , "~Name"
        , "~Default_value"
        )

    structKeys = (
          "key"
        , "name"
        , "longitude"
        , "latitude"
        , "temp"
        , "iblock"
        , "id"
        , "timestampX"
        , "iblockId"
        , "idjsonName"
        , "active"
        , "sort"
        , "code"
        , "defaultValue"
        , "propertyType"
        , "rowCount"
        , "colCount"
        , "listType"
        , "multiple"
        , "xmlId"
        , "fileType"
        , "multipleCnt"
        , "tmpId"
        , "linkIblockId"
        , "withDescription"
        , "searchable"
        , "filtrable"
        , "isRequired"
        , "version"
        , "userType"
        , "userTypeSettings"
        , "hint"
        , "propertyValueId"
        , "value"
        , "description"
        , "valueEnum"
        , "valueXmlId"
        , "valueSort"
        , "~value"
        , "~description"
        , "~name"
        , "~defaultValue"
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
                    allCams,
                    (structKeys, structTitles),
                    domainFolder,
                    "rptCud59ParserMasterIdList",
                    selectedList=selection)
        except HPatrolError:
            logger.exception("Unable to do ID comparison")
            return False

    if shouldWriteAimpoints:
        try:
            logger.info("Processing for still images")
            _doStillCams(allCams, selection, configTemplate)
        except HPatrolError as err:
            return False

    return True


def _doStillCams(population, allSelected, configTemplate):
    theKey = f"{DOMAIN}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    for aCam in allSelected:
        for anEntry in population:
            if aCam in anEntry["value"]:
                logger.info(f"Creating JSON file for ID: {aCam}")
                configTemplate["deviceID"] = aCam
                configTemplate["accessUrl"] = f"https://cud59.ru/pool/webasmo/{aCam}.jpg"
                configTemplate["longLat"] = [float(anEntry["longitude"]), float(anEntry["latitude"])]
                configTemplate["bucketPrefixTemplate"] = f"ru/cud59/{aCam}/{{year}}/{{month}}"
                if allSelected[aCam] == "decoy" or allSelected[aCam] == "monitor-decoy":
                    configTemplate["decoy"] = True
                else:
                    configTemplate["decoy"] = False

                # logger.debug(configTemplate)
                outFile = os.path.join(config["workDirectory"], f"{aCam}.json")
                try:
                    ut.writeJsonDataToFile(configTemplate, outFile)
                except Exception as err:
                    logger.exception(f"Error creating aimpoint file:::{err}")
                    raise HPatrolError("Error creating aimpoint file")

                s3Dir = aimpointDir
                if allSelected[aCam] in ["monitor", "monitor-mp4", "monitor-decoy"]:
                    s3Dir = monitoredDir

                result = GLOBALS.S3utils.pushToS3(outFile,
                                            s3Dir,
                                            config["defaultWrkBucket"],
                                            s3BaseFileName=f"{aCam}.json",
                                            deleteOrig=GLOBALS.onProd,
                                            extras={"ContentType": "application/json"})


def _getConfigTemplate():
    configTemplate = {
          "deviceID": "SETLATER"
        , "enabled": True
        , "decoy": "SETLATER"
        , "collRegions": ["Europe (Stockholm)"]
        , "collectionType": "STILLS"
        , "accessUrl": "SETLATER"
        , "pollFrequency": 660
        , "filenameBase": "{deviceID}"
        , "finalFileSuffix": "_{year}_{month}_{day}"
        , "longLat": "SETLATER"
        , "bucketPrefixTemplate": "ru/cud59/{deviceID}/{year}/{month}"
        , "headers": {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0"
            , "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        	, "Accept-Encoding": "gzip, deflate, br"
        	, "Accept-Language": "en-US,en;q=0.5"
            , "Sec-Fetch-Dest": "document"
            , "Sec-Fetch-Mode": "navigate"
            , "Sec-Fetch-Site": "none"
            , "Connection": "keep-alive"
            , "Pragma": "no-cache"
            , "Cache-Control": "no-cache"
            , "DNT": "1"
            }
        , "devNotes": {
              "givenURL": "http://cud59.ru"
            , "startedOn": "October 2022"
            , "missionTLDN": "ru"
            , "setBy": "reynaldn"
            , "notes": "On 09.29.22 cameras seem to be updating around 10mins (600s)"
            }
        }
    return configTemplate


def _getDomainFolder(ap):
    countryDomain = ap["bucketPrefixTemplate"].split("/{deviceID}")[0]
    domainPrefix = f"{GLOBALS.deliveryKey}/{countryDomain}"
    return domainPrefix


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Aimpoint generator for stills",
        formatter_class=argparse.RawTextHelpFormatter
    )
    theChoices = ["stills"]
    parser.add_argument("task",
                        help="task to execute",
                        choices=theChoices,
                        type=str.lower,
                        nargs="?",
                        const=""
                        )
    args = parser.parse_args()
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

    argVal = args.task
    if argVal:
        execute(upSince, True)
    else:
        execute(upSince, False)

    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
