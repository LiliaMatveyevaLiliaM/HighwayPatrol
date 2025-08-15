"""
Module to create the JSON aimpoints.

Function retrieves the main configuration file, parses it and creates subsequent JSON files

Can be run as a stand-alone python script to test
"""

# External libraries import statements
import os
import re
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
DOMAIN = "trafficlandStills"


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


def execute():
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Parser"
    GLOBALS.subtaskName = "TrafficlandParser"

    try:
        allCams = _getPopulation()
    except Exception as err:
        logger.exception(f"Error getting target list:::{err}")
        return False

    idsSelectionFile = f"selected-{DOMAIN}.json"
    try:
        stillsSelection = hput.getSelection(idsSelectionFile)
        # logger.debug(f"stillsSelection={stillsSelection}")
        _doStillCams(allCams, stillsSelection)
    except HPatrolError as err:
        # logger.error(err)
        return False

    return True


def _doStillCams(allCams, selection):
    theKey = f"taffy/{DOMAIN}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    configTemplate = {
          "deviceID": "SETLATER"
        , "enabled": True
        , "decoy": "SETLATER"
        , "collRegions": ["United States (N. Virginia)"]
        , "collectionType": "STILLS"
        , "accessUrl": "SETLATER"
        , "pollFrequency": "SETLATER"
        , "singleCollector": True
        , "concatenate": False
        , "transcodeExt": None
        , "longLat": "SETLATER"
        , "filenameBase": "trafficland_{deviceID}"
        , "finalFileSuffix": "_{epoch}"
        , "bucketPrefixTemplate": "SETLATER"
        , "wrkBucket": "taffy-ch-prod"
        , "dstBucket": "taffy-ch-prod"
        , "headers": {
              "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0"
            , "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
            , "Accept-Encoding": "gzip, deflate, br"
            , "Accept-Language": "en-US,en;q=0.5"
            , "Sec-Fetch-Dest": "document"
            , "Sec-Fetch-Mode": "navigate"
            , "Sec-Fetch-Site": "none"
            , "Connection": "keep-alive"
            , "Referer": "https://ddottrafficmap.azurewebsites.net"
            , "DNT": "1"
        }
        , "devNotes": {
              "givenURL": "Several on https://ie.trafficland.com/v2.0/<deviceID>/full?system=ddot"
            , "startedOn": "October 2022"
            , "setBy": "reynaldn"
            , "missionTLDN": "taffy"
            , "freqNote": "On 10.27.22 seems that cameras are updating every 2secs"
        }
    }

    # Loop through the cams
    for aCam in allCams:
        if _allInputsValid(aCam):
            theID = str(aCam['id'])
            if theID in selection:
                logger.info(f"Creating JSON file for ID:{theID}")
                configTemplate["deviceID"] = theID
                if selection[theID] == "decoy" or selection[theID] == "monitor-decoy":
                    configTemplate["decoy"] = True
                else:
                    configTemplate["decoy"] = False
                configTemplate["accessUrl"] = aCam["image"]
                # We may need to add the epoch timestamp...hopefully not (would require specific coding)
                # from: https://ie.trafficland.com/v2.0/200031/full?system=ddot&pubtoken=48322aca5b6ea0983cd129b8ce8911c0d4e9510e885b3c850c653555cfb8bad6&refreshRate=2000
                #   to: https://ie.trafficland.com/v2.0/200031/full?system=ddot&pubtoken=6dba1c6f7c2f989202151868ec5fd5d60bd67c687f9e418ea54f1a2ca2bc7a75&refreshRate=2000&t=1666900102446

                configTemplate["longLat"] = [aCam["lng"], aCam["lat"]]
                configTemplate["pollFrequency"] = int(aCam["refresh"] / 1000)
                configTemplate["bucketPrefixTemplate"] = f"stills/{{year}}/{{month}}/{{day}}/trafficland_{theID}"
                # logger.debug(configTemplate)
                outFile = os.path.join(config['workDirectory'], f"{theID}.json")
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
                                        config['defaultWrkBucket'],
                                        s3BaseFileName=f"{theID}.json",
                                        deleteOrig=GLOBALS.onProd,
                                        extras={'ContentType': 'application/json'})


def _getPopulation():
    theUrl = "https://ddottrafficmap.azurewebsites.net"
    if GLOBALS.useTestData:
        testFile = "testResources/01-FirstPage.html"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(testFile, 'r') as f:
            pageContent = f.read()
    else:
        logger.info(f"Getting page '{theUrl}'")
        try:
            r = GLOBALS.netUtils.get(theUrl)
        except:
            raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {theUrl}") from None
        pageContent = r.text

    # Eliminate spaces to make regex easier
    noSpaces = "".join(pageContent.split())

    # Since data is large, we rather find the start and end of it instead of making one regex
    # Here we find the start of the JSON chunk
    regex = r"//<!\[CDATA\[LoadCameras\((\[{\"id\"):"
    matches = re.search(regex, noSpaces)
    if not matches:
        logger.info("Requested camData NOT found; exiting")
        logger.debug(f"Content received is:\n{pageContent}")
        raise HPatrolError("Unable to parse for camData")
    # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
    # for groupNum in range(0, len(matches.groups())):
    #     groupNum = groupNum + 1
    #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))
    # logger.debug(f"START: {matches.start(1)}")
    theStart = matches.start(1)

    # Find the end of the JSON data
    regex = r"pindex\":4}](\)//]]></script>)</form"
    matches = re.search(regex, noSpaces)
    if not matches:
        logger.info("Requested camData NOT found; exiting")
        logger.debug(f"Content received is:\n{pageContent}")
        raise HPatrolError("Unable to parse for camData")
    # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
    # for groupNum in range(0, len(matches.groups())):
    #     groupNum = groupNum + 1
    #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))
    # logger.debug(f"END: {matches.start(1)}")
    theEnd = matches.start(1)

    # Grab the entire JSON data
    textChunk = noSpaces[theStart: theEnd]
    # logger.debug(f"textChunk: {textChunk}")
    try:
        allCams = json.loads(textChunk)
        logger.info("Obtained camera JSON data")
    except Exception:
        logger.debug(f"Content received is:\n{textChunk}")
        raise

    return allCams


def _allInputsValid(camSpec):
    if camSpec['id'] == "":
        return False
    if camSpec['image'] == "":
        return False
    if camSpec['refresh'] == "":
        return False
    if camSpec["lat"] == "":
        return False
    if camSpec["lng"] == "":
        return False

    return True


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

    execute()

    nownow = int(time.time())
    logger.info(f'Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}')

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
