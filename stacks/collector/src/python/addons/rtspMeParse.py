"""

As of 09/07/22 rtsp.me uses an "access hash" in order to compose the video's URL.
This access hash is obtained from a different URL than the original. Here's an example:

From the camera URL (https://rtsp.me/embed/Ths3QZZN/) we obtain the "n_url" and the "time URL"
    var n_url = "https://itl.rtsp.me/"+hash.sub+"/1662577225/hls/Ths3QZZN.m3u8?ip=18.235.83.242";
Notice the hash.sub variable here ---------^

The "time URL" can be found on a <script src> tag
    <script src='https://itl.rtsp.me/exY_6blZTsosfFCWpfkNeg/1662574225/hls/Ths3QZZN.js?time=1662577225'></script>
Notice this other hash  here -------------------^

From this "time URL" we obtain the following, and grab the "sub" variable to place in the n_url
    var hash = { sub : 'aXPtXFNMTHDRy5lBgCUeBg', main : 'IVq-OxMex7QNCr0SwszBrw' };

"""


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
        testFile = "testResources/Ths3QZZN_RTSP.ME.html"
        with open(testFile, 'r') as f:
            pageContent = f.read()
    else:
        try:
            r = GLOBALS.netUtils.get(theUrl, headers=theHeaders)
        except:
            raise HPatrolError(f"URL access failed from {GLOBALS.perceivedIP} attempting {theUrl}")
        pageContent = r.text

    urlTemplate, timeUrl = _parseForPlaylistURL(pageContent)


    if GLOBALS.useTestData:
        testFile = "testResources/Ths3QZZN_RTSP.ME.js"
        with open(testFile, 'r') as f:
            pageContent = f.read()
    else:
        try:
            r = GLOBALS.netUtils.get(timeUrl, headers=theHeaders)
        except:
            raise HPatrolError(f"URL access failed from {GLOBALS.perceivedIP} attempting {theUrl}")
        pageContent = r.text

    accessHash = _getHash(pageContent)

    playlistUrl = urlTemplate.replace('"+hash.sub+"', accessHash)

    return playlistUrl


def _parseForPlaylistURL(pageContent):
    regex = r"n_url = (?:\"(https?:\/\/.*)\");"
    matches = re.search(regex, pageContent)
    if matches:
        # # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
        # for groupNum in range(0, len(matches.groups())):
        #     groupNum = groupNum + 1
        #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

        urlTemplate = matches.group(1)
        logger.info(f"URL found: '{urlTemplate}'")

    else:
        logger.error(f"No matches found in pageContent looking for '{regex}'")
        logger.debug(f"Content received is:\n{pageContent}")

        raise HPatrolError(f"No matches found in pageContent looking for '{regex}'")


    regex = r"<script src=(?:\'(https?:\/\/.*)\')></script>"
    matches = re.search(regex, pageContent)
    if matches:
        # # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
        # for groupNum in range(0, len(matches.groups())):
        #     groupNum = groupNum + 1
        #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

        theUrl = matches.group(1)
        logger.info(f"\"time URL\" found: '{theUrl}'")

        return urlTemplate, theUrl

    logger.error(f"No matches found in pageContent looking for '{regex}'")
    logger.debug(f"Content received is:\n{pageContent}")

    raise HPatrolError(f"No matches found in pageContent looking for '{regex}'")


def _getHash(pageContent):
    regex = r"sub *: '(.{22})',"
    matches = re.search(regex, pageContent)
    if matches:
        # # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
        # for groupNum in range(0, len(matches.groups())):
        #     groupNum = groupNum + 1
        #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

        theHash = matches.group(1)
        logger.info(f"Hash for URL found: '{theHash}'")
        return theHash

    logger.error(f"No hash found in pageContent looking for '{regex}'")
    logger.debug(f"Content received is:\n{pageContent}")

    raise HPatrolError(f"No hash found in pageContent looking for '{regex}'")
