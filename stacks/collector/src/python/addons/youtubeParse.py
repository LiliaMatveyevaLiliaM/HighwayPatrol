# External libraries import statements
import re
import json
import time
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
    theHeaders = jsonConfig['headers']
    theUrl = jsonConfig['accessUrl']
    theId = _getTubeId(theUrl)

    # Go through all known YT access methods
    idx = 1
    while(True):
        playerParams = _selectPlayerParams(idx, theId, theHeaders)
        # logger.debug(f"playerParams:\n{json.dumps(dict(playerParams))}")
        try:
            # Notice we are picking HLS format (HTTP Live Streaming)
            playlistUrl = playerParams["streamingData"]["hlsManifestUrl"]
            break
        except (KeyError, TypeError) as err:
            logger.error(f"Parameter {err} NOT found on access method #{idx}")
            logger.debug(f"Content received is:\n{playerParams}")
        idx += 1

    return playlistUrl


def _selectPlayerParams(accessMethod, theId, theHeaders):
    # Handles different known methods to obtain YT's player parameters
    logger.info(f"Attempting YT access method #{accessMethod}")

    if accessMethod == 1:
        tubeUrl= f"https://www.youtube.com/watch?v={theId}"
        if GLOBALS.useTestData:
            testFile = "testResources/YTSeoul.html"
            logger.debug(f"Reading from test file '{testFile}'")
            with open(testFile, 'r') as f:
                mainHtml = f.read()
        else:
            try:
                response = GLOBALS.netUtils.get(tubeUrl, headers=theHeaders)
            except:
                raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {tubeUrl}") from None
            mainHtml = response.text

        try:
            regex = r"var ytInitialPlayerResponse = (.*);var meta = document\.createElement"
            pleya = _doRegex("ytInitialPlayerResponse", regex, mainHtml)
            playerParams = json.loads(pleya)
            return playerParams
        except HPatrolError:
            # First method to get data didn't work; try the next
            return
        except json.decoder.JSONDecodeError as err:
            logger.error(f'Unable to parse YT player params: {err}')
            logger.debug(f"Content received is:\n{pleya}")
            return

    elif accessMethod == 2:
        tubeUrl= f"https://www.youtube-nocookie.com/embed/{theId}?autoplay=1&amp;state=1"
        theKeys = _getContactKeys(tubeUrl, theHeaders)

        if GLOBALS.useTestData:
            testFile = "testResources/YTplayerParams.json"
            logger.debug(f"Reading from test file '{testFile}'")
            with open(testFile, 'r') as f:
                playerParams = json.loads(f.read())
                return playerParams
        else:
            playerUrl = f"https://www.youtube-nocookie.com/youtubei/v1/player?key={theKeys['apiKey']}&prettyPrint=false"
            newHeaders = GLOBALS.netUtils.sessionObj.headers
            newHeaders['Cache-Control'] = "no-cache"
            newHeaders['Content-Type'] = "application/json"
            newHeaders['X-Youtube-Bootstrap-Logged-In'] = "false"
            newHeaders['X-Goog-Visitor-Id'] = theKeys["visitorData"]
            newHeaders['Origin'] = "https://www.youtube-nocookie.com"
            newHeaders['X-Youtube-Client-Name'] = theKeys["clientName"]
            newHeaders['X-Youtube-Client-Version'] = theKeys["clientVersion"]
            logger.info(f'Headers modified for POST:\n{json.dumps(dict(newHeaders))}')

            payload = _tubePlayerPostSettings(theId, theKeys["visitorData"], theKeys["clientVersion"], newHeaders)
            try:
                response = GLOBALS.netUtils.post(playerUrl, data=json.dumps(payload), headers=newHeaders, verify=False)
            except Exception as err:
                logger.critical(f'Caught Exception: {err}')
                raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {tubeUrl}") from None

            try:
                playerParams = json.loads(response.text)
                # logger.debug(f"playerParams:\n{json.dumps(dict(playerParams))}")
                return playerParams
            except Exception as err:
                logger.error(f"Unable to parse YT player params: {err}")
                logger.debug(f"Content received is:\n{response.text}")
            return
    else:
        logger.info(f"No YT access method #{accessMethod} available")

    # ********Unable to access parameters********
    raise HPatrolError("Can't find player params")


def _tubePlayerPostSettings(videoId, googId, clientVersion, headers):
    # We don't really know what half of these things are, but it seems to work
    # This was extracted out of the browser's developer console
    # At some point, we should study and understand YT's API parameters
    now = time.time()

    allSettings = {
        "videoId": videoId,
        "context": {
            "client": {
                "hl": "en",
                "gl": "US",
                "remoteHost":  GLOBALS.perceivedIP,
                "deviceMake": "",
                "deviceModel": "",
                "visitorData": googId,
                "userAgent": headers["User-Agent"],
                "clientName": "WEB_EMBEDDED_PLAYER",
                "clientVersion": clientVersion,
                "osName": "X11",
                "osVersion": "",
                "originalUrl": f"https://www.youtube-nocookie.com/embed/{videoId}?autoplay=1&amp%3Bstate=1",
                "platform": "DESKTOP",
                "clientFormFactor": "UNKNOWN_FORM_FACTOR",
                "browserName": "",
                "browserVersion": "102.0",
                "deviceExperimentId": "ChxOekl3TlRZM01UazBOalEwTkRZNU5qQXdOQT09EPXg_p8GGPXg_p8G",
                "screenWidthPoints": 968,
                "screenHeightPoints": 545,
                "screenPixelDensity": 1,
                "screenDensityFloat": 1,
                "utcOffsetMinutes": 0,
                "userInterfaceTheme": "USER_INTERFACE_THEME_LIGHT",
                "timeZone": "UTC",
                "playerType": "UNIPLAYER",
                "tvAppInfo": {
                    "livingRoomAppMode": "LIVING_ROOM_APP_MODE_UNSPECIFIED"
                },
                "clientScreen": "EMBED"
            },
            "user": {
                "lockedSafetyMode": False
            },
            "request": {
                "useSsl": True,
                "internalExperimentFlags": [],
                "consistencyTokenJars": []
            },
            "adSignalsInfo": {
                "params": [
                    {
                        "key": "dt",
                        "value": f"{int(now*1000)}"
                    },
                    {
                        "key": "flash",
                        "value": "0"
                    },
                    {
                        "key": "frm",
                        "value": "2"
                    },
                    {
                        "key": "u_tz",
                        "value": "0"
                    },
                    {
                        "key": "u_his",
                        "value": "3"
                    },
                    {
                        "key": "u_h",
                        "value": "1600"
                    },
                    {
                        "key": "u_w",
                        "value": "2560"
                    },
                    {
                        "key": "u_ah",
                        "value": "1534"
                    },
                    {
                        "key": "u_aw",
                        "value": "2560"
                    },
                    {
                        "key": "u_cd",
                        "value": "24"
                    },
                    {
                        "key": "bc",
                        "value": "31"
                    },
                    {
                        "key": "bih",
                        "value": "-12245933"
                    },
                    {
                        "key": "biw",
                        "value": "-12245933"
                    },
                    {
                        "key": "brdim",
                        "value": "779,130,779,130,2560,28,1573,1354,968,545"
                    },
                    {
                        "key": "vis",
                        "value": "1"
                    },
                    {
                        "key": "wgl",
                        "value": "true"
                    },
                    {
                        "key": "ca_type",
                        "value": "image"
                    }
                ]
            }
        },
        "playbackContext": {
            "contentPlaybackContext": {
                "html5Preference": "HTML5_PREF_WANTS",
                "lactMilliseconds": "98",
                "referer": f"https://www.youtube-nocookie.com/embed/{videoId}?autoplay=1&amp;state=1",
                "signatureTimestamp": 19415,
                "autoCaptionsDefaultOn": False,
                "autoplay": True,
                "mdxContext": {},
                "playerWidthPixels": 968,
                "playerHeightPixels": 545,
                "ancestorOrigins": []
            }
        },
        "cpn": "z3Go9zZN60JeB7C5",
        "captionParams": {}
    }

    # logger.debug(f"PlayerSettings:\n{allSettings}\n")
    return allSettings


def _getTubeId(theUrl):
    regex = r"(?:watch\?v=(.*))"
    matches = re.search(regex, theUrl)
    if matches:
        # # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
        # for groupNum in range(0, len(matches.groups())):
        #     groupNum = groupNum + 1
        #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

        # logger.debug(f"\nGROUP1: \n{matches.group(1)}\n")
        videoId = matches.group(1)
        # logger.debug(f"videoId: {videoId}")

    else:
        logger.error("videoId NOT found in accessUrl")
        logger.info("Need accessUrl format to be https://www.youtube.com/watch?v=<videoId>")
        raise HPatrolError("videoId NOT found")

    return videoId


def _getContactKeys(tubeUrl, theHeaders):
    if GLOBALS.useTestData:
        testFile = "testResources/YTnoCookieEmbed.html"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(testFile, 'r') as f:
            embedHtml = f.read()

    else:
        try:
            r = GLOBALS.netUtils.get(tubeUrl, headers=theHeaders)
        except:
            raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {tubeUrl}") from None
        embedHtml = r.text
        # logger.debug(f"Response headers: '{r.headers}'")
        # logger.debug(f"embedHtml: '{embedHtml}'")


    regex = r"(?:\"visitorData\":\"(.*)\",\"userAgent)"
    visitorData = _doRegex("visitorData", regex, embedHtml)

    regex = r"(?:\"INNERTUBE_API_KEY\":\"(.*)\",\"INNERTUBE_API_)"
    apiKey = _doRegex("apiKey", regex, embedHtml)

    regex = r"(?:\"INNERTUBE_CLIENT_VERSION\":\"(.*)\",\"INNERTUBE_CONTEXT\":{\"client)"
    clientVersion = _doRegex("clientVersion", regex, embedHtml)

    regex = r"(?:\"INNERTUBE_CONTEXT_CLIENT_NAME\":(.*),\"INNERTUBE_CONTEXT_CLIENT_VERSION)"
    clientName = _doRegex("clientName", regex, embedHtml)

    return {
          "apiKey": apiKey
        , "clientName":clientName
        , "visitorData":visitorData
        , "clientVersion":clientVersion
        }


# # NOT CURRRENTLY USED
# # Here for if in the future; to consider reading the entire JSON structure
# # and use that instead of regexing the needed values individually
# def NOT_getContactKeys(tubeUrl, theHeaders):
#     if GLOBALS.useTestData:
#         testFile = "testResources/YTnoCookieEmbed.html"
#         logger.debug(f"Reading from test file '{testFile}'")
#         with open(testFile, 'r') as f:
#             embedHtml = f.read()
#     else:
#         try:
#             r = GLOBALS.netUtils.get(tubeUrl, headers=theHeaders)
#         except:
#             raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {tubeUrl}")
#         # logger.debug(f"Response headers: '{r.headers}'")
#         embedHtml = r.text
#         # logger.debug(f"embedHtml: '{embedHtml}'")

#     regex = r"(?:ytcfg\.set\(({\\\"EVENT_ID\\\":.*}),\\\"POST_MESSAGE_ORIGIN)"
#     matches = re.search(regex, embedHtml)
#     if matches:
#         # # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
#         # for groupNum in range(0, len(matches.groups())):
#         #     groupNum = groupNum + 1
#         #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

#         # logger.debug(f"\nGROUP1: \n{matches.group(1)}\n")
#         matchGroup = matches.group(1)
#         theJson = json.loads(matchGroup)
#         logger.debug(f"theJson: {theJson}")

#     else:
#         logger.error("JSON data NOT found in embeded HTML")
#         raise HPatrolError("JSON  NOT found")


def _doRegex(varName, regex, content):
    matches = re.search(regex, content)
    if matches:
        # # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
        # for groupNum in range(0, len(matches.groups())):
        #     groupNum = groupNum + 1
        #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

        # logger.debug(f"\nGROUP1: \n{matches.group(1)}\n")
        theMatch = matches.group(1)
        # logger.debug(f"FOUND {varName}: {theMatch}")

    else:
        logger.error(f"{varName} NOT found")
        logger.debug(f"Content received is:\n{content}")
        raise HPatrolError(f"{varName} NOT found")

    return theMatch
