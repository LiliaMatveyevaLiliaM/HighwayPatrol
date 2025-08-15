# External libraries import statements
import os
import json
import time
import base64
import logging
import datetime as dt
from shutil import copyfile


# This application's import statements
try:
    # These are for when running in an EC2
    from exceptions import *
    import superGlblVars as GLOBALS
    from superGlblVars import config
    from orangeUtils import utils as ut
    from addons import iodineAddon as io
    from utils import hPatrolUtils as hput
    from collectionTypes import CollectionType

except ModuleNotFoundError as err:
    # These are for when running in a Lambda
    print(f"Loading module for lambda execution: {__name__}")
    from src.python.exceptions import *
    from src.python.superGlblVars import config
    from src.python.orangeUtils import utils as ut
    from src.python import superGlblVars as GLOBALS
    from src.python.addons import iodineAddon as io
    from src.python.utils import hPatrolUtils as hput
    from src.python.collectionTypes import CollectionType


logger = logging.getLogger()


def handleStills(collType, prefixBase, ap, lambdaContext=None):
    try:
        if collType == CollectionType.STILLS:
            logger.info("Type selected: stills")
            _collectStill(prefixBase, ap, lambdaContext)

        elif collType == CollectionType.FSTLLS:
            logger.info("Type selected: fStills")
            idList  = ap["deviceIdList"]
            urlList = ap["accessUrlList"]
            fnbList = ap["filenameBaseList"]
            for id, url, fNamBas in zip(idList, urlList, fnbList):
                ap["deviceID"] = id
                ap["accessUrl"] = url
                ap["filenameBase"] = fNamBas
                fnBase = hput.formatNameBase(fNamBas, id)

                # Reconstitute the prefixBase w/new fNamBas
                # Do it like this here cause don't have date info here
                prefixParts = prefixBase.split("/")
                prefixBase = "/".join(prefixParts[:-1]) + "/" + fnBase
                _collectStill(prefixBase, ap, lambdaContext)

        elif collType == CollectionType.ISTLLS:
            logger.info("Type selected: iStills")
            fNamBas = ap["filenameBase"]
            urlList = io.getUpdatedImgsUrls(ap)
            fnBase = hput.formatNameBase(fNamBas, ap["deviceID"])
            # FIXME: Technical debt: Handle for-loops in singleCollector mode
            #        This for-loop occurs outside the while-loop in _collectStill()
            #        so singleCollector mode here may not really ever work.
            #        Essentially, getUpdatedImgsUrls() should be part of the while loop.
            for anUpdate in urlList:
                ap["accessUrl"] = anUpdate["url"]

                # Adding lastUpdate for hash and filename
                ap["lastUpdate"] = anUpdate["lastUpdate"]

                _collectStill(prefixBase, ap, lambdaContext, collType)

        elif collType == CollectionType.IMAGEINJSON:
            logger.info("Type selected: imageInJson")
            _collectStill(prefixBase, ap, lambdaContext, collType)

    except KeyError as err:
        logger.error("Parameter unspecified in input configuration")
        logger.error(f"Be sure to specify {err} in JSON file")
        raise HPatrolError("Parameter unspecified in input configuration")
    except ConnectionError as err:
        raise HPatrolError(f"Connection error getting image URL: {err}")


def _collectStill(prefixBase, ap, lambdaContext=None, collType=CollectionType.STILLS):
    try:
        devId = ap["deviceID"]
        imageUrl = ap["accessUrl"]
        filenameBase = ap["filenameBase"]
    except KeyError as err:
        logger.error("Parameter unspecified in input configuration")
        logger.error(f"Be sure to specify {err} in JSON file")
        raise HPatrolError('Parameter unspecified in input configuration')

    ourFilename = f"{hput.formatNameBase(filenameBase, devId)}.JPG"

    try:
        decoy = True == ap["decoy"]
    except KeyError:
        decoy = False

    try:
        singleCollector = True == ap["singleCollector"]
    except KeyError:
        singleCollector = False

    breakPoint, theSleep, sleepyFraction = hput.calculateExecutionStop(ap, lambdaContext)

    while True:
        if GLOBALS.useTestData:
            testFile = "testResources/kameraSample.jpg"
            camFile = f"{config['workDirectory']}/{ourFilename}"
            copyfile(testFile, camFile)
            hashFile = f"{ourFilename}.md5"
            # Hash file is NOT created because it'd have to be deleted every time to test
            logger.debug("Using TEST data; hash file is NOT created")

        else:
            if collType == CollectionType.ISTLLS:
                # ISTILLS uses its own filename processing
                fnBase = hput.formatNameBase(filenameBase, devId)

                # Derive image filename
                base = os.path.basename(imageUrl)                           # "PreX00002.jpg"
                presetText = os.path.splitext(base)                         # ("PreX00002", ".jpg")
                presetBase = presetText[0].replace(presetText[0][4:], "")   # "PreX"

                lu = dt.datetime.strptime(ap["lastUpdate"], "%Y-%m-%dT%H:%M:%S.%fZ")
                lastUpdate = lu.strftime("%Y%m%d%H%M%S")

                # e.g.: ourFilename = <devID>-PreX_YYYYMMDDHHMMSS.jpg
                ourFilename = f"{fnBase}-{presetBase}_{lastUpdate}{presetText[1]}"

            if collType == CollectionType.IMAGEINJSON:
                try:
                    # If we don't specify ?format=xxx in the URL, the server gives code and instructions
                    r = GLOBALS.netUtils.get(imageUrl)
                    jsonContent = json.loads(r.text)

                    # Uncomment to test this collectionType
                    # testFile = "testResources/imageInJson.json"
                    # logger.debug(f"Reading from test file '{testFile}'")
                    # with open(testFile, "r") as f:
                    #     jsonContent = json.loads(f.read())
                except Exception as e:
                    # We want the process to continue regardless of any errors collecting
                    logger.error(e)
                    logger.warning(f"Unable to grab camera from {imageUrl}")
                    # We may be getting denied, network down, or something else...wait the actual pollFrequency
                    if hput.itsTimeToBail(lambdaContext, breakPoint, ap["pollFrequency"]*1000):
                        # If we get here we're encountering an error and will not collect
                        GLOBALS.sqsUtils.sendMessage(config["statusQueue"], {"aimpoint": ap, "isCollecting": False})
                        break
                    logger.info(f"Sleeping the specified poll frequency: {ap['pollFrequency']}s")
                    time.sleep(ap["pollFrequency"])
                    continue

                fullFilePath = os.path.join(config["workDirectory"], ourFilename)
                # FIXME: Make an aimpoint parameter to specify the key from where to get the image 
                #        This piece and collectionType was hastily put together for a time-sensitive task
                #        Ideally we should have a configurable key like the {firstContactData:{key:""}} specification
                #        i.e. here jsonContent["contentBase64"] is hardcoded to the particular target we have right now
                with open(fullFilePath, "wb") as fh:
                    # Extract the image from the JSON
                    fh.write(base64.decodebytes(bytes(jsonContent["contentBase64"], "utf-8")))

            # For all other types *except* IMAGEINJSON
            else:
                try:
                    GLOBALS.netUtils.downloadImage(ourFilename, imageUrl)

                except Exception as e:
                    # We want the process to continue regardless of any errors collecting
                    logger.error(e)
                    logger.warning(f"Unable to grab camera from {imageUrl}")
                    # We may be getting denied, network down, or something else...wait the actual pollFrequency
                    if hput.itsTimeToBail(lambdaContext, breakPoint, ap["pollFrequency"]*1000):
                        # If we get here we're encountering an error and will not collect
                        GLOBALS.sqsUtils.sendMessage(config["statusQueue"], {"aimpoint": ap, "isCollecting": False})
                        break
                    logger.info(f"Sleeping the specified poll frequency: {ap['pollFrequency']}s")
                    time.sleep(ap["pollFrequency"])
                    continue

        # Gave up on querying target's lastModDate; will always use our own timestamp
        lastModDate = int(time.time())

        # Add suffix epoch to the filename
        theSplit = os.path.splitext(ourFilename)
        finalFilename = f"{theSplit[0]}_{lastModDate}{theSplit[1]}"
        os.rename(
            os.path.join(config["workDirectory"], ourFilename),
            os.path.join(config["workDirectory"], finalFilename)
        )

        wrkBucketName = hput.pickBestBucket(ap, "wrkBucket")

        if collType == CollectionType.ISTLLS:
            # For ISTILLS we already confirmed these are new images
            if _saveWasSuccessful(decoy, wrkBucketName, prefixBase, finalFilename, ap):
                _pushHashInTheName(io.hashForTracking(ap["deviceID"], ap["lastUpdate"]))

        else:
            # Create hash id file
            fullFilePath = os.path.join(config["workDirectory"], finalFilename)
            hashFilePath = ut.makeHashFileFromData(
                open(fullFilePath, "rb").read(),
                config["workDirectory"],
                ourFilename)
            hashFile = os.path.basename(hashFilePath)

            if not _isSameImage(wrkBucketName, ourFilename):
                if _saveWasSuccessful(decoy, wrkBucketName, prefixBase, finalFilename, ap):
                    # Note that this dedup check uses the device name w/out the epoch (ourFilename)
                    _pushHashInContent(hashFile)

        # We're only intended to run once
        if not singleCollector:
            logger.info("Not a singleCollector request; breaking out")
            break

        if hput.itsTimeToBail(lambdaContext, breakPoint, theSleep):
            break
        # Don't sleep if we're just using the test data; don't waste time
        if not GLOBALS.useTestData:
            logger.info(f"Sleeping {sleepyFraction*100:g}% of the poll frequency: {theSleep/1000:.2f}s")
            time.sleep(theSleep/1000)

    # While-loop ends here


def _isSameImage(bucketName, fileName):
    logger.info(f"Checking for a change in image for '{fileName}'")
    hashFileName = f"{fileName}.md5"
    # Read from S3 the old hash id file
    # Note: On this dup-check technique the hash is in the contents of the file
    # on another dup-check, the filename is the hash.
    oldHash = GLOBALS.S3utils.readFileContent(bucketName, f"{GLOBALS.s3Hashfiles}/{hashFileName}")
    # logger.debug(f"oldHash ->{oldHash}<-")
    if not oldHash:
        logger.info("There may not be an MD5 file yet")
        return False

    # Open recent hash id file; open as text
    # Hash file (.md5) was created by the download function
    try:
        hashFile = os.path.join(config["workDirectory"], hashFileName)
        logger.debug(f"Reading hash file '{hashFile}'")
        with open(hashFile, "r") as f:
            newHash = f.read()
    except FileNotFoundError:
        logger.info("No local MD5 file found")
        return False
    # logger.debug(f"newHash ->{newHash}<-")

    if oldHash == newHash:
        logger.debug("Image unchanged (same hash)")
        return True

    return False


def _pushHashInTheName(theHash):
    if not GLOBALS.S3utils.createEmptyKey(
        config["defaultWrkBucket"],
        f"{GLOBALS.s3Hashfiles}/{theHash}"
    ):
        logger.warning("Could not create hash file; ignoring its creation")


def _pushHashInContent(hashFileName):
    hashFilePath = os.path.join(config["workDirectory"], hashFileName)

    try:
        if os.path.isfile(hashFilePath):
            result = GLOBALS.S3utils.pushToS3(hashFilePath,
                                                GLOBALS.s3Hashfiles,
                                                config["defaultWrkBucket"],
                                                deleteOrig=GLOBALS.onProd)
            if result:
                logger.info(f"Pushed hash file: {hashFileName}")
            else:
                logger.error(f"Hash file {hashFileName} was not pushed to S3!")
        else:
            logger.warning(f"Unable to push {hashFileName}; file not found: {hashFilePath}")
    except:
        logger.warning(f"Unknown error trying to push {hashFileName}: {hashFilePath}")


def _saveWasSuccessful(decoy, theBucket, lzS3Prefix, finalFileName, ap):
    if decoy:
        GLOBALS.sqsUtils.sendMessage(config["statusQueue"], {"aimpoint": ap, "isCollecting": True})
        logger.info("Decoy aimpoint; NOT pushing to S3")
        return True

    logger.info(f"Pushing image to S3 as '{finalFileName}'")
    fileNamePath = os.path.join(config["workDirectory"], finalFileName)
    try:
        if os.path.isfile(fileNamePath):
            result = GLOBALS.S3utils.pushToS3(fileNamePath,
                                                lzS3Prefix,
                                                theBucket,
                                                s3BaseFileName=finalFileName,
                                                deleteOrig=GLOBALS.onProd,
                                                extras={"ContentType": "image/jpeg"})
            if result:
                # Push successful
                GLOBALS.sqsUtils.sendMessage(config["statusQueue"], {"aimpoint": ap, "isCollecting": True})
                return True
            else:
                logger.error(f"Image file {finalFileName} was not pushed to S3!")
        else:
            logger.warning(f"Unable to push {finalFileName}; file not found: {fileNamePath}")
    except:
        logger.warning(f"Unknown error trying to push {finalFileName}: {fileNamePath}")
    GLOBALS.sqsUtils.sendMessage(config["statusQueue"], {"aimpoint": ap, "isCollecting": False})
    return False
