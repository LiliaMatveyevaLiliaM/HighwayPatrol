# External libraries import statements
import re
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


def getPlaylist(jsonConfig):
    returnedStr = _handleSubtype(jsonConfig)

    # Cleanup string if necessary; just fixes any potentially escaped characters
    cleanUp = json.loads(f'{{"url":"{returnedStr}"}}')
    theUrl = cleanUp['url']

    try:
        # For cases where the returned string is a partial URL
        if jsonConfig["firstContactData"]["prependUrl"]:
            return f"{jsonConfig['accessUrl']}{theUrl}"
    except KeyError:
        pass
    return theUrl


def _handleSubtype(jsonConfig):
    # Handles different types of firstContact scenarios

    initUrl = jsonConfig["accessUrl"]
    firstContactData = jsonConfig["firstContactData"]

    # Check that subtype is specified before attempting to access the target
    contactType = firstContactData["subtype"]
    logger.info(f"FirstContact subtype: '{contactType}'")

    if GLOBALS.useTestData:
        success = True
        testFile = "testResources/lanoptic_iframe.html"
        # testFile = "testResources/EarthCam - Las Vegas Cams.html"
        logger.debug(f"Reading from TEST file '{testFile}'")
        with open(testFile, 'r') as f:
            testData = f.read()
        class MyClass:
            content = bytes(testData, encoding='utf-8')
            headers = "Testing; No headers here"
            class cookies:
                def get_dict():
                    return {"PHPSESSID":"o29u0k5dtbu4gtmucjjpht5va2"}
        # Here's a slightly different type of test data; keeping for future reference/use
        #     # testData = '[{"cover_image":"https://wolfstream-assets-production.wmscdn.com/screenshots/iJwjLrdSZd_202203021512.jpg","secure_url":"https://cdn.wolfstream.app/stream/z2RWGAr0DQ1b/manifest.m3u8?token=n82JMry0MRaWS6PHLsH0UQ&expires=1646248598","status":2}]'
        #     testData = 'if (mVideo.readyState === 4 || mVideo.readyState === 3) { $(".alert div").css("display", "none");}\n\n\t\t\t }\n\n\t\t\t </script> \n\n<script>\n\n\n\nvar n_url = "https://msk.rtsp.me/2YnCGZ4AQCMD9VDl_ooWhA/1653076966/hls/rdk4h57D.m3u8?ip=18.235.83.242"; \n\n//      $.getScript("https://rtsp.me/embed/logics.js");\n\n//Плей пауза'
        firstResp = MyClass()

    else:
        # FIXME: Handle exceptions specifically
        # Related to
        # FIXME: Modify aimpoint handlers to not force always needing specifying headers
        try:
            firstResp = GLOBALS.netUtils.get(initUrl, headers=jsonConfig["headers"])
        except:
            raise HPatrolError(f"URL access failed from {GLOBALS.perceivedIP} attempting {initUrl}")

    pageContent = firstResp.content.decode('utf-8')

    # React to the specified subtype
    if contactType == "json":
        # As of 09.21.22, not using this subtype
        # This is meant to handle the return of a JSON string from an initial URL
        # The key to use in the received JSON is specified in the firstContactData
        theJson = json.loads(pageContent)
        keyPath = firstContactData["key"].split("/")

        # Navigate the JSON to the key we want
        theUrl = theJson
        for aKey in keyPath:
            theUrl = theUrl[aKey]
        return theUrl


    elif contactType == "cookie":
        lookFor = firstContactData["cookie"]
        allCookies = firstResp.cookies.get_dict()
        logger.debug(f"Cookies Received:  {allCookies}")
        theUrl = firstContactData["urlTemplate"].format(cookie=allCookies[lookFor])
        return theUrl


    elif contactType == "regex":
        regex = jsonConfig["playlistRegex"]
        # regex = r"n_url = (?:\"(https?:\/\/.*)\");"  This works for rtsp.me
        try:
            matches = re.search(regex, pageContent)
        except TypeError:
            logger.error(f"TypeError looking for '{regex}'")
            logger.debug(f"Content received is:\n{pageContent}")
            raise HPatrolError("TypeError")

        if matches:
            # # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
            # for groupNum in range(0, len(matches.groups())):
            #     groupNum = groupNum + 1
            #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

            aString = matches.group(firstContactData["group"])
            return aString

        else:
            logger.error(f"No matches found in pageContent looking for '{regex}'")
            logger.debug(f"Content received is:\n{pageContent}")
            raise HPatrolError(f"No matches found in pageContent looking for '{regex}'")

            # TODO: Recognize video-off events
            # Received this on 2022-06-07T13:59:59.453-04:00
            # <div class="video_off"> <span>Working hours  <h2>00:00 - 23:59</h2><small> (Қазақстан / Астана)</small><br> </span></div>


    elif contactType == "regexJson":
        regex = jsonConfig["playlistRegex"]
        keyPath = firstContactData["key"].split("/")

        try:
            matches = re.search(regex, pageContent)
        except TypeError:
            logger.error(f"TypeError looking for '{regex}'")
            logger.debug(f"Content received is:\n{pageContent}")
            raise HPatrolError("TypeError")

        if matches:
            # # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
            # for groupNum in range(0, len(matches.groups())):
            #     groupNum = groupNum + 1
            #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

            theJson = json.loads(matches.group(1))
            # logger.debug(f"\nGROUP1: \n'{theJson}'\n")
        else:
            logger.error(f"No matches found in pageContent looking for '{regex}'")
            logger.debug(f"Content received is:\n{pageContent}")
            raise HPatrolError(f"No matches found in pageContent looking for '{regex}'")

        # Navigate the JSON to the key we want
        theUrl = theJson
        for aKey in keyPath:
            theUrl = theUrl[aKey]

        return theUrl


    elif contactType == "regexReplace":
        regex = jsonConfig["playlistRegex"]
        try:
            matches = re.search(regex, pageContent)
        except TypeError:
            logger.error(f"TypeError looking for '{regex}'")
            logger.debug(f"Content received is:\n{pageContent}")
            raise HPatrolError("TypeError")

        if matches:
            aString = matches.group(firstContactData["group"])
            try:
                aString = aString.replace(firstContactData["oldString"], firstContactData["newString"])
                # suffix can be empty "" or contain final part of URL if needed (ie: "/playlist.m3u8")
                aString += firstContactData["suffix"]
                return aString
            except KeyError as e:
                logger.error(f"{e} not specified in aimpoint file.")
                logger.error(f"`oldString`, `newString` and `suffix` are required in `firstContactData` for subtype `regexReplace`")
                raise HPatrolError(f"Missing value {e} in aimpoint file for subtype `regexReplace`")
        else:
            logger.error(f"No matches found in pageContent looking for '{regex}'")
            logger.debug(f"Content received is:\n{pageContent}")
            raise HPatrolError(f"No matches found in pageContent looking for '{regex}'")


    # # 01.27.2025
    # # TODO: Add a regexTemplate subtype
    # # This would take groups from playlistRegex and put them where needed in urlTemplate to build the URL
    # # e.g. for the k-live.ru domain, with a playlistRegex as
    # # "<input type=\"hidden\" id=\"design_sr\" value=\"(https://.*)\"/><input type=\"hidden\" id=\"design_ty(.*)design_id\" value=\"(.*)\"/><input type=\"hidden\" id=\"design_th"
    # # get design_sr and design_id as groups 1 and 3
    # # and put them into urlTemplate https://<group1>/hls/<group3>/playlist.m3u8"
    # # This type can substitute the above regexReplace and also eliminate the 'suffix' element; remember to edit the README
    # 
    # elif contactType == "regexTemplate":
    #     regex = jsonConfig["playlistRegex"]
    #     try:
    #         matches = re.search(regex, pageContent)
    #     except TypeError:
    #         logger.error(f"TypeError looking for '{regex}'")
    #         logger.debug(f"Content received is:\n{pageContent}")
    #         raise HPatrolError("TypeError")
    #     .
    #     .
    #     .
    #     theUrl = firstContactData["urlTemplate"].format(groupXYZ=groupXYZ)
    #     return theUrl

    else:
        logger.error('FirstContact subtype not specified')
        raise HPatrolError('FirstContact subtype not specified')
