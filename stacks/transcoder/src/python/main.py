"""
Module to transcode or create a timelapse video out of stills.
This will act on the specific videos (or stills) time-range requested.
If a time buffer is desired on the core video, it needs to be specified by increasing the time-range requested.

Can be run as a stand-alone python script to test.
"""

# External libraries import statements
import os
import time
import json
import uuid
import logging
import argparse
import threading
import subprocess
import datetime as dt
from subprocess import CalledProcessError


# This application's import statements
try:
    # These are for when running in an EC2
    import processInit
    import systemSettings
    from exceptions import *
    import superGlblVars as GLOBALS
    from superGlblVars import config
    from orangeUtils import auditUtils
    from utils import hPatrolUtils as hput
    from ec2_metadata import ec2_metadata as ec2
    from orangeUtils.auditUtils import AuditLogLevel

except ModuleNotFoundError as err:
    # These are for when running in a Lambda
    print(f"Loading module for lambda execution: {__name__}")
    from src.python.exceptions import *
    from src.python import processInit
    from src.python import systemSettings
    from src.python.superGlblVars import config
    from src.python.orangeUtils import auditUtils
    from src.python import superGlblVars as GLOBALS
    from src.python.utils import hPatrolUtils as hput
    from src.python.orangeUtils.auditUtils import AuditLogLevel


logger = logging.getLogger()


def _sendToBucket(dstBucket, dstPrefix, filename):
    logger.info("Sending file to S3")

    fileNamePath = os.path.join(config['workDirectory'], filename)
    try:
        if os.path.isfile(fileNamePath):
            result = GLOBALS.S3utils.pushToS3(fileNamePath,
                                                dstPrefix,
                                                dstBucket,
                                                s3BaseFileName=filename,
                                                deleteOrig=GLOBALS.onProd)
            if result:
                logger.info(f"Pushed file {filename} as {dstPrefix}/{filename}")
            else:
                logger.error(f"File {filename} was not pushed to S3!")
                raise HPatrolError(f"File {filename} was not pushed to S3!")
        else:
            logger.warning(f"Unable to push {filename}; file not found: {fileNamePath}")
            raise HPatrolError(f"Unable to push {filename}; file not found: {fileNamePath}")
    except Exception as err:
        logger.warning(f"Error trying to push {filename}: {fileNamePath} ::{err}")
        raise HPatrolError(f"Error trying to push {filename}: {fileNamePath} ::{err}")


def _getRangeOfFiles(bucket, prefix, clipStart):
    # Obtain a large set of files bounded by the epoch times of clipStart. The files
    # are later downselected to the interested ones, but for now, just get the bunch.

    # Notice we cut off the last 4 digits of epoch to do our search. Then
    # increase the least significant digit by one to grab the next chunk.
    # This gives us a block of at most 5.5 hours (10000secs = 2hrs 46mins) (twice)
    # Example, if epoch is 1663017388
    # the search starts at 166301
    # then will repeat at  166302
    epochFirstSearch = int(clipStart[:-4])
    epochSecndSearch = epochFirstSearch + 1

    logger.info(f"Searching file chunks for '{epochFirstSearch}*'")
    searchFor = f'{prefix}{epochFirstSearch}*'
    # logger.debug(f"searchFor: '{searchFor}'")
    try:
        firstList = GLOBALS.S3utils.getWildcardKey(searchFor, bucket, unique=True)
        firstList.sort(key=hput.naturalKeys)
    except Exception:
        # Ignore; there may not be any files
        logger.warning("No files found...strange")
        firstList = []
    # logger.debug(f"firstList:\n {firstList}\n")

    logger.info(f"Searching file chunks for '{epochSecndSearch}*'")
    searchFor = f'{prefix}{epochSecndSearch}*'
    # logger.debug(f"searchFor: '{searchFor}'")
    try:
        secndList = GLOBALS.S3utils.getWildcardKey(searchFor, bucket, unique=True)
        secndList.sort(key=hput.naturalKeys)
    except Exception:
        # Ignore; there may not be any files
        logger.warning("No files found...strange")
        secndList = []
    # logger.debug(f"secndList:\n {secndList}\n")

    firstList.extend(secndList)
    if firstList == []:
        raise HPatrolError("No files to process found")

    return firstList


def _focusFileList(sortedFiles, clipStart, clipEnd, ext):
    # Note that function assumes it receives a sorted list
    logger.info("Reducing file list to within the requested timeframe")

    clipStart = int(clipStart)
    clipEnd = int(clipEnd)

    cleanedList = []
    for idx, aFile in enumerate(sortedFiles):
        # Using float because some filenames have additional indices (i.e.: _<epoch>.idx.ts)
        fileEpoch = float(aFile.replace(ext, '').split('_')[-1])
        if fileEpoch < clipStart or fileEpoch > clipEnd:
            continue
        # print(f'{idx}: {aFile} {fileEpoch}')
        cleanedList.append(aFile)

    if cleanedList == []:
        raise HPatrolError("No files to process")

    # Checks if the interval requested is longer than the files returned by _getRangeOfFiles
    if clipEnd > float(sortedFiles[-1].replace(ext, '').split('_')[-1]):
        logger.warning("Requested clip end time is beyond the last identified file in the list - list may be incomplete")

    # print(f'clipStart: {clipStart}  clipEnd: {clipEnd}')
    # print(f'cleanedList ({len(cleanedList)})\n{cleanedList}')
    logger.info(f"Total files left to use: {len(cleanedList)}")
    return cleanedList


def _goodTranscode(downloadedList, mp4Filename, transcodeOptions):
    # Compose the fileList as input to ffmpeg with a random filename
    aTempFile = os.path.join(config['workDirectory'], str(uuid.uuid4()) + ".txt")
    # logger.debug(f"aTempFile: {aTempFile}")

    # Triple-confirm correct order
    downloadedOrig = downloadedList.copy()
    downloadedList.sort(key=hput.naturalKeys)
    if downloadedOrig != downloadedList:
        logger.info(f"NOTE: video segments list was incorrectly sorted; corrected")
        logger.debug(f"was:{downloadedOrig}")
        logger.debug(f" is:{downloadedList}")

    # Create the text input file to ffmpeg; specifies the files to concatenate
    with open(aTempFile, mode='wt', encoding='utf-8') as f:
        for i in downloadedList:
            f.write(f"file \'{i}\'\n")

    outFile = os.path.join(config['workDirectory'], mp4Filename)
    try:
        success = True
        logger.info("Transcoding video file")
        # Valid loglevels are: "quiet", "panic", "fatal", "error", "warning", "info", "verbose", "debug", "trace"
        builder = hput.FFMPEGBuilder(aTempFile, outFile, transcodeOptions)
        builder.ffmpeg = config["ffmpeg"]
        # Note we need "-safe 0" for some of our target filenames; we know our names don't specify any protocols
        # This fixes an "Unsafe file name" issue from ffmpeg where it has trust issues with some files
        builder.input(
            {
                "-hide_banner": "",
                "-safe": "0",
                "-f": "concat"
             })
        builder.output(
            {
                "-acodec": "copy",
                "-vcodec": "copy",
                "-v": "error" 
             })

        ffmpegCommand = builder.renderCommand()
        logger.debug(f"Invoking FFMPEG Command: {' '.join(str(x) for x in ffmpegCommand)}")
        try:
            subprocess.run(ffmpegCommand, check=True)
        except CalledProcessError as cmdError:
            logger.error("Error with ffmpeg execution")
            logger.error(cmdError)
            success = False

		# -hide_banner  # All FFmpeg tools normally show a copyright notice, build options and library versions; suppress printing this
		# -acodec 'copy'  # Set the audio codec or use special value copy (output only) to use the same stream that's already in there
		# -vcodec 'copy'  # Set the video codec or use special value copy (output only) to use the same stream that's already in there

        # commented out 09/13/22: this call would reduce the bitrate
        # subprocess.run(f"{config['ffmpeg']} -f concat -i {aTempFile} -c:v libx264 -c:a aac -b:v 97k {outFile} -v error".split())
        # Clips are bitrate reduced solely for ease of upload
        # Output is at 97kbps bitrate; original videos may be higher bitrate
    except Exception as err:
        logger.exception(f"Exception caught:::{err}")
        success = False

    # Delete temporary file
    os.remove(os.path.join(config['workDirectory'], aTempFile))

    return success


def _goodSplit(downloadedList, mp4Filename):
    # Compose the fileList as input to ffmpeg with a random filename
    aTempFile = os.path.join(config['workDirectory'], str(uuid.uuid4()) + ".txt")
    # logger.debug(f"aTempFile: {aTempFile}")

    # Triple-confirm correct order
    downloadedOrig = downloadedList.copy()
    downloadedList.sort(key=hput.naturalKeys)
    if downloadedOrig != downloadedList:
        logger.info(f"NOTE: video segments list was incorrectly sorted; corrected")
        logger.debug(f"was:{downloadedOrig}")
        logger.debug(f" is:{downloadedList}")

    # Create the text input file to ffmpeg; specifies the files to concatenate
    # Simple text file where each line is simply "file '<inputFilename>'"
    with open(aTempFile, mode='wt', encoding='utf-8') as f:
        for i in downloadedList:
            f.write(f"file \'{i}\'\n")

    outFile = os.path.join(config['workDirectory'], mp4Filename)
    try:
        success = True
        logger.info("Extracting audio")
        # Valid loglevels are: "quiet", "panic", "fatal", "error", "warning", "info", "verbose", "debug", "trace"
        commandString = f"{config['ffmpeg']} -hide_banner -f concat -i {aTempFile} -vn -acodec copy {outFile} -v error"
        # logger.debug(f"commandString: {commandString}")
        subprocess.run(commandString.split())
		# -hide_banner    # All FFmpeg tools normally show a copyright notice, build options and library versions; suppress printing this
		# -vn             # No video
		# -acodec 'copy'  # Set the audio codec or use special value copy (output only) to use the same stream that's already in there

    except Exception as err:
        logger.exception(f"Exception caught:::{err}")
        success = False

    # Delete temporary file
    os.remove(os.path.join(config['workDirectory'], aTempFile))

    return success


def _getFiles(fileList, srcBucket):
    downloadedList = []
    try:
        for fileToGet in fileList:
            logger.debug(fileToGet)
            fileName = fileToGet.split(os.path.sep)[-1]
            # logger.debug(f"would be dowloading:{fileToGet}")
            GLOBALS.S3utils.getFileFromS3(srcBucket, fileToGet, os.path.join(config['workDirectory'], fileName))
            downloadedList.append(fileName)
    except Exception as err:
        logger.exception(err)
        raise HPatrolError("Error downloading")
    # logger.debug(f"downloadedList:{downloadedList}")

    return downloadedList


# Allow for advanced ffmpeg processing features
def _getVideoFiles(fileList, taskConfig):
    srcBucket = taskConfig["wrkBucket"]
    downloadedList = _getFiles(fileList, srcBucket)

    try:
        ffmpegDedup = taskConfig["ffmpegDedup"]
    except:
        ffmpegDedup = GLOBALS.ffmpegDedup

    # Determine if further deduping is required
    if ffmpegDedup:
        dedupedList = []
        uniqueFFMPEGHash = []

        # Dedup by ffmpeg hash
        if ffmpegDedup == "ffmpegHash":
            logging.info("Executing additional deduplication using hash")
            for fileName in downloadedList:
                try:
                    command = hput.FFMPEGBuilder(os.path.join(config["workDirectory"], fileName), "-")
                    command.input({"-hide_banner": ""})
                    command.output({
                        "-map": "0:v",
                        "-f": "md5"})
                    # The above is equivalent to:
                    # command = f"ffmpeg -hide_banner -i {os.path.join(config['workDirectory'], fileName)} -map 0:v -f md5 - ".split()
                    ffResult = subprocess.run(command.renderCommand(), capture_output=True, text=True)
                    if ffResult.stdout:
                        hash = ffResult.stdout.replace("MD5=", "").strip()
                        if hash in uniqueFFMPEGHash:
                            logging.info(f"The hash {hash} is already in the list; will skip {fileName}")
                            os.remove(f"{os.path.join(config['workDirectory'], fileName)}")
                        else:
                            uniqueFFMPEGHash.append(hash)
                            dedupedList.append(fileName)
                except Exception as err:
                    logging.exception(f"Could not hash file:::{err}")
                    if fileName not in downloadedList:
                        dedupedList.append(fileName)
            return dedupedList

        # Dedup by ffmpeg segments
        elif ffmpegDedup == "ffmpegFrameHash":
            # TODO implement deduplication by segments of a video
            logging.warning("Deduplication by segment has not been implemented")
            return downloadedList
        else:
            return downloadedList

    else:
        # No deduplication requested
        pass

    return downloadedList


def _resetStartTime(downloadedList):
    logger.info("Preprocessing video files")
    for file in downloadedList:
        inputFile = os.path.join(config["workDirectory"], file)
        outFile = os.path.join(config["workDirectory"], f"{str(uuid.uuid4())}{file}")
        ffmpegBuilder = hput.FFMPEGBuilder(inputFile, outFile)
        ffmpegBuilder.input({"-hide_banner": "", "-ss": "00:00:00"})
        ffmpegBuilder.output({"-c": "copy", "-v": "error"})
        command = ffmpegBuilder.renderCommand()
        logger.debug(f"Invoking FFMPEG Command: {' '.join(str(x) for x in command)}")
        try:
            subprocess.run(command)
        except Exception as err:
            logger.warning(f"Exception caught: Preprocessing video; will continue:::{err}")
            continue
        os.replace(outFile, inputFile)
    return downloadedList


def _determineGroups(downloadedList):
    # This determines the particular group of files that will be used to create the MP4s
    # Sometimes the files are large enough that they are not to be combined with others
    aGroup = []
    allGroups = []
    for aFile in downloadedList:
        if _isLongSegment(aFile):
            if aGroup != []:
                # Close out the on-going group
                allGroups.append(aGroup)
                aGroup = []
            # Create a group of this file by itself
            allGroups.append([aFile])
            continue
        else:
            aGroup.append(aFile)
    if aGroup != []:
        allGroups.append(aGroup)

    return allGroups


def _isLongSegment(aFile):
    # Return True if the segment received is larger than 80% of the system's periodicity
    # Using 80% in case there are collection issues and couldn't get 100%, but it's still large enough
    # Encountered a situation where 'duration' was coming back as epoch, so we're limiting
    # our thresholds here to upper and lower bounds in order to ignore those files because they would
    # appear as being larger than the system's periodicity. Those we treat as smaller files as it is
    # considered an obvious error since no file could be larger than the system's periodicity.
    gottenThreshold = 0.80
    errorsThreshold = 1.50
    localFilePath = f"{config['workDirectory']}/{aFile}"

    # Get segment's metadata
    commandString = f"{config['ffprobe']} -hide_banner -v error -print_format json -select_streams v -show_entries stream=duration {localFilePath}"
    # logger.debug(f"commandString: {commandString}")
    ffprobeResult = subprocess.run(commandString.split(), capture_output=True, text=True)
    if ffprobeResult.returncode != 0:
        logger.error(f"Frame error {ffprobeResult.stderr} (ffprobeResult.returnCode={ffprobeResult.returncode})")
        # Returning True so it's processed by itself
        return True
    videoInfo = json.loads(ffprobeResult.stdout)
    # logger.debug(videoInfo) # Print ffprobe's raw JSON result

    try:
        segmentLen = float(videoInfo["streams"][0]["duration"])
    except KeyError:
        logger.info("Ffprobe couldn't get 'duration' in segment; will attempt to calculate")
        commandString = f"{config['ffprobe']} -hide_banner -v error -print_format json -select_streams v -count_packets -show_entries stream=nb_read_packets,r_frame_rate {localFilePath}"
        # logger.debug(f"commandString: {commandString}")
        ffprobeResult = subprocess.run(commandString.split(), capture_output=True, text=True)
        if ffprobeResult.returncode != 0:
            logger.error(f"Frame error {ffprobeResult.stderr} (ffprobeResult.returnCode={ffprobeResult.returncode})")
            # Returning True so it's processed by itself
            return True
        videoInfo = json.loads(ffprobeResult.stdout)
        # logger.debug(videoInfo) # Print ffprobe's raw JSON result

        try:
            frameRate = int(videoInfo["streams"][0]["r_frame_rate"].split("/")[0])
            readPackets = int(videoInfo["streams"][0]["nb_read_packets"])
        except KeyError as err:
            logger.error(f"Unable to get {err} in segment; notify developer")
            # Highlighting this so we develop more handling options here if this were to occur
            # For now will treat as if it's a large segment so it's processed by itself
            return True

        # The number of packets (frames) divided by the frameRate gives us the duration
        segmentLen =  readPackets / frameRate
        # logger.debug(f"frameRate:{frameRate} readPackets:{readPackets}")
        # logger.debug(f"segmentLen:{segmentLen}")

    # Make sure to convert systemPeriodicity to seconds...doh!
    lowerLimit = config['systemPeriodicity'] * gottenThreshold * 60
    upperLimit = config['systemPeriodicity'] * errorsThreshold * 60
    if segmentLen > lowerLimit and segmentLen < upperLimit:
        return True
    return False


def execute(taskConfig):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Transcoder"
    logger.info(f"Received task: {json.dumps(taskConfig)}")

    allFiles = _getRangeOfFiles(
        taskConfig["wrkBucket"],
        f'{taskConfig["srcPrefix"]}/{taskConfig["filenameBase"]}_',
        taskConfig["clipStart"]
        )

    clipEnd = int(taskConfig["clipStart"]) + int(taskConfig["clipLengthSecs"])
    if taskConfig["task"] == "transcode":
        filesToWorkOn = _focusFileList(
            allFiles,
            taskConfig["clipStart"],
            clipEnd,
            '.ts'
            )
        _doTranscoding(taskConfig, filesToWorkOn)

    elif taskConfig["task"] == "timelapse":
        filesToWorkOn = _focusFileList(
            allFiles,
            taskConfig["clipStart"],
            clipEnd,
            '.JPG'
            )
        _doTimelapse(taskConfig, filesToWorkOn)

    elif taskConfig["task"] == "takeaudio":
        filesToWorkOn = _focusFileList(
            allFiles,
            taskConfig["clipStart"],
            clipEnd,
            '.ts'
            )
        _doSplitAudio(taskConfig, filesToWorkOn)

    else:
        raise HPatrolError("No task specified")


def _doTranscoding(taskConfig, filesToWorkOn):
    logger.info("Downloading video segments")
    downloadedList = _getVideoFiles(filesToWorkOn, taskConfig)
    downloadedList = _resetStartTime(downloadedList)
    segmentGroups = _determineGroups(downloadedList)
    try:
        transcodeOptions = taskConfig["transcodeOptions"]
    except:
        transcodeOptions = {}

    ext = os.path.splitext(taskConfig["outFilename"])[1]
    name = os.path.splitext(taskConfig["outFilename"])[0]

    # Need random filename because sometimes an input file is same name as output
    tempFileName = f"{uuid.uuid4()}{ext}"

    mp4List = []
    if len(segmentGroups) == 1:
        # Effectively only one segment group; don't add group suffix
        aGroup = segmentGroups[0]
        outFilename = f"{name}{ext}"
        logger.info(f"File '{outFilename}' composed of: {aGroup}")
        if _goodTranscode(aGroup, tempFileName, transcodeOptions):
            mp4List.append({"tFile": tempFileName, "oFile": outFilename})
    else:
        for idx, aGroup in enumerate(segmentGroups):
            outFilename = f"{name}_{idx:02d}{ext}"
            tmpFilename = f"{tempFileName}_{idx:02d}{ext}"
            logger.info(f"File '{outFilename}' composed of: {aGroup}")
            if _goodTranscode(aGroup, tmpFilename, transcodeOptions):
                mp4List.append({"tFile": tmpFilename, "oFile": outFilename})

    # Cleanup files from the working area; important for when in lambda execution
    logger.info("Deleting working files")
    for f in downloadedList:
        os.remove(os.path.join(config['workDirectory'], f))

    for aFile in mp4List:
        # Rename needs to be after the deletion of files above
        os.rename(
            os.path.join(config["workDirectory"], aFile["tFile"]),
            os.path.join(config["workDirectory"], aFile["oFile"])
        )
        _sendToBucket(taskConfig["dstBucket"],
                      taskConfig["dstPrefix"],
                      aFile["oFile"])


def _doTimelapse(taskConfig, filesToWorkOn):
    logger.info("Downloading still images")

    framerate = taskConfig["timelapseFPS"]
    try:
        transcodeOptions = taskConfig["transcodeOptions"]
    except:
        transcodeOptions = {}
    filePattern = f"{config['workDirectory']}/{taskConfig['filenameBase']}_*.JPG"

    downloadedList = _getFiles(filesToWorkOn, taskConfig["wrkBucket"])

    outFile = os.path.join(config['workDirectory'], taskConfig["outFilename"])

    try:
        logger.info("Creating timelapse video")
        # Valid loglevels are: "quiet", "panic", "fatal", "error", "warning", "info", "verbose", "debug", "trace"
        builder = hput.FFMPEGBuilder(filePattern, outFile, transcodeOptions)
        builder.ffmpeg = config['ffmpeg']
        builder.input(
            {
                "-hide_banner":"", 
                "-y":"",
                "-framerate": framerate,
                "-pattern_type": "glob" 
             })                
        builder.output(
            {
                "-vcodec":"libx264", 
                "-crf":"0",
                "-v": "error" 
             })
        ffmpegCommand = builder.renderCommand()
        # The built command above is equivalent to:
        # ffResult = subprocess.run(f"{config['ffmpeg']} -hide_banner -y -framerate {framerate} -pattern_type glob -i {filePattern} -vcodec libx264 -crf 0 {outFile} -v error".split())
		# -hide_banner  # All FFmpeg tools normally show a copyright notice, build options and library versions; suppress printing this
        # -crf		    # sets the quality of the output video (0-51); around 18 is perceptually lossless; 50 is extremely compressed
        # -y		    # overwrite file if it exists
        # -framerate	# frames per second
        logger.debug(f"Invoking FFMPEG Command: {' '.join(str(x) for x in ffmpegCommand)}")
        ffResult = subprocess.run(ffmpegCommand)

    except Exception as err:
        logger.exception(f"Exception caught:::{err}")

    if ffResult.returncode != 0:
        logger.error(f"FFMPEG (stderr={ffResult.stderr}) (returnCode={ffResult.returncode})")
        raise HPatrolError("FFmpeg error")

    # Cleanup files from the working area; important for when in lambda execution
    logger.info("Deleting working files")
    for f in downloadedList:
        os.remove(os.path.join(config['workDirectory'], f))

    _sendToBucket(taskConfig["dstBucket"], taskConfig["dstPrefix"], taskConfig["outFilename"])


def _doSplitAudio(taskConfig, filesToWorkOn):
    logger.info("Downloading video segments")
    downloadedList = _getFiles(filesToWorkOn, taskConfig["wrkBucket"])
    segmentGroups = _determineGroups(downloadedList)

    ext = os.path.splitext(taskConfig["outFilename"])[1]
    name = os.path.splitext(taskConfig["outFilename"])[0]
    ext = _determineExtension(downloadedList)   # audio-specific extension

    mp4List = []
    if len(segmentGroups) == 1:
        # Effectively only one segment group; don't add group suffix
        aGroup = segmentGroups[0]
        outFilename = f"{name}{ext}"
        logger.info(f"File '{outFilename}' composed of: {aGroup}")
        if _goodSplit(aGroup, outFilename):
            mp4List.append(outFilename)
    else:
        for idx, aGroup in enumerate(segmentGroups):
            outFilename = f"{name}_{idx:02d}{ext}"
            logger.info(f"File '{outFilename}' composed of: {aGroup}")
            if _goodSplit(aGroup, outFilename):
                mp4List.append(outFilename)

    # Cleanup files from the working area; important for when in lambda execution
    logger.info("Deleting working files")
    for f in downloadedList:
        os.remove(os.path.join(config['workDirectory'], f))

    for aFile in mp4List:
        _sendToBucket(taskConfig["dstBucket"], taskConfig["dstPrefix"], aFile)


def _determineExtension(downloadedList):
    # To correctly name our audio output files

    # Valid loglevels are: "quiet", "panic", "fatal", "error", "warning", "info", "verbose", "debug", "trace"
    testFile = os.path.join(config['workDirectory'], downloadedList[0])
    commandString = f"{config['ffprobe']} -loglevel error -print_format json -select_streams a:0 -show_entries stream=codec_name {testFile}"
    # logger.debug(f"commandString: {commandString}")

    ffprobeResult = subprocess.run(commandString.split(), capture_output=True, text=True)
    try:
        videoInfo = json.loads(ffprobeResult.stdout)
        # logger.debug(videoInfo) # Print ffprobe's raw JSON result

        theExt = videoInfo["streams"][0]["codec_name"]
    except Exception:
        logger.warning("Ffprobe couldn't get 'audio codec name'; will default to MP4")
        theExt = "mp4"

    return f".{theExt}"


def lambdaHandler(event, context):
    upSince = processInit.preFlightSetup()
    processInit.initSessionObject(config['sessionHeaders'])
    if not processInit.initialize():
        logger.error("Failed to initialize")
        exit(1)

    # Capture our ARN for later use
    GLOBALS.myArn = context.invoked_function_arn

    # Grab input
    try:
        body = json.loads(event['Records'][0]['body'])
        test = body['clipStart']
    except KeyError:
            logger.error('Invalid message received')
            logger.debug(f"Message received is:{event}")
            return {"status": False}

    try:
        # Pre-set values in case execution is interrupted
        dataLevel = AuditLogLevel.INFO
        systemLevel = AuditLogLevel.INFO
        exitMessage = "Exit with errors"

        # Execute!
        trueOrFalse = True
        execute(body)
        exitMessage = f"Normal execution"

    except HPatrolError as e:
        logger.warning(e)
        dataLevel = AuditLogLevel.WARN
        exitMessage = str(e)
        trueOrFalse = True

    except Exception as err:
        logger.exception(f"UNHANDLED EXCEPTION CAUGHT:::{err}")
        systemLevel = AuditLogLevel.CRITICAL
        dataLevel = None
        trueOrFalse = False

    finally:
        nownow = int(time.time())
        logger.info(f'Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}')

        auditUtils.logFromLambda(
            event=event,
            msg=exitMessage,
            arn=GLOBALS.myArn,
            dataLevel=dataLevel,
            lambdaContext=context,
            ip=GLOBALS.perceivedIP,
            systemLevel=systemLevel,
            taskName=GLOBALS.taskName,
            stackName=GLOBALS.projectName,
            subtaskName=GLOBALS.subtaskName,
            enterDatetime=dt.datetime.fromtimestamp(upSince),
            leaveDatetime=dt.datetime.fromtimestamp(nownow),
            # **collectionSummaryArgs
            # collectionSummaryArgs1="some",
            # collectionSummaryArgs2="additional",
            # collectionSummaryArgs3="info"
            )

    toPrint = "Exiting Process"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")

    return {"status": trueOrFalse}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Transcoder from TS or STILLS:\n'\
            '\tCLI version uses testEvent in the code\n'\
            '\tYou need to modify this; see the main fn()',
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument('task',
                        help='task to execute',
                        choices=["transcode", "timelapse", "audio"],
                        )
    args = parser.parse_args()

    upSince = processInit.preFlightSetup()
    processInit.initSessionObject(config['sessionHeaders'])
    if not processInit.initialize():
        logger.error("Failed to initialize")
        exit(1)

    # Don't use proxy for AWS metadata; will timeout if proxy is tried
    # This is the equivalent of doing: $ export no_proxy=169.254.169.254
    try:
        os.environ["no_proxy"] = f"{os.environ['no_proxy']},169.254.169.254"
    except KeyError:
        os.environ["no_proxy"] = "169.254.169.254"

    # Create our ARN for later use
    region = ec2.region
    accountId = ec2.account_id
    instanceId = ec2.instance_id
    arn = f'arn:aws:ec2:{region}:{accountId}:instance/{instanceId}'
    GLOBALS.myArn = arn

    if args.task == "transcode":
        testEvent = {
              "task": args.task
            , "filenameBase": "testTranscode"
            , "outFilename": "aTranscodeTest.mp4"
            , "clipStart": "1688650191"
            , "clipLengthSecs": 920
            , "wrkBucket": config['defaultWrkBucket']
            , "dstBucket": config['defaultWrkBucket']
            , "srcPrefix": "lz/ulCams/a1c2d5816afd5ee5/2022/09/02"
            , "dstPrefix": f"{GLOBALS.deliveryKey}/ulCams/a1c2d5816afd5ee5/2022/09/02"
        }

    elif args.task == "timelapse":
        testEvent = {
              "task": args.task
            , "filenameBase": "artMuseum"
            , "outFilename": "aTimelapseTest.mp4"
            , "clipStart": "1687991162"
            , "clipLengthSecs": 300
            , "wrkBucket": config['defaultWrkBucket']
            , "dstBucket": config['defaultDstBucket']
            , "srcPrefix": "lz/taffy/stills/2023/06/28/artMuseum"
            , "dstPrefix": f"{GLOBALS.deliveryKey}/taffy/artMuseum/2023/06/28"
            , "timelapseFPS": 1
        }

    elif args.task == "audio":
        testEvent = {
              "task": "takeaudio"
            , "filenameBase": "gorillas"
            , "outFilename": "Gorillas.mp4"
            , "clipStart": "1708989461"
            , "clipLengthSecs": 300
            , "wrkBucket": config['defaultWrkBucket']
            , "dstBucket": config['defaultDstBucket']
            , "srcPrefix": "lz/ytTest/2024-02-26"
            , "dstPrefix": f"{GLOBALS.deliveryKey}/audioTest"
        }

    try:
        execute(testEvent)
    except HPatrolError as e:
        logger.error(e)

    nownow = int(time.time())
    logger.info(f'Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}')

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
