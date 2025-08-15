# External libraries import statements
import os
import re
import time
import json
import m3u8
import logging
import subprocess
import urllib.parse
import datetime as dt
import concurrent.futures
from requests import Response
from datetime import timezone as tz
from requests.exceptions import SSLError


# This application's import statements
try:
    # These are for when running in an EC2
    import systemSettings
    from exceptions import *
    import superGlblVars as GLOBALS
    from superGlblVars import config
    from orangeUtils import utils as ut
    from addons import ufaNetParse as un
    from addons import rtspMeParse as rt
    from addons import optionsAddon as op
    from addons import iVideonParse as iv
    from addons import youtubeParse as yt
    from addons import ganDongParse as gd
    from addons import bazaNetParse as bn
    from utils import hPatrolUtils as hput
    from addons import ipCamLiveParse as ip
    from addons import hngsCloudParse as hc
    from addons import firstContactParse as fc
    from collectionTypes import CollectionType

except ModuleNotFoundError as err:
    # These are for when running in a Lambda
    print(f"Loading module for lambda execution: {__name__}")
    from src.python.exceptions import *
    from src.python import systemSettings
    from src.python.superGlblVars import config
    from src.python.orangeUtils import utils as ut
    from src.python import superGlblVars as GLOBALS
    from src.python.addons import ufaNetParse as un
    from src.python.addons import rtspMeParse as rt
    from src.python.addons import optionsAddon as op
    from src.python.addons import iVideonParse as iv
    from src.python.addons import youtubeParse as yt
    from src.python.addons import ganDongParse as gd
    from src.python.addons import bazaNetParse as bn
    from src.python.utils import hPatrolUtils as hput
    from src.python.addons import ipCamLiveParse as ip
    from src.python.addons import hngsCloudParse as hc
    from src.python.addons import firstContactParse as fc
    from src.python.collectionTypes import CollectionType


logger = logging.getLogger()


def handleVideos(collType, prefixBase, ap, lambdaContext=None):
    # Note that Collectors are assumed to be running because they are indeed supposed to be
    # running; this is handled by the Scheduler; hence start time is not checked; only stop time
    breakPoint, theSleep, sleepyFraction = hput.calculateExecutionStop(
        ap, lambdaContext
    )

    wrkBucketName = hput.pickBestBucket(ap, "wrkBucket")

    try:
        singleCollector = True == ap["singleCollector"]
    except KeyError:
        singleCollector = False

    try:
        decoy = True == ap["decoy"]
    except KeyError:
        decoy = False

    try:
        if collType == CollectionType.FIRST:
            # Sometimes we need to obtain the m3u8 URL from a different URL
            logger.info("Type selected: firstContact")
            playlistUrl = fc.getPlaylist(ap)
            newHeaders = ap["headers"]

        elif collType == CollectionType.IVIDEO:
            logger.info("Type selected: iVideon")
            playlistUrl = iv.getPlaylist(ap)
            newHeaders = None

        elif collType == CollectionType.M3U:
            logger.info("Type selected: m3u8")
            newHeaders = ap["headers"]
            playlistUrl = ap["accessUrl"]

        elif collType == CollectionType.UFANET:
            logger.info("Type selected: ufaNet")
            playlistUrl = un.getPlaylist(ap)
            newHeaders = None

        elif collType == CollectionType.RTSPME:
            logger.info("Type selected: rtsp.me")
            playlistUrl = rt.getPlaylist(ap)
            newHeaders = ap["headers"]

        elif collType == CollectionType.IPLIVE:
            logger.info("Type selected: ipcamlive.com")
            playlistUrl = ip.getPlaylist(ap)
            newHeaders = ap["headers"]

        elif collType == CollectionType.HNGCLD:
            logger.info("Type selected: hngscloud.com")
            playlistUrl = hc.getPlaylist(ap)
            newHeaders = ap["headers"]

        elif collType == CollectionType.YOUTUB:
            logger.info("Type selected: youtubeStream")
            playlistUrl = yt.getPlaylist(ap)
            newHeaders = ap["headers"]

        elif collType == CollectionType.GNDONG:
            logger.info("Type selected: gandongyun.com")
            playlistUrl = gd.getPlaylist(ap)
            newHeaders = ap["headers"]

        elif collType == CollectionType.BAZNET:
            logger.info("Type selected: baza.net")
            playlistUrl = bn.getPlaylist(ap)
            newHeaders = None

        elif collType == CollectionType.OPTION:
            logger.info("Type selected: OPTIONS call")
            playlistUrl = op.getPlaylist(ap)
            newHeaders = None

        else:
            logger.error("Collection type undefined")
            raise HPatrolError("Collection type undefined")

    except KeyError as err:
        logger.error("Parameter unspecified in input configuration")
        logger.error(f"Be sure to specify {err} in JSON file")
        raise HPatrolError("Parameter unspecified in input configuration")

    except ConnectionError as err:
        raise HPatrolError(f"Error getting playlist: {err}")

    if GLOBALS.perceivedIP in playlistUrl:
        logger.warning("WARNING: Found our IP in the URL!!!")
        logger.info(f"Attempting to remove our IP from: '{playlistUrl}'")
        playlistUrl = playlistUrl.replace(f"ip={GLOBALS.perceivedIP}", "")

    # Check before we go further
    if urllib.parse.urlparse(playlistUrl).scheme == "":
        logger.error(f"Playlist URL seems invalid: ({playlistUrl})")
        raise HPatrolError("Invalid URL")

    logger.info(f"Playlist URL is '{playlistUrl}'")
    if not playlistUrl:
        raise HPatrolError("Empty playlist URL")

    allSegments = []
    while True:
        try:
            tsList, segIniter, tsDurations = _getPlaylist(playlistUrl, ap, newHeaders)
        except ConnectionError as err:
            logger.warning(err)
            break

        try:
            newM3u8List = _getTsFiles(
                ap, playlistUrl, tsList, newHeaders, allSegments, tsDurations, segIniter
            )
        except KeyError as err:
            logger.exception(f"Execution error:::{err}")
            break

        # One long list of segments to analyze as a whole and in case we need to concat them
        allSegments = allSegments + newM3u8List

        # We're only intended to run once
        if not singleCollector:
            logger.info(f"Not a singleCollector request; breaking out")
            break

        if hput.itsTimeToBail(lambdaContext, breakPoint, theSleep):
            break
        # Don't sleep if we're just using the test data
        if not GLOBALS.useTestData:
            logger.info(
                f"Sleeping {sleepyFraction*100:g}% of the poll frequency: {theSleep/1000:.2f}s"
            )
            time.sleep(theSleep / 1000)
    logger.info("Enough iterations for now")

    if len(allSegments) == 0:
        logger.warning("No new .ts files captured")
        GLOBALS.sqsUtils.sendMessage(config["statusQueue"], {"aimpoint": ap, "isCollecting": False})
        return

    if decoy:
        GLOBALS.sqsUtils.sendMessage(config["statusQueue"], {"aimpoint": ap, "isCollecting": True})
        # Don't upload; not even the .m3u8
        logger.info(f"Decoy aimpoint; NOT pushing to S3")
        return

    finalSegments = _uploadSegments(ap, wrkBucketName, allSegments, prefixBase)
    if not finalSegments:
        logger.warning(f"No new segments found")
        GLOBALS.sqsUtils.sendMessage(config["statusQueue"], {"aimpoint": ap, "isCollecting": False})
        return

    GLOBALS.sqsUtils.sendMessage(config["statusQueue"], {"aimpoint": ap, "isCollecting": True})
    return

    # 12.11.24 Ray: Stopping the creation of m3u8s since we don't use the Copier anymore
    # TODO: Consider deleting all m3u8 creation code; most probably unnecessary now
    #       Unsure at this point; may want to keep in case we need again in the future
    # # Prepare the list to be used as an m3u8 file
    # finalPlaylist = ["#EXTM3U"]  # m3u header in first position
    # finalPlaylist.extend(finalSegments)
    # finalPlaylist.append("")  # Empty line at end

    # # Once all segments are collected, save the m3u8 file
    # m3u8Key = (
    #     f"{prefixBase}/{hput.formatNameBase(ap['filenameBase'], ap['deviceID'])}.m3u8"
    # )
    # logger.info(f"Saving m3u8 as '{m3u8Key}'")
    # GLOBALS.S3utils.pushDataToS3(wrkBucketName, m3u8Key, "\n".join(finalPlaylist))


def _determineIfFmp4(baseUrl, m3u8Str, useCurl, headers=None):
    #  To be viewable, segments of the format fMP4 (fragmented MP4) require a "segment initializer"; need to get it
    if not "#EXT-X-MAP" in m3u8Str:
        return None

    # Need the URI of the segment initialization portion
    regex = r"#EXT-X-MAP:URI=\"(.*)\""
    matches = re.search(regex, m3u8Str)
    if matches:
        # # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
        # for groupNum in range(0, len(matches.groups())):
        #     groupNum = groupNum + 1
        #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

        segUrl = matches.group(1)
        logger.info(f"Segment initer identified: '{segUrl}'")
    else:
        logger.error(
            f"No matches found for segment initialization; looking for '{regex}'"
        )
        logger.debug(f"Content received is:\n{m3u8Str}")
        raise HPatrolError(f"No matches found looking for '{regex}'")

    # See if we don't have a full URL
    if urllib.parse.urlparse(segUrl).netloc == "":
        temp = baseUrl.split("/")
        if segUrl[0] == "/":
            newTemp = temp[:3]  # grab just the "scheme://<netloc>" part
            newTemp.append(segUrl[1:])  # grab the segUrl w/out the initial '/'
            temp = newTemp
        else:
            temp[-1] = segUrl  # substitute the last portion only
        theUrl = "/".join(temp)
    else:
        theUrl = segUrl

    if GLOBALS.useTestData:
        segInit = b"AAAAIGZ0eXBpc29tAAAAAGlzb21hdmMxbXA0MmRhc2gAAALCbW9vdgAAAGxtdmhkAAAAAOBk2B7gZNgeAAFfkAAAAAAAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABVpb2RzAAAAABAHAE/////+/wAAADhtdmV4AAAAEG1laGQAAAAAAAAAAAAAACB0cmV4AAAAAAAAAAEAAAABAAAAAQAAAAEAAAAAAAACAXRyYWsAAABcdGtoZAAAAA/gZNge4GTYHgAAAAEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAEAAAAAHgAAABDgAAAAAAZ1tZGlhAAAALG1kaGQBAAAAAAAAAOBk2B4AAAAA4GTYHgABX5AAAAAAAAAAAAAAAAAAAAAtaGRscgAAAAAAAAAAdmlkZQAAAAAAAAAAAAAAAFZpZGVvSGFuZGxlcgAAAAE8bWluZgAAABR2bWhkAAAAAQAAAAAAAAAAAAAAOmRpbmYAAAAyZHJlZgAAAAAAAAABAAAAInVybCAAAAABaHR0cHM6Ly9mbHVzc29uaWMuY29tLwAAAOZzdGJsAAAAmnN0c2QAAAAAAAAAAQAAAIphdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAB4AEOABIAAAASAAAAAAAAAABHwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAGP//AAAAJGF2Y0MBTQAo/+EADWdNQCiVoB4AiflmwEABAARo7jyAAAAAEHBhc3AAAAABAAAAAQAAABBzdHNjAAAAAAAAAAAAAAAQc3RjbwAAAAAAAAAAAAAAEHN0dHMAAAAAAAAAAAAAABRzdHN6AAAAAAAAAAAAAAAA"
    else:
        try:
            resp = GLOBALS.netUtils.get(theUrl, headers=headers, useCurl=useCurl)
            segInit = resp.content
        except:
            raise ConnectionError(
                f"URL access failed from {GLOBALS.perceivedIP} attempting {theUrl}"
            ) from None
    logger.info("Obtained segment initializer")

    return segInit


def _playlistRequest(useCurl: bool, headers: dict, url: str) -> Response:
    """Make GET request for the playlist"""
    try:
        if headers:
            m3u8Resp = GLOBALS.netUtils.get(url, headers=headers, useCurl=useCurl)
        else:
            m3u8Resp = GLOBALS.netUtils.get(url, useCurl=useCurl)
        return m3u8Resp
    except SSLError as sslErr:
        # logger.error(sslErr)
        logger.info("SSL Error - switching to unverified certs")
        GLOBALS.netUtils.disableCertCheck()
        if headers:
            m3u8Resp = GLOBALS.netUtils.get(url, headers=headers, useCurl=useCurl)
        else:
            m3u8Resp = GLOBALS.netUtils.get(url, useCurl=useCurl)
        return m3u8Resp
    except Exception as err:
        logger.error(err)
        raise ConnectionError(
            f"URL access failed from {GLOBALS.perceivedIP} attempting {url}"
        ) from None


def _getPlaylist(url, ap, headers=None):
    # From m3u8 URL, get ts files

    useCurl = ap.get("useCurl", False)
    attempts = 3  # Try x times before giving up
    for x in range(attempts):
        if GLOBALS.useTestData:

            class MyClass:
                content = b"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:17\n#EXT-X-MEDIA-SEQUENCE:18902\n#EXTINF:8.333333,\nplaylist49q18902.ts\n#EXTINF:8.333333,\nplaylist49q18903.ts\n#EXTINF:8.333333,\nplaylist49q18904.ts\n#EXTINF:16.666667,\nplaylist49q18905.ts\n#EXTINF:8.333333,\nplaylist49q18906.ts\n#EXTINF:8.333333,\nplaylist49q18907.ts\n#EXTINF:8.333333,\nplaylist49q18908.ts\n#EXTINF:8.333333,\nplaylist49q18909.ts\n#EXTINF:16.666667,\nplaylist49q18910.ts\n#EXTINF:8.333333,\nplaylist49q18911.ts\n#EXTINF:8.333333,\nplaylist49q18912.ts\n#EXTINF:8.333333,\nplaylist49q18913.ts\n#EXTINF:8.333333,\nplaylist49q18914.ts\n#EXTINF:16.666667,\nplaylist49q18915.ts\n#EXTINF:8.333333,\nplaylist49q18916.ts\n#EXTINF:8.333333,\nplaylist49q18917.ts\n#EXTINF:8.333333,\nplaylist49q18918.ts\n#EXTINF:8.333333,\nplaylist49q18919.ts\n#EXTINF:16.666667,\nplaylist49q18920.ts\n#EXTINF:8.333333,\nplaylist49q18921.ts\n"
                headers = "Testing; No headers here"

            m3u8Resp = MyClass()
        else:
            m3u8Resp = _playlistRequest(useCurl, headers=headers, url=url)
        m3u8Str = m3u8Resp.content.decode("utf-8")
        logger.debug(f"M3U CONTENTS:\n{m3u8Str}")

        playlistObj = m3u8.loads(m3u8Str)

        # Handle m3u within m3u's
        if playlistObj.is_variant:
            logger.info(f"Received m3u8 variant; analyzing")
            newUrl = _getSubM3uUrl(url, playlistObj)
            tsList, fmp4Init, tsDurations = _getPlaylist(newUrl, ap)

            # Sometimes the subM3uUrl changes us to a different working path
            # for ex.: we went orginally to
            #       https://theSite.net/somePath/index.m3u8
            # and the video segments are in
            #       https://theSite.net/somePath/addedPath/index.m3u8
            # but this addedPath is not in the final playlist, and neither
            # does the calling function have it, so we need to add it

            # Don't do anything if the playlist elements start at the server root
            if tsList[0][0] == "/":
                pass
            # If the elements contain full URLs, no need to process this either
            elif urllib.parse.urlparse(tsList[0]).netloc == "":
                head = url[:url.rfind("/")]
                addedPath = newUrl[:newUrl.rfind("/")].replace(head, "")
                try:
                    if addedPath[0] == "/":
                        addedPath = addedPath.replace("/", "", 1)
                    tsList = [addedPath + "/" + one for one in tsList]
                    logger.debug(f"Modified Playlist is: {tsList}")
                except IndexError:
                    # There was no addedPath to add
                    pass
            return tsList, fmp4Init, tsDurations

        tsList = [x.uri for x in playlistObj.segments]
        tsDurations = [x.duration for x in playlistObj.segments]

        if _isPlaylistValid(tsList):
            logger.info(f"Total segments in playlist is {len(tsList)}: {tsList}")
            fmp4Init = _determineIfFmp4(url, m3u8Str, useCurl, headers)
            return tsList, fmp4Init, tsDurations

        else:
            logger.warning("Invalid playlist")

        # Wait a bit before retrying
        ut.randomSleep(3, 10)

    raise ConnectionError(f"Couldn't obtain a valid playlist after {attempts} attempts")


def _getSubM3uUrl(url, playlistObj):
    # Receives an m3u8 object (defined by the m3u8 library)
    # Returns the best m3u8 to go after

    if len(playlistObj.playlists) > 1:
        bWidth = 0
        for aLine in playlistObj.playlists:
            # Grab the highest bandwidth option
            # There are other types of size discriminators; using only this one now
            # Implement the rest when/if we see them
            if aLine.stream_info.bandwidth > bWidth:
                bWidth = aLine.stream_info.bandwidth
                newOption = aLine.uri

    else:
        newOption = playlistObj.playlists[0].uri

    # See if we don't have a full URL
    if urllib.parse.urlparse(newOption).netloc == "":
        temp = url.split("/")
        if newOption[0] == "/":
            newTemp = temp[:3]  # grab just the "scheme://<netloc>" part
            newTemp.append(newOption[1:])  # grab the segUrl w/out the initial "/"
            temp = newTemp
        else:
            temp[-1] = newOption  # substitute the last portion only
        newUrl = "/".join(temp)
    else:
        newUrl = newOption

    # logger.debug(f"Will use variant: '{newUrl}'")
    return newUrl


def _isPlaylistValid(m3u8List):
    # Some playlists are coming back with invalid .ts files:
    #     #EXTINF:3.700000,
    #     #EXT-X-DISCONTINUITY
    #     rdk4h57D-1-403.ts     <----invalid file
    #     #EXT-X-DISCONTINUITY

    if len(m3u8List) == 0:
        logger.warning("Empty playlist")
        return False

    for aLine in m3u8List:
        if "-403.ts" in aLine:
            logger.warning(f"Invalid playlist detected: {m3u8List}")
            return False

    return True


def _getTsFiles(
    targetConfig, playlistUrl, tsList, newHeaders, previousSegments, tsDurations, segIniter=None
):
    useCurl = targetConfig.get("useCurl", False)

    # Decompose the playlist URL to use its portions later
    parsedPlaylist = urllib.parse.urlparse(playlistUrl)

    # Iterate over tsList to download the video segment files
    fCount = 0
    m3u8List = []
    dedupSet = set(
        [x["hash"] for x in previousSegments]
    )  # sets are faster than lists for lookup
    for idx, tsEntry in enumerate(tsList):
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and idx == 4:
            logger.debug(f"Not running on PROD; exiting before processing video #{idx}")
            break

        # Get the video segment file
        if GLOBALS.useTestData:
            testFile = "testResources/testVideo.ts"
            logger.debug(f"Reading from test file '{testFile}'")
            with open(testFile, "rb") as f:
                videoContent = f.read()

            class MyClass:
                content = videoContent
                headers = {}

            tsResp = MyClass()

        else:
            # Figure out how the playlist is structured to compose the correct URL
            if urllib.parse.urlparse(tsEntry).netloc != "":
                # Sometimes the playlist contains full URLs
                tsUrl = tsEntry

            elif tsEntry[0] == "/":
                # For cases where the playlist element starts at the server root
                tsUrl = urllib.parse.urlunparse(
                    urllib.parse.ParseResult(
                        scheme=parsedPlaylist.scheme,
                        netloc=parsedPlaylist.netloc,
                        path=tsEntry,
                        params=None,
                        query=None,
                        fragment=None,
                    )
                )

            else:
                # Simple concatenate; last slash is important
                baseUrl = playlistUrl.split("/")[:-1]  # eliminate the .m3u8 portion
                tsAccess = "/".join(baseUrl) + "/"
                tsUrl = tsAccess + tsEntry

            try:
                tsResp = GLOBALS.netUtils.get(tsUrl, headers=newHeaders, useCurl=useCurl)
            except Exception:
                logger.warning(f"Unable to obtain {tsEntry}; continuing")
                continue
        logger.info(f"Retrieved '{os.path.basename(tsEntry)}'")
        thisHash = ut.getHashFromData(tsResp.content)

        # Note: This dedup is local for the right-now execution
        # Later there's another dedup check against S3 for system-wide dedup
        if thisHash in dedupSet:
            logger.info(f"Ignored; segment previously captured ({thisHash})")
            continue
        dedupSet.add(thisHash)

        # Gave up on querying target's lastModDate; will always use our own timestamp
        # At one point we were getting the same lastModDate throughout the same day
        tsLastModDate = int(time.time())

        logger.info(
            f"Using timestamp as "
            f"{dt.datetime.fromtimestamp(tsLastModDate, tz=tz.utc).isoformat()} ({tsLastModDate})"
        )
        ourTsFilename = f"{hput.formatNameBase(targetConfig['filenameBase'], targetConfig['deviceID'])}_{tsLastModDate}.ts"

        # Save segment locally
        localFilenameAndPath = f"{config['workDirectory']}/{ourTsFilename}"
        if os.path.isfile(localFilenameAndPath):
            logger.info(f"Already have file '{ourTsFilename}'")
            # Change the filename so we don't overwrite ourselves
            # Using a period in ".{fCount}.ts" to support sorting later
            ourTsFilename = f"{hput.formatNameBase(targetConfig['filenameBase'], targetConfig['deviceID'])}_{tsLastModDate}.{fCount:02d}.ts"
            logger.info(f"Renaming as '{ourTsFilename}'")
            localFilenameAndPath = f"{config['workDirectory']}/{ourTsFilename}"

        with open(localFilenameAndPath, "wb") as f:
            if segIniter:
                logger.info("Prefixing with the initialization segment")
                f.write(segIniter + tsResp.content)
            else:
                f.write(tsResp.content)
        theSize = ut.sizeofFormat(len(tsResp.content))
        logger.debug(f"Saved as '{localFilenameAndPath}' ({theSize})")
        m3u8List.append({"file": ourTsFilename, "hash": thisHash})

        # How many new video segments have we actually gotten
        fCount += 1

        try:
            if targetConfig["honorExtinf"] == True:
                time.sleep(tsDurations[idx])
        except (KeyError, IndexError):
            # Aimpoints may not have the honorExtinf key, so avoid raising here
            pass
    if fCount == 0:
        logger.info("No new .ts files detected; could be harmless system overlap")

    logger.info(
        f"Finished collecting m3u8 and {fCount} ts file{'s' if fCount > 1 else ''}"
    )

    return m3u8List


def _uploadSegments(targetConfig, bucketName, origList, prefixBase):
    try:
        doConcat = True == targetConfig["concatenate"]
    except KeyError:
        doConcat = False

    try:
        singleCollector = True == targetConfig["singleCollector"]
    except KeyError:
        singleCollector = False

    if len(origList) > 1:
        sortedFilesList = _fixTsFilesOrder(origList)
    else:
        sortedFilesList = origList
    # Note! We're pushing the files by the new sorted order but using the original name's order
    # We rather do this than spend time renaming the files on the filesystem, and then pushing
    # This may cause a slight and negligible discrepancy of epoch name to when it was really got
    # IMPORTANT! Notice that this means that sortedFilesList has the right order of videos, but
    # origList has the right order of names according to the timestamp when they were collected

    finalList = []
    if doConcat:
        try:
            listToConcat = [i["file"] for i in sortedFilesList]
            concatedFile = ut.concatFiles(
                listToConcat, config["workDirectory"], GLOBALS.onProd
            )
            if singleCollector:
                # If in singleCollector and concatenated, the final file won't ever have a dup,
                # unless, of course, we run another collector at the exact time to the exact target
                # and no, the probabilities of that are near nil; don't waste effort.
                theHash = None
            else:
                theHash = ut.getHashFromFile(config["workDirectory"], concatedFile)

            if not _wasSaveSuccessful(
                concatedFile, prefixBase, bucketName, origList[0]["file"], theHash
            ):
                raise HPatrolError("Error pushing to S3")
        except FileNotFoundError as err:
            logger.error(err)

        # Since we concatenated, only use the one filename
        finalList.append(origList[0]["file"])

    else:
        # If no concat, just save them to the bucket
        # Note that we're pushing the files by the new sorted names order instead of the original
        # We rather do this than spend time renaming the files on the filesystem, and then pushing
        # This may cause a slight and negligible discrepancy of epoch name to when it was really got

        # Executor dictionary for parallel uploads
        executers  = {}

        # Reference https://docs.python.org/3/library/concurrent.futures.html#threadpoolexecutor
        with concurrent.futures.ThreadPoolExecutor(max_workers=GLOBALS.upThreads) as executor:
            for idx, aTsFile in enumerate(sortedFilesList):
                fileNamePath = os.path.join(config["workDirectory"], aTsFile["file"])
                # Here, take the name out of origList but based on the order of sortedFilesList
                finalFileName = origList[idx]["file"]

                # Schedule the callable function _wasSaveSuccesful with its parameters
                futureObj = executor.submit(_wasSaveSuccessful, fileNamePath, prefixBase, bucketName, finalFileName, aTsFile["hash"])

                # The executers dictionary looks like this
                #   Key:   ThreadPoolExecutor future object
                #   Value: {finalFileName, atsFile["file"]}
                # The return value of each call is retrieved below with future.result()
                executers[futureObj] = {"finalFileName": finalFileName, "file": aTsFile["file"]}

            # Keep track of all completed executers and work with results
            # Reference https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.as_completed
            for future in concurrent.futures.as_completed(executers):
                try:
                    finalFileName = executers[future]["finalFileName"]
                    file = executers[future]["file"]
                    # If save was sucessful add to list
                    if future.result():
                        finalList.append(finalFileName)
                    else:
                        logger.error(f"Segment {file} was NOT pushed to S3!")
                except Exception as exc:
                    logger.error(f"Exception from '{finalFileName}' :::{exc}")
            finalList.sort(key=hput.naturalKeys)
    return finalList


def _wasSaveSuccessful(filetoSave, prefixBase, bucketName, s3FileName, theHash):
    # Note: On this dup-check technique we put the hash as a filename,
    # on other dup-checks, we put the hash in the file contents
    # FIXME: Add a target discriminator to the hashfiles location
    #        It seems we're starting to see MD5 collisions
    if theHash:
        if GLOBALS.S3utils.isFileInS3(
            bucketName, f"{GLOBALS.s3Hashfiles}/{theHash}.md5"
        ):
            logger.info(f"Ignored; {s3FileName} previously captured ({theHash})")
            return False

    if GLOBALS.S3utils.pushToS3(
        filetoSave,
        prefixBase,
        bucketName,
        s3BaseFileName=s3FileName,
        deleteOrig=GLOBALS.onProd
    ):
        if theHash:
            if not GLOBALS.S3utils.createEmptyKey(
                bucketName, f"{GLOBALS.s3Hashfiles}/{theHash}.md5"
            ):
                logger.warning("Could not create MD5 file; ignoring its creation")
        return True

    return False


def _fixTsFilesOrder(tsList):
    logger.info("Checking segments correctness; may take a while if many")

    toSort = []  # a list of dictionaries
    for aTsFile in tsList:
        localFilePath = f"{config['workDirectory']}/{aTsFile['file']}"

        # Get frame's metadata
        commandString = f"{config['ffprobe']} -hide_banner -show_frames -print_format json {localFilePath}".split()
        # logger.debug(f"commandString: {commandString}")
        ffprobeResult = subprocess.run(commandString, capture_output=True, text=True)

        if ffprobeResult.returncode != 0:
            # Ignore and delete problematic frames; don't include them in the final list
            logger.error(
                f"Frame ignored {ffprobeResult.stderr} (ffprobeResult.returnCode={ffprobeResult.returncode})"
            )
            try:
                if GLOBALS.onProd:
                    os.remove(localFilePath)
            except FileNotFoundError:
                pass
        else:
            videoInfo = json.loads(ffprobeResult.stdout)
            # logger.debug(json.dumps(videoInfo)) # Print ffprobe's raw JSON result
            try:
                firstFrame = videoInfo["frames"][0]
                lastFrame = videoInfo["frames"][-1]
            except IndexError:
                logger.error("FFprobe data does not contain frames; segment ignored")
                logger.debug(videoInfo)
                try:
                    if GLOBALS.onProd:
                        os.remove(localFilePath)
                except FileNotFoundError:
                    pass
                continue

            try:
                pktPts1st = firstFrame["pkt_pts"]
            except KeyError:
                logger.error(
                    "First frame does not contain presentation timestamp (pkt_pts); trying the next"
                )
                firstFrame = videoInfo["frames"][1]
                try:
                    pktPts1st = firstFrame["pkt_pts"]
                except KeyError:
                    logger.error(
                        "Second frame does not contain presentation timestamp (pkt_pts); segment ignored"
                    )
                    logger.debug(json.dumps(videoInfo))
                    try:
                        if GLOBALS.onProd:
                            os.remove(localFilePath)
                    except FileNotFoundError:
                        pass
                    continue

            try:
                pktPtsLst = lastFrame["pkt_pts"]
            except KeyError:
                logger.error(
                    "Last frame does not contain presentation timestamp (pkt_pts); trying the next"
                )
                lastFrame = videoInfo["frames"][-2]
                try:
                    pktPtsLst = lastFrame["pkt_pts"]
                except KeyError:
                    logger.error(
                        "Frame does not contain presentation timestamp (pkt_pts); segment ignored"
                    )
                    logger.debug(json.dumps(videoInfo))
                    try:
                        if GLOBALS.onProd:
                            os.remove(localFilePath)
                    except FileNotFoundError:
                        pass
                    continue

            toSort.append(
                {
                    "first": pktPts1st,
                    "last": pktPtsLst,
                    "file": aTsFile["file"],
                    "hash": aTsFile["hash"]
                }
            )

    # Sort the files by the frame's presentation timestamp (pkt_pts; PTS)
    newSorted = sorted(toSort, key=lambda d: d["first"])

    # Eliminate incomplete chunks when we have larger ones
    # Need to go backwards, otherwise it's like sawing off the tree-branch you're sitting on ;-)
    for idx, aTsFile in reversed(list(enumerate(newSorted))):
        # Make sure to not delete the last remaining element if there's just one left
        # Because the check is idx against idx-1, it can compare one against the same
        if len(newSorted) == 1:
            break

        # First, look for if they have the same start frame
        if newSorted[idx]["first"] == newSorted[idx - 1]["first"]:
            # Pick the larger of the two frames
            if newSorted[idx]["last"] >= newSorted[idx - 1]["last"]:
                # Important! Delete first, then remove from list
                try:
                    if GLOBALS.onProd:
                        os.remove(
                            os.path.join(
                                config["workDirectory"], newSorted[idx - 1]["file"]
                            )
                        )
                except FileNotFoundError:
                    pass
                del newSorted[idx - 1]
            else:
                # Important! Delete first, then remove from list
                try:
                    if GLOBALS.onProd:
                        os.remove(
                            os.path.join(
                                config["workDirectory"], newSorted[idx]["file"]
                            )
                        )
                except FileNotFoundError:
                    pass
                del newSorted[idx]

    toReturn = [{"file": i["file"], "hash": i["hash"]} for i in newSorted]

    if toReturn != tsList:
        logger.info(f"Segments cleaned and ordered")
        logger.debug(f"was ({len(tsList)}) :{tsList}")
        logger.debug(f"is  ({len(toReturn)}) :{toReturn}")
        if len(toReturn) == 0:
            raise HPatrolError("Empty frames")
    else:
        logger.info("Segments obtained in proper sequence")

    return toReturn
