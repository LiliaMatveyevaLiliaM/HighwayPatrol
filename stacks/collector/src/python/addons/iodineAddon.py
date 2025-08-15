# External libraries import statements
import json
import copy
import logging


# This application's import statements
try:
    # These are for when running in an EC2
    from exceptions import *
    import superGlblVars as GLOBALS
    from superGlblVars import config
    from orangeUtils import utils as ut

except ModuleNotFoundError as err:
    # These are for when running in a Lambda
    print(f"Loading module for lambda execution: {__name__}")
    from src.python.exceptions import *
    from src.python.superGlblVars import config
    from src.python.orangeUtils import utils as ut
    from src.python import superGlblVars as GLOBALS


logger = logging.getLogger()


def getUpdatedImgsUrls(ap: dict):
    theUrl = ap["accessUrl"]
    deviceID = ap["deviceID"]

    if GLOBALS.useTestData:
        testFile = "testResources/irCamerasResp.json"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(testFile, "r") as f:
            respText = f.read()

    else:
        # Note: Using deepcopy so .get() doesn't modify the headers
        theHeaders = copy.deepcopy(ap["headers"])
        try:
            r = GLOBALS.netUtils.get(theUrl, headers=theHeaders)
        except:
            raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {theUrl}") from None
        respText = r.text

    try:
        respJson = json.loads(respText)
    except json.decoder.JSONDecodeError as err:
        logger.error(f"Unable to parse response: {err}")
        logger.debug(f"Content received is:\n{respText}")
        raise HPatrolError("Response text is not JSON")

    # Parse image URLs and return in list
    urlList = []
    try:
        camLinks = respJson["data"]["camera_links"]
        for link in camLinks:
            try:
                # Select only new images
                # Pre-checking...don't hit the server unnecessarily
                aHash = hashForTracking(deviceID, link["updated_at"])
                if GLOBALS.S3utils.isFileInS3(config["defaultWrkBucket"], f"{GLOBALS.s3Hashfiles}/{aHash}"):
                    logger.info(f"Ignored; previously captured ({aHash})")
                    continue

                urlList.append({"url": link["link"], "lastUpdate": link["updated_at"]})
            except KeyError as err:
                logger.warning(f"Key {err} missing for camera_links: {link}")
                continue

    except KeyError as err:
        logger.error(f"Key {err} missing from JSON returned")
        logger.debug(f"Content received is:\n{respText}")
        raise HPatrolError("Key missing")

    if urlList:
        logger.debug(f"Updated URLs:\n{json.dumps(urlList, indent=4)}")
    else:
        logger.info(f"No new updates returned for ID: {deviceID}")
        logger.debug(f"Content received is:\n {json.dumps(respJson, indent=4)}")

    return urlList


def hashForTracking(deviceID, updatedAt):
    trackingId = f"{deviceID}/{updatedAt}"
    return ut.getHashFromData(trackingId.encode())
