# External libraries import statements
import os
import time
import logging
from yt_dlp import YoutubeDL


# This application's import statements
try:
    # These are for when running in an EC2
    from exceptions import *
    import superGlblVars as GLOBALS
    from superGlblVars import config
    from orangeUtils import utils as ut
    from utils import hPatrolUtils as hput

except ModuleNotFoundError as err:
    # These are for when running in a Lambda
    print(f"Loading module for lambda execution: {__name__}")
    from src.python.exceptions import *
    from src.python.superGlblVars import config
    from src.python.orangeUtils import utils as ut
    from src.python import superGlblVars as GLOBALS
    from src.python.utils import hPatrolUtils as hput


logger = logging.getLogger()


def _sentToBucket(theBucket, lzS3Prefix, finalFileName, fileNamePath):
    logger.info(f"Pushing to S3 as '{finalFileName}'")

    try:
        if os.path.isfile(fileNamePath):
            result = GLOBALS.S3utils.pushToS3(fileNamePath,
                                                lzS3Prefix,
                                                theBucket,
                                                s3BaseFileName=finalFileName,
                                                deleteOrig=GLOBALS.onProd)
            if result:
                logger.info(f"Pushed video: {finalFileName}")
                return True
            else:
                logger.error(f"Video file {finalFileName} was not pushed to S3!")
        else:
            logger.warning(f"Unable to push {finalFileName}; file not found: {fileNamePath}")
    except:
        logger.warning(f"Unknown error trying to push {finalFileName}: {fileNamePath}")
    return False


def handleTube(prefixBase, ap):
    logger.info("Type selected: youtubeFile")

    try:
        devId = ap["deviceID"]
        videoUrl = ap["accessUrl"]
        filenameBase = ap["filenameBase"]
    except KeyError as err:
        logger.error("Parameter unspecified in input configuration")
        logger.error(f"Be sure to specify {err} in JSON file")
        raise HPatrolError("Parameter unspecified in input configuration")

    wrkBucketName = hput.pickBestBucket(ap, "wrkBucket")

    # Determine if proxy has been configured
    if config["proxy"]:
        theProxy = config["proxy"]
    else:
        theProxy = None

    # Create the yt_dlp options
    # quiet => will determine log level
    # proxy => HTTP/HTTPS/SOCKS proxy
    # check_formats  => Make sure formats are selected only from those that are actually downloadable
    # extractor_args => skip downloading unecessary and remove IOS from default player_client content see https://man.archlinux.org/man/yt-dlp.1#youtube
    ydlOpts = {
        'quiet': False,
        'proxy': theProxy,
        'overwrites': True,
        'check_formats': 'selected',
        'extractor_args': {'youtube': {'player_skip': ['webpage', 'configs', 'js'], 'player_client': ['android', 'web']}}
    }

    # Ensuring the stream is closed if any exceptions occur
    with YoutubeDL(ydlOpts) as ydl:
        try:
            # query for video metadata
            result = ydl.extract_info(videoUrl, download=False)
        except Exception as err:
            logger.error(f"Error calling metadata for provided video URL: {err}")
            raise HPatrolError("Error querying YouTube object metadata")

        # retrive all available formats
        formats = result["formats"]

        # create audio and video dict to capture the different formats
        audio = []
        video = []

        # filter the different formats in their perspective variables
        for format in formats:
            if format["resolution"] == "audio only":
                audio.insert(0,format)
            else:
                video.insert(0,format)

        # use yt_dlp function to build a table of all available video formats and log results
        logging.info("Logging video formats \n{0}\n".format(ydl.render_formats_table({"formats":video})))

        # use yt_dlp function to build a table of all available audio formats and log results
        logging.info("Logging audio formats \n{0}\n".format(ydl.render_formats_table({"formats":audio})))

        # log video title
        logger.info(f'Video title: "{result["title"]}"')

        # select best video format
        theStream = video[0]
        # use yt_dlp function to build a table of selected stream format and log results
        logging.info("Stream selected:  \n{0}\n".format(ydl.render_formats_table({"formats":[theStream]})))
        # select stream format webm/mp4/mhtml ...
        ext = theStream["ext"]

        # build the name of the output filename 
        filenameBase = f"{hput.formatNameBase(filenameBase, devId)}.{ext}"
        try:
            ourFilename = hput.formatNameSuffix(filenameBase, ap["finalFileSuffix"], int(time.time()))
        except KeyError:
            ourFilename = hput.formatNameSuffix(filenameBase, "", int(time.time()))

        if GLOBALS.useTestData:
            fileWithPath = "testResources/testVideo.ts"
            logger.debug(f"Using test file '{fileWithPath}'")
        else:
            # outtmpl => used to indicate a template for the output file name
            if "workDirectory" in config:
                ydlOpts["outtmpl"] = os.path.join(config["workDirectory"],ourFilename)
            else:
                ydlOpts["outtmpl"] = ourFilename    

            # provide the format id desired for download
            ydlOpts["format"]  = theStream["format_id"]
            # download video with options
            with YoutubeDL(ydlOpts) as ydl:
                ydl.download(videoUrl)
        if _sentToBucket(wrkBucketName, prefixBase, ourFilename, ydlOpts["outtmpl"]["default"]):
            GLOBALS.sqsUtils.sendMessage(config["statusQueue"], {"aimpoint": ap, "isCollecting": True})
        else:
            GLOBALS.sqsUtils.sendMessage(config["statusQueue"], {"aimpoint": ap, "isCollecting": False})
