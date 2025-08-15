# External libraries import statements
import json
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


# This server requires receiving an OPTIONS call before the request
def getPlaylist(jsonConfig) -> str:
    theUrl = jsonConfig["accessUrl"]
    deviceID = jsonConfig["deviceID"]
    theHeaders = jsonConfig["headers"]

    try:
        GLOBALS.netUtils.options(theUrl, headers=theHeaders)

        response = GLOBALS.netUtils.post(theUrl, data=json.dumps(deviceID))
    except:
        raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {theUrl}")

    contents = json.loads(response.text)

    # This is the path to the URL for the avanta-telecom.ru site
    # Modify this code with a generic solution if needed for another OPTIONS site
    playlistUrl = contents["result"]["cam"]

    # logger.debug(playlistUrl)
    return playlistUrl
