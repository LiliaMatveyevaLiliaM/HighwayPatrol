# External libraries import statements
import os
import time
import json
import base64
import asyncio
import logging
import subprocess

# FIXME: Playwright lambda layer too big?
# Disabled until solved
# This import affects all other non-playwright collectors because of size constraints
# from playwright.async_api import async_playwright


# This application's import statements
try:
    # These are for when running in an EC2
    import systemSettings
    from exceptions import *
    import superGlblVars as GLOBALS
    from superGlblVars import config
    from utils import hPatrolUtils as hput

except ModuleNotFoundError as err:
    # These are for when running in a Lambda
    print(f"Loading module for lambda execution: {__name__}")
    from src.python.exceptions import *
    from src.python import systemSettings
    from src.python.superGlblVars import config
    from src.python import superGlblVars as GLOBALS
    from src.python.utils import hPatrolUtils as hput


logger = logging.getLogger()


# This captures a websocket stream using playwright handlers
# Eventually there might be multiple stream types and selectors
# Other stream types will have different handlers
# Other selector schemes will be handled in here
async def captureWebsocketStream(ap, prefixBase, wrkBucketName, lambdaContext=None):
    # Calculate the filemane for local saving
    tsLastModDate = int(time.time())
    ourTsFilename = f"{hput.formatNameBase(ap['filenameBase'], ap['deviceID'])}_{tsLastModDate}.ts"
    framesDir = config['workDirectory']
    os.makedirs(framesDir, exist_ok=True)
    frameCounter = {"count": 0}
    numFramesPerStore = 10  # May turn into an aimpoint element
    frameFiles = []
    curFrames = [] # Used to upload smaller chunks
    initPath = os.path.join(framesDir, f"{ourTsFilename}_init.mp4") # The path to the initial segment

    logger.info(f"local Dir: {framesDir}")
    logger.info(f"local filename: {ourTsFilename}")

    breakPoint, theSleep, sleepyFraction = hput.calculateExecutionStop(
        ap, lambdaContext
    )
 

    async def handleWebsocket(ws):
        logger.info(f"WebSocket connected: {ws.url}")
        # framereceived is the message flag that the signifies what the client sent to us
        ws.on("framereceived", handleFrameReceived)
        ws.on("framesent", lambda payload: logger.info(f"Frame sent ({len(payload)} bytes)"))
        ws.on("close", lambda : logger.info("WebSocket closed"))
 

    async def handleFrameReceived(payload):
        if isinstance(payload, bytes):
            frameCounter["count"] += 1
            filename = os.path.join(framesDir, f"{ourTsFilename}_{int(time.time())}_frame_{frameCounter['count']:05d}.bin")
            with open(filename, "wb") as f:
                f.write(payload)
            if frameCounter["count"] % 1000 == 0:
                logger.info(f"📦 Saved binary frame {filename} ({len(payload)} bytes)") # KEEP THE EMOJI IN PRODUCTION. IT'S LAW!!!!! -william11
            frameFiles.append(filename)

            # Store every X frames
            curFrames.append(payload)
            if frameCounter["count"] % numFramesPerStore == 0:
                _uploadFrames(ap, wrkBucketName, os.path.join(framesDir, 
                    f"{hput.formatNameBase(ap['filenameBase'], ap['deviceID'])}_" + 
                    f"{time.time():.3f}"),
                    initPath, curFrames, prefixBase)
                logger.info(f"Saved {len(curFrames)} frames to S3")
                curFrames.clear()

        else:
            # The intial header is sent as JSON (metadata); we'll need that to complete the video
            # This section may need to be updated for non-mp4 streams, but we haven't seen one yet
            try:
                data = json.loads(payload)
                if data.get("type") == "mse_init_segment":
                    tracks = data.get("tracks", [])
                    for track in tracks:
                        if track.get("content") == "video":
                            initPayloadB64 = track["payload"]
                            initPayload = base64.b64decode(initPayloadB64)
                            with open(initPath, "wb") as f:
                                f.write(initPayload)
                            logger.info(f"Saved initialization segment as {initPath}")
                            frameFiles.append(initPath)

            except json.JSONDecodeError:
                logger.error(f"Received Something Other Than JSON: {payload}")
 
    async with async_playwright() as p:
        # Important! You'll need to use an actual Chrome instance to do this
        # Chromium doesn't have the h.264 codec to play the video
        browser = await p.chromium.launch(channel='chrome', headless=True, args=[
            "--headless=new",
            "--disable-gpu",
            "--single-process",
            "--no-zygote",
            "--remote-allow-origins=*"
            ])

        # Testing with Firefox
        # browser = p.firefox.launch(headless=True)

        # Note the browser being used
        logger.info(f"Browser Type: {browser.browser_type}")
        logger.info(f"Browser Version: {browser.version}")

        # How do we get to the stream
        try: 
            url= ap["accessUrl"]
            playwrightData = ap["playwrightData"]
            videoSelector = playwrightData["streamSelector"]
            videoAction = playwrightData["streamAction"]

        except KeyError as err:
            logger.error("Parameter unspecified in input configuration")
            logger.error(f"Be sure to specify {err} in JSON file")
            raise HPatrolError("Parameter unspecified in input configuration")

        # Context is basically the browser window
        # Use a proxy if we're in an EC2
        if lambdaContext == None:
            context = await browser.new_context(proxy={"server":config["proxy"]})
        else:
            context = await browser.new_context()
        page = await context.new_page()

        # Stealth makes the browser stealthy like a ninja
        # from playwright_stealth import stealth_async
        # await stealth_async(page)
 
        # This is a listener callback checking for websocket connections
        page.on("websocket", handleWebsocket)
 
        # This section will be different for each website - or at least the video_selector will be
        # Navigate to the page
        logger.info(f"Navigating to {url}")
        await page.goto(url, wait_until="load", timeout=300000) # note that we set the timeout to five minutes
        # Wait for the video to load
        logger.info(f"Waiting for selector: {videoSelector}")
        await page.wait_for_selector(videoSelector)

        # Action to start the video
        if videoAction == "click":
            logger.info(f"Clicking on selector to start video")
            await page.click(videoSelector)
        elif videoAction == "none":
            logger.info("Video will autoplay")
        else:
            logger.info(f"streamAction '{videoAction}' not recognized or handled")
 
        # Wait for pollFrequency seconds after clicking the video
        # If all goes well, we'll get pollFrequency seconds recorded
        while True:
            logger.info(f"Checking bail - break at {breakPoint}, time is {time.time()}")

            # Saves frames to S3 here; Async/Event-based operation for handleFrameReceived()

            if hput.itsTimeToBail(lambdaContext, breakPoint, theSleep):
                break
            time.sleep(theSleep / 1000)
 
        # Close the browser
        await browser.close()

        # Upload the last set of frames
        _uploadFrames(ap, wrkBucketName, os.path.join(framesDir, 
            f"{hput.formatNameBase(ap['filenameBase'], ap['deviceID'])}_" + 
            f"{time.time():.3f}"),
            initPath, curFrames, prefixBase)
        logger.info(f"Saved {len(curFrames)} frames to S3")

        return frameFiles


def handleVideos(collType, prefixBase, ap, lambdaContext=None):
    # Note that Collectors are assumed to be running because they are indeed supposed to be
    # running; this is handled by the Scheduler; hence start time is not checked; only stop time
    breakPoint, theSleep, sleepyFraction = hput.calculateExecutionStop(
        ap, lambdaContext
    )

    logger.info(f"Playwright handleVideos started - end at {breakPoint}")
    # logger.debug(f"lambdaContext: {lambdaContext}")

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
        pd = ap["playwrightData"]
        streamType = pd["streamType"]
        streamSelector = pd["streamSelector"]
    except KeyError as err:
        raise HPatrolError(f"PlaywrightData not correct: {err}")

    try:
        if streamType == "websocket":
            # Sometimes we need to obtain the m3u8 URL from a different URL
            logger.info("Playwright Type selected: websocket")

            # This sets up Playwright to capture the stream - getting the selector and then
            # going asynchronous to collect for as long as the ap tells us to
            frameFiles = asyncio.run(captureWebsocketStream(ap, prefixBase, wrkBucketName, lambdaContext))

        else:
            logger.error("Playwright Stream type undefined")
            raise HPatrolError("Playwright Stream type undefined")

    except KeyError as err:
        logger.error("Parameter unspecified in input configuration")
        logger.error(f"Be sure to specify {err} in JSON file")
        raise HPatrolError("Parameter unspecified in input configuration")

    if len(frameFiles) == 0:
        logger.warning("No new frames captured")
        return

    if decoy:
        # Don't upload; not even the .m3u8
        logger.info(f"Decoy aimpoint; NOT pushing to S3")
        return

    finalFile = _uploadSegments(ap, wrkBucketName, frameFiles, prefixBase)
    if not finalFile:
        logger.warning(f"No new segments found")
        return


# Upload a subset of frames, or even a single frame
def _uploadFrames(ap, bucketName, localFilename, initPath, frameList, prefixBase):

    fileName = f"{localFilename}.mp4"
    s3FileName = fileName.split("/")[-1]

    # Concatenate files and init segment to disk
    with open(fileName, "wb") as outputFile:
        # Write the initialization segment
        with open(initPath, "rb") as f:
            outputFile.write(f.read())

        # Write out the frames
        for frame in frameList:
            outputFile.write(frame)
    
    # Upload to S3
    if not _wasSaveSuccessful(
        fileName, prefixBase, bucketName, s3FileName, None
    ):
        raise HPatrolError("Error pushing to S3")


def _uploadSegments(ap, bucketName, origList, prefixBase):
    try:
        doConcat = True == ap["concatenate"]
    except KeyError:
        doConcat = False

    try:
        singleCollector = True == ap["singleCollector"]
    except KeyError:
        singleCollector = False

    # Concatenate the files - make one final file
    fileName = origList[0].split(".")[0] + ".mp4"
    logger.info(f"Concatenating files into {fileName}")
    with open(fileName, "wb") as outputFile:
        for frame in origList:
            with open(frame, "rb") as f:
                outputFile.write(f.read())

    # Grab the filename with no path
    s3FileName = fileName.split("/")[-1]

    if not _wasSaveSuccessful(
        fileName, prefixBase, bucketName, s3FileName, None
    ):
        raise HPatrolError("Error pushing to S3")

    return fileName


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
