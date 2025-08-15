# External libraries import statements
import re
import json
import logging


# This application's import statements
try:
    # These are for when running in an EC2
    from exceptions import *
    import superGlblVars as GLOBALS
    from orangeUtils import utils as ut

except ModuleNotFoundError as err:
    # These are for when running in a Lambda
    print(f"Loading module for lambda execution: {__name__}")
    from src.python.exceptions import *
    from src.python.orangeUtils import utils as ut
    from src.python import superGlblVars as GLOBALS


logger = logging.getLogger()


def getPlaylist(jsonConfig):
    theUrl = jsonConfig["accessUrl"]
    theHeaders = jsonConfig["headers"]

    if GLOBALS.useTestData:
        testFile = "testResources/iVideonPage.html"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(testFile, "r") as f:
            startContent = f.read()
    else:
        try:
            r = GLOBALS.netUtils.get(theUrl, headers=theHeaders)
        except:
            raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {theUrl}") from None
        startContent = r.text

    return _parseForPlaylist(startContent, jsonConfig)


def _parseForPlaylist(startContent, jsonConfig):
    # logger.debug("Composing URL to get playlist from https://{netLoc}cameras/{serverId}:{cameraId}/live_stream?op=GET&access_token={accessToken}&q=2&video_codecs=h264&audio_codecs=aac%2Cmp3&format=hls&wait_segments={waitSegments}&segment_duration={segmentDuration}&_={random}")

    regex = r"var config\s*=\s*({.*?});\s*ivideon\.config\s*=\s*config;"
    matches = re.search(regex, startContent)
    if matches:
        # # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
        # for groupNum in range(0, len(matches.groups())):
        #     groupNum = groupNum + 1
        #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

        # logger.debug(f"\nGROUP1: \n{matches.group(1)}\n")
        theDict = json.loads(matches.group(1))

        try:
            netLoc = theDict["openApi"]["apiUrl"]
            # logger.debug(f"******************: {netLoc}")
            accessToken = theDict["openApi"]["publicAccessToken"]
            # logger.debug(f"accessToken: {accessToken}")

            serverId = theDict["ivEmbedAppOptions"]["cameraData"]["serverId"]
            # logger.info(f"serverId: {serverId}")
            cameraId = theDict["ivEmbedAppOptions"]["cameraData"]["cameraId"]
            # logger.info(f"cameraId: {cameraId}")

        except Exception as err:   # Catches both KeyError and TypeError
            logger.error("Dictionary elements not found")
            logger.debug(f"Content received is:\n{startContent}")
            logger.debug(f"Regex match is: {matches}")
            # logger.debug(err)
            raise HPatrolError("cameraData NOT found")

    else:
        logger.warning("Unable to parse device data; exiting")
        logger.debug(f"Content received is:\n{startContent}")
        raise HPatrolError("cameraData NOT found")

    # The sample URL we identified during dev was this
    # https://openapi-alpha.ivideon.com/cameras/100-7a5c7c5223b3770d0ac81c3bb3f91ee4:0/live_stream?op=GET&access_token=public&q=2&video_codecs=h264&audio_codecs=aac%2Cmp3&format=hls&wait_segments=1&segment_duration=1&_=0.5654728125512419
    # During testing, higher than 20s segmentDuration would cause a time out
    segmentDuration = jsonConfig["pollFrequency"]

    # We don't know what these two do
    waitSegments = 1    # at greater than 1, it times out
    # This appears to be a random number; who knows
    theRand = ut.generateRandomInt(signed=False)/10000000000000000000
    logger.info(f"Successfully composed URL from where to get the playlist")
    composed = f"{netLoc}cameras/{serverId}:{cameraId}/live_stream?op=GET&access_token={accessToken}&q=2&video_codecs=h264&audio_codecs=aac%2Cmp3&format=hls&wait_segments={waitSegments}&segment_duration={segmentDuration}&_={theRand}"

    newHeaders = {
          "Host": "openapi-alpha.ivideon.com"
        , "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0"
        , "Accept": "*/*"
        , "Accept-Language": "en-US,en;q=0.5"
        , "Accept-Encoding": "gzip, deflate, br"
        , "Origin": "https://open.ivideon.com"
        , "DNT": "1"
        , "Connection": "keep-alive"
        , "Referer": "https://open.ivideon.com/"
        , "Sec-Fetch-Dest": "empty"
        , "Sec-Fetch-Mode": "cors"
        , "Sec-Fetch-Site": "same-site"
        , "Pragma": "no-cache"
        , "Cache-Control": "no-cache"
    }

    if GLOBALS.useTestData:
        streamInfo = b'#EXTM3U\n#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=2350383,CODECS="avc1.4d0028,mp4a.40.2",RESOLUTION=640x480\nhttps://usa502.extcam.com/hls/playlist.m3u8?hlsId=f08ac1e063764947b6fb6c28df95ede9_100000948212_0&expires_at=1646857509&token=32487a14e175e7c2f692e156957abc4d\n'
        streamInfoStr = streamInfo.decode("utf-8")
    else:
        try:
            # Need to handle the redirects ourselves
            r = GLOBALS.netUtils.get(composed, headers=newHeaders, timeout=40, allow_redirects=False)
        except:
            raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {composed}") from None
        streamInfoStr = r.text

    logger.info("Scanning for m3u8 URL playlist")
    streamInfoStrList = streamInfoStr.split("\n")
    for aLine in streamInfoStrList:
        if ".m3u8" in aLine:
            logger.info("m3u8 URL found")
            # logger.debug(f"FOUND: '{aLine}'")
            return aLine

    logger.warning("m3u8 URL NOT found")
    logger.debug(f"Content received is:\n{streamInfoStr}")
    raise HPatrolError("m3u8 URL NOT found")


# Not currenly used. Here as historical record in case we need to come back to this.
# This was used when we needed to obtain the URL for a JavaScript that we needed to call
def _createLocationComposerJS(content):
    regex = r"ivideon-preferredCdn=https%3A//(.*?)\","
    testString = "ivideon-preferredCdn=https%3A//my-static-usa.iv-cdn.com\","
    matches = re.search(regex, content)
    if matches:
        # # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
        # for groupNum in range(0, len(matches.groups())):
        #     groupNum = groupNum + 1
        #     print("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

        urlStart = matches.group(1)
    else:
        logger.info("NOTHING FOUND; exiting")
        logger.debug(f"Content received is:\n{content}")
        raise HPatrolError("Regex not found")

    regex = r"var m=(.*?)\|\|void 0;S\.isInitialized\(\)\|\|void 0===m\|\|S\.init\(m\);var j=S}\(\),"
    testString = ("i.C?s(null):v()}),1e3)}};var b=window._ivideonAssetLoader,S=b||new g;b||(window._ivideonAssetLoader=S);var m={\"baseUrls\":[\"https://my-static-usa.iv-cdn.com\",\"https://my-static-usa.ivideon.com\"],\"cookieParams\":{\"domain\":\".ivideon.com\",\"path\":\"/\",\"expire\":86400000,\"secure\":true,\"httpOnly\":false,\"sameSite\":\"Lax\",\"name\":\"ivideon-preferredCdn\"},\"gaCategory\":\"Embedded Video\",\"initialAssets\":[{\"url\":\"/assets/static/cdn-check.js\",\"type\":\"js\",\"options\":{\"timeout\":5000,\"crossOrigin\":\"anonymous\",\"cachebusting\":true,\"withoutEvaluation\":true}},{\"url\":\"/assets/build/iv-embed/dist/runtime.__bfd062572cf1d5536769__.js\",\"type\":\"js\",\"options\":{\"crossOrigin\":\"anonymous\",\"timeout\":20000}},{\"url\":\"/assets/build/iv-embed/dist/954.__a3aec859716807380d41__.js\",\"type\":\"js\",\"options\":{\"crossOrigin\":\"anonymous\",\"timeout\":20000}},{\"url\":\"/assets/build/iv-embed/dist/iv-embed.__426d905e41aec42a4a9b__.js\",\"type\":\"js\",\"options\":{\"crossOrigin\":\"anonymous\",\"timeout\":20000}}]}||void 0;S.isInitialized()||void 0===m||S.init(m);var j=S}(),\n")
    matches = re.search(regex, content)
    if matches:
    #     # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
    #     for groupNum in range(0, len(matches.groups())):
    #         groupNum = groupNum + 1
    #         print("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

        # # chunks = re.search(regex, content)
        # print(f"\nGROUP1: \n{matches.group(1)}\n")
        theDict = json.loads(matches.group(1))
        for anAsset in theDict["initialAssets"]:
            if "iv-embed." in anAsset["url"]:
                jsUrl = f"https://{urlStart}{anAsset['url']}"
                logger.info(f"Composed JS URL as: '{jsUrl}'")
                return jsUrl

    logger.info(f"NOT found")
    raise HPatrolError("Element not found")
