"""
This code is to obtain video from a site in the "hngscloud.com" domain

The sequence to obtain the m3u8 file is:
    0) Visit the main site
    1) Construct a URL containing the camera ID, the current time in epoch milliseconds, and an offset time that we create
    using a random number generator. We're not sure why it works, but it seems to
    2) Visit that site, and obtain a "play URL" containing an encoded time and secret key
    3) Visit the play URL site, which finally returns the URL of the m3u8 data
    4) Visit the m3u8 URL and return its contents

    Note that the main site is visited using the default headers. The first contructed URL is visited using an augmented
    set of headers and following sites are visited using a slightly modified version of those headers.

"""


# External libraries import statements
import time
import json
import logging
from random import randrange


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


# Constants
URL_CAM = "https://weixin.hngscloud.com/camera/playUrl?cameraNUm="
URL_TIME = "&videoType=2&videoRate=0&t="
URL_OFFSET_TIME  = "&_="


def getPlaylist(jsonConfig):
    theUrl = jsonConfig['accessUrl']
    theHeaders = jsonConfig['headers']

    # NOTE: Our headers are set up to visit the playlist and then the video URLs - is this really necessary?
    theHeaders.pop('DNT', None)
    #del theHeaders['Host']
    theHeaders.pop('Referer', None)
    theHeaders.pop('Sec-Fetch-Dest', None)
    theHeaders.pop('Sec-Fetch-Mode', None)
    theHeaders.pop('Sec-Fetch-Site', None)
    #del theHeaders['TE']

    # Just visit the base URL; doesn't really return useful information
    if not GLOBALS.useTestData:
        logger.debug("Simulated visit to main site - nothing to see here")
        try:
            r = GLOBALS.netUtils.get(theUrl, headers=theHeaders)
        except:
            raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {theUrl}") from None

    # Construct the 1st URL and then visit it to get the play URL
    nowTimeStr, pastTimeStr = _getTimeStrings()
    firstUrl = URL_CAM + jsonConfig['deviceID'] + URL_TIME + nowTimeStr + URL_OFFSET_TIME + pastTimeStr
    # firstHost = firstUrl.split('/')[2]

    #theHeaders['DNT'] = '1'
    #theHeaders['Host'] = firstHost
    theHeaders['Referer'] = theUrl + '/'
    #theHeaders['Origin'] = 'https://weixin.hngscloud.com'
    #theHeaders['Sec-Fetch-Dest'] = 'empty'
    #theHeaders['Sec-Fetch-Mode'] = 'cors'
    theHeaders['Sec-Fetch-Site'] = 'same-origin'
    theHeaders['TE'] = 'trailers'
    theHeaders['X-Requested-With'] = 'XMLHttpRequest'

    # Visit the play URL site and get the playlist data URL
    if GLOBALS.useTestData:
        playUrl = "https://play.hngscloud.com/live/2dfc05f4-1017-4666-8b0e-0eb4d88b2694.m3u8?txSecret=d875cfc35f065353c12f398fd20a7010&txTime=63C819EA"

    else:
        try:
            r = GLOBALS.netUtils.get(firstUrl, headers=theHeaders)
        except:
            raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {firstUrl}") from None

        # Retrieve the JSON and check the code returned
        try:
            cameraDict = json.loads(r.text)
        except Exception:
            logger.error("No JSON received")
            logger.debug(f"Content received is:\n{r.text}")
            raise HPatrolError("Invalid content")

        try:
            cameraCode = cameraDict["code"]
            cameraData = cameraDict["data"]
        except KeyError as err:
            logger.error(f"Key {err} missing from JSON returned")
            logger.debug(f"Content received is:\n{json.dumps(cameraDict)}")
            raise HPatrolError("Key missing")

        if cameraCode != 200:
            logger.error(f"Wrong code returned from visiting first URL: {cameraCode}")
            logger.debug(f"Content received is:\n{cameraDict}")
            raise HPatrolError("Wrong code returned")

        try:
            playUrl = cameraData["playUrl"]
        except KeyError:
            logger.error("Key 'playUrl' missing from JSON returned")
            logger.debug(f"Content received is:\n{cameraData}")
            raise HPatrolError("Key missing")

    # Visit the play URL site
    playHost = playUrl.split('/')[2]
    theHeaders.pop('X-Requested-With', None)
    theHeaders.pop('TE', None)
    theHeaders['Host'] = playHost
    theHeaders['Origin'] = 'https://weixin.hngscloud.com'
    theHeaders['Sec-Fetch-Site'] = 'same-site'
    if GLOBALS.useTestData:
        playlistUrl = "https://dc1769a42a64a444b68e0fc184768dba.livehwc3.cn/play.hngscloud.com/live/2dfc05f4-1017-4666-8b0e-0eb4d88b2694.m3u8?txTime=63C819EA&edge_slice=true&txSecret=d875cfc35f065353c12f398fd20a7010&user_session_id=e539925c4dfce5eda524c29de8c4ac50&sub_m3u8=true"

    else:
        try:
            r = GLOBALS.netUtils.get(playUrl, headers=theHeaders)
        except:
            raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {playUrl}") from None

        # Capture the playlist URL
        respLines = r.text.split('\n')
        for lin in respLines:
            tmpLin = lin.strip()
            if tmpLin.startswith('https'):
                playlistUrl = tmpLin
                break

    if not playlistUrl:
        logger.error(f"Could not find playlist URL in text returned")
        logger.debug(f"Content received is:\n{r.text}")
        raise HPatrolError(f"No playlist URL")

    return playlistUrl.strip()


# Return current time as an epoch millisecond string,
# and an offset time, determined by a random number generator
def _getTimeStrings():
    millisec = int(time.time() * 1000)
    nowTimeStr = str(millisec)

    pastTime = millisec - randrange(30000, 999000)
    pastTimeStr = str(pastTime)

    return nowTimeStr, pastTimeStr
