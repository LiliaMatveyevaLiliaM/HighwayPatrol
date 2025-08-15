# External libraries import statements
import re
import logging


# This application's import statements
try:
    # These are for when running in an EC2
    from exceptions import *
    import superGlblVars as GLOBALS

except ModuleNotFoundError as err:
    # These are for when running in a Lambda
    print(f"Loading module for lambda execution: {__name__}")
    from src.python.exceptions import *
    from src.python import superGlblVars as GLOBALS


logger = logging.getLogger()


def getPlaylist(jsonConfig):
    theUrl = jsonConfig['accessUrl']
    theHeaders = jsonConfig['headers']

    if GLOBALS.useTestData:
        testFile = "testResources/Maps.UfaNet.ru.html"
        with open(testFile, 'r') as f:
            responseContent = f.read()
    else:
        try:
            r = GLOBALS.netUtils.get(theUrl, headers=theHeaders)
        except:
            raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {theUrl}") from None
            # Note we are suppressing the original exception so it doesn't visually clog the stack trace
            # This is from PEP409 (https://peps.python.org/pep-0409/)
        responseContent = r.text

    try:
        # For ufaNet, the deviceID is after the '#'
        deviceID = theUrl.split("#")[1]
    except IndexError:
        logger.warning(f"DeviceID NOT found in given URL; exiting")
        raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {theUrl}")
        # Note that here we don't suppress the original exception

    return _parseForPlaylist(responseContent, deviceID)


def _parseForPlaylist(pageContent, deviceID):
    # logger.debug("Composing URL to get playlist for http://{serverIP}/{deviceID}/tracks-v1/mono.m3u8?token={aToken}")

    regex = fr"(marker=L\.marker\(.{{100,250}}marker\.number='{deviceID}';marker\.token='.{{32}}';)}}else{{marker\.content="
    # Note: Setting only between 100 and 250 characters because
    # it was returning everything before our target section

    # Eliminate spaces to make regex easier
    noSpaces = "".join(pageContent.split())
    matches = re.search(regex, noSpaces)
    if not matches:
        logger.info("Requested deviceID NOT found; exiting")
        logger.debug(f"Content received is:\n{pageContent}")
        raise HPatrolError("Unable to parse for playlist")
    # # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
    # for groupNum in range(0, len(matches.groups())):
    #     groupNum = groupNum + 1
    #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

    # logger.debug(f"\nGROUP1: \n{matches.group(1)}\n")
    textChunk = matches.group(1)

    # Extract the coordinates
    regex = r"L\.marker\(\[[-+]?([1-8]?\d(?:\.\d+)?|90(\.0+)?),\s*[-+]?(180(\.0+)?|(?:(1[0-7]\d)|(?:[1-9]?\d))(?:\.\d+)?)\]"
    matches = re.search(regex, textChunk)
    if matches:
        lon = matches.group(1)
        lat = matches.group(3)
        # logger.debug(f"LON, LAT=[{lon},{lat}]")

    # Extract the server info
    # serverIP = r"marker\.server='(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})';"
    # theToken = r"marker\.token='(.{32})';"
    regex = fr"marker\.server='(\d{{1,3}}\.\d{{1,3}}\.\d{{1,3}}\.\d{{1,3}})';marker\.number='{deviceID}';marker\.token='(.{{32}})';"
    matches = re.search(regex, textChunk)
    if matches:
        serverIp = matches.group(1)
        # logger.debug(f"serverIp={serverIp}")
        theToken = matches.group(2)
        # logger.debug(f"theToken={theToken}")
    else:
        logger.info("Target parameters NOT found; exiting")
        logger.debug(f"Content received is:\n{pageContent}")
        raise HPatrolError("Unable to parse for playlist")

    composed = f"http://{serverIp}/{deviceID}/index.m3u8?token={theToken}"
    # logger.debug(f"composed={composed}")
    logger.info(f"Successfully composed URL for playlist")

    return composed
