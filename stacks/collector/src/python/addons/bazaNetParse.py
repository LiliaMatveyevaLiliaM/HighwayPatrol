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
        testFile = "testResources/Baza.net_HarExtracted.html"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(testFile, 'r') as f:
            startContent = f.read()
    else:
        try:
            r = GLOBALS.netUtils.get(theUrl, headers=theHeaders)
        except:
            raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {theUrl}") from None
        startContent = r.text

    return _parseForPlaylist(startContent)


def _parseForPlaylist(startContent):
    # Example of what we're expecting and searching for
    # <iframe allowfullscreen class='camera-frame' style='width: 100%; border: none; aspect-ratio: 1.7777777777778;' src='https://dvr1.baza.net/poshehonskoe.kolco-90efe89be1/embed.html?autoplay=true&token=YzJmZTUxNThjMjliMTY1NWJmMjdhNDNmNWM5YTNjYWUyYmQxZDdjNy4xNzQ2ODE0MzMw'></iframe>

    regex = r"<iframe (?:.*)src='(.*)/embed.html\?autoplay(?:.*)&token=(.*)'></iframe>"
    matches = re.search(regex, startContent)
    if matches:
        # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
        # for groupNum in range(0, len(matches.groups())):
        #     groupNum = groupNum + 1
        #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

        netLoc = matches.group(1)
        accessToken = matches.group(2)
        # camName = netLoc.split("-")[-1]
        # logger.debug(f"****camName: {camName}")

    else:
        logger.critical(f"String NOT found looking for '{regex}'; exiting")
        logger.debug(f"Content received is:\n{startContent}")
        raise HPatrolError("Access token NOT found during parse")

    logger.info("Successfully composed URL from where to get the playlist")
    composed = f"{netLoc}/index.fmp4.m3u8?token={accessToken}"

    return composed
