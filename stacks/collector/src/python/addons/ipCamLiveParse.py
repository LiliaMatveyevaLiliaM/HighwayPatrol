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
        testFile = "testResources/ipCamLivePlayer.html"
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
    # Handles different known methods to obtain IPLive's player parameters
    # Each method attempts to set the 'composed' variable to the m3u8 URL
    composed = None

    for accessMethod in range(1, 3):
        logger.info(f"Attempting IPLIVE access method #{accessMethod}")

        if accessMethod == 1:
            regex = r"var address = '(.*)';"
            matches = re.search(regex, startContent)
            if matches:
                # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
                # for groupNum in range(0, len(matches.groups())):
                #     groupNum = groupNum + 1
                #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

                # logger.debug(f"\nGROUP1: \n{matches.group(1)}\n")
                netLoc = matches.group(1)
                # logger.debug(f"netLoc: {netLoc}")

            else:
                logger.info(f"URL NOT found looking for \"{regex}\"; trying next method")
                continue # skip the rest of this iteration

            regex = r"var streamid = '(.{17})';"
            matches = re.search(regex, startContent)
            if matches:
                # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
                # for groupNum in range(0, len(matches.groups())):
                #     groupNum = groupNum + 1
                #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

                # logger.debug(f"\nGROUP1: \n{matches.group(1)}\n")
                accessToken = matches.group(1)
                # logger.debug(f"accessToken: {accessToken}")

            else:
                logger.info(f"accessToken NOT found looking for '{regex}' - tring next method")
                continue # skip the rest of this iteration                

            logger.info(f"Successfully composed URL from where to get the playlist")
            composed = f"{netLoc}streams/{accessToken}/stream.m3u8"
            break # stop executing this loop, we have what we need

        if accessMethod == 2:
            # <meta property="og:image" content="https://s2.ipcamlive.com/streams/02mijlaxlt1f0qvvd/snapshot.jpg" />
            # Looks like we can take the ID out of the JPG file there and grab the playlist
            # In this example above, the ID is: 02mijlaxlt1f0qvvd, which creates:
            # https://s2.ipcamlive.com/streams/02mijlaxlt1f0qvvd/stream.m3u8
            regex = r"meta property.*content=\"(.*)streams/(.*)/snapshot.jpg"
            matches = re.search(regex, startContent)
            if matches:
                # logger.debug(f"\nGROUP1: \n{matches.group(1)}\n")
                netLoc = matches.group(1)
                # logger.debug(f"netLoc: {netLoc}")

                # logger.debug(f"\nGROUP2: \n{matches.group(2)}\n")
                accessToken = matches.group(2)
                # logger.debug(f"accessToken: {accessToken}")

            else:
                logger.info(f"URL NOT found looking for \"{regex}\"; trying next method")
                continue # skip the rest of this iteration

            logger.info(f"Successfully composed URL from where to get the playlist")
            composed = f"{netLoc}streams/{accessToken}/stream.m3u8"
            break # stop executing this loop, we have what we need

    if not composed:
        logger.debug(f"Content received is:\n{startContent}")
        raise HPatrolError("Access token NOT found during parse")

    return composed
