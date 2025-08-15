"""
General utility methods
"""


# External libraries import statements
import os
import re
import time
import json
import math
import ctypes
import base36
import codecs
import shutil
import urllib
import random
import string
import hashlib
import zipfile
import logging
import datetime as dt
from datetime import timezone

# Local import statements
from . import timeUtils as tu


logger = logging.getLogger()


def writePidFile(pidDir, appName):
    # Write this process' ID to a file
    filePath = os.path.join(pidDir, appName + ".pid")
    pid = str(os.getpid())
    f = open(filePath, 'w')
    f.write(pid)
    f.write("\n")
    f.close()
    return pid


def writeJsonDataToFile(jsonData, jsonFilename):
    # logger.debug('Writing out JSON file:  %s', jsonFilename)
    with codecs.open(jsonFilename, 'w', 'utf-8') as jsonFile:
        json.dump(jsonData, jsonFile, ensure_ascii=False, indent=4)


def writeFile(outputText, fullFilePath):
    # Writes outputText (as text) to output file
    # Use this method to write a single chunk of text one time only.
    # If you need to write a line at a time, use other methods.
    try:
        fil = open(fullFilePath, 'w')
        fil.write('%s\n' % outputText)
        fil.close()
        logger.info(f"Successful creation of output file '{fullFilePath}'")
        return True
    except Exception as e:
        logger.critical('Unable to write results: {}'.format(e))
        return False


def zipFilesList(filename, allFiles):
    # Notice that the original files being zipped are deleted
    try:
        with zipfile.ZipFile(filename, 'w') as zipMe:
            for file in allFiles:
                zipMe.write(file, os.path.basename(file), compress_type=zipfile.ZIP_DEFLATED)
                os.unlink(file)
    except Exception as e:
        logger.error(f"Can't zip files. {e}")
        return False

    return True


def generateRandomInt(howLargeInBytes=8, signed=True):
    """Generates a random, optionally signed, integer (maximum of 8 bytes)"""
    try:
        if howLargeInBytes > 8:
            howLargeInBytes = 8
    except TypeError:
        howLargeInBytes = 8
    return int.from_bytes(os.urandom(howLargeInBytes), signed=signed, byteorder='little')


def getRandomString(howLarge=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=howLarge))


def getQuasiRandomToken():
    # This here was recreated in Python from a piece of Javascript used in some target sites
    # The JS is this:
    # zx = Math.floor(2147483648*Math.random()).toString(36)+Math.abs(Math.floor(2147483648*Math.random())^Date.now()).toString(36))

    firstHalf = base36.dumps(math.floor(2147483648*random.random()))

    dateNow = math.floor(time.time()*1000)
    # logger.debug(dateNow)
    # Notice that for JS, bitwise operations are limited to 32bits
    # That's why we're using ctypes here
    # Python has a much larger range and would give us the real bitwise xor result
    xoredVal = ctypes.c_int(math.floor(2147483648*random.random())^dateNow).value
    secndHalf = base36.dumps(abs(xoredVal))
    # logger.debug(f"{firstHalf}-{secndHalf}")

    return f"{firstHalf}{secndHalf}"


def returnYMD(timestamp):
    logger.warning("CODECHANGE: *****************************************")
    logger.warning("CODECHANGE: *****************************************")
    logger.warning("CODECHANGE: returnYMD() is in new module timeUtils.py")
    logger.warning("CODECHANGE: *****************************************")
    logger.warning("CODECHANGE: *****************************************")
    raise DeprecationWarning


def returnYMDHMS(timestamp):
    logger.warning("CODECHANGE: ********************************************")
    logger.warning("CODECHANGE: ********************************************")
    logger.warning("CODECHANGE: returnYMDHMS() is in new module timeUtils.py")
    logger.warning("CODECHANGE: ********************************************")
    logger.warning("CODECHANGE: ********************************************")
    raise DeprecationWarning


def returnUtcUnderscores(timestamp, withSeconds=True):
    logger.warning("CODECHANGE: ****************************************************")
    logger.warning("CODECHANGE: ****************************************************")
    logger.warning("CODECHANGE: returnUtcUnderscores() is in new module timeUtils.py")
    logger.warning("CODECHANGE: ****************************************************")
    logger.warning("CODECHANGE: ****************************************************")
    raise DeprecationWarning


def returnUtcDashes(timestamp, withSeconds=True):
    logger.warning("CODECHANGE: ***********************************************")
    logger.warning("CODECHANGE: ***********************************************")
    logger.warning("CODECHANGE: returnUtcDashes() is in new module timeUtils.py")
    logger.warning("CODECHANGE: ***********************************************")
    logger.warning("CODECHANGE: ***********************************************")
    raise DeprecationWarning


def utcfy(theFilename, **kwargs):
    logger.warning("CODECHANGE: *************************************")
    logger.warning("CODECHANGE: *************************************")
    logger.warning("CODECHANGE: utcfy() is in new module timeUtils.py")
    logger.warning("CODECHANGE: *************************************")
    logger.warning("CODECHANGE: *************************************")
    raise DeprecationWarning


def getHeaderLastModDateEpoch(theHeaders):
    logger.warning("CODECHANGE: *********************************************************")
    logger.warning("CODECHANGE: *********************************************************")
    logger.warning("CODECHANGE: getHeaderLastModDateEpoch() is in new module timeUtils.py")
    logger.warning("CODECHANGE: *********************************************************")
    logger.warning("CODECHANGE: *********************************************************")
    raise DeprecationWarning


def dashify(theFilename, timestamp=None, withSeconds=True):
    if not timestamp:
        timestamp = time.time()

    nowDashes = tu.returnUtcDashes(timestamp, withSeconds)
    ext = os.path.splitext(theFilename)[1]
    name = os.path.splitext(theFilename)[0]
    fileName = f"{name}_{nowDashes}{ext}"

    return fileName


def chunks(aList, n):
    """Yield successive n-sized chunks from aList"""
    for i in range(0, len(aList), n):
        yield aList[i:i + n]


def sizeofFormat(num, suffix='B'):
    for unit in ['', 'K', 'M', 'G', 'T', 'P', 'E', 'Z']:
        if abs(num) < 1024.0:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1024.0

    return "%.1f%s%s" % (num, 'Yi', suffix)


def extractHexCode(theString):
    regex = r"0[xX][0-9a-fA-F]+"
    matches = re.search(regex, str(theString))
    if matches:
        return matches.group()

    logger.error(f"Hex code not found on received string: {theString}")
    return 0x000000000000


def downloadProgress(bytesDownloaded, totalBytes):
    logger.debug("DownloadProgress: {:,}/{:,} ({:.0%})".format(bytesDownloaded, totalBytes, bytesDownloaded/totalBytes))
    if bytesDownloaded != totalBytes:
        return
    logger.debug("Download Finished!!!")


def randomSleep(floor=5, ceiling=17):
    logger.debug(f"Sleeping a random time between {floor} and {ceiling} secs...")
    time.sleep(random.randint(floor, ceiling))


def getEnrichments(myArn, perceivedIP, myVersion, proxies):
    enrichments = {}
    now = int(time.time())
    enrichments["x_harvestDate"] = now
    enrichments["x_arn"] = myArn
    enrichments["x_proxies"] = proxies
    enrichments["x_systemIP"] = perceivedIP
    enrichments["x_codeVersion"] = myVersion
    enrichments["x_harvestIsoDate"] = dt.datetime.fromtimestamp(now, tz=timezone.utc).isoformat()

    return enrichments


def checkAndFixUrl(baseUrl: str, urlToCheck: str) -> str:
    # To fix relative URLs
    # logger.debug(f"BASEURL: {baseUrl}")
    # logger.debug(f"TOCHECK: {urlToCheck}")
    baseUrlParsed = urllib.parse.urlparse(baseUrl)
    urlToCheckParsed = urllib.parse.urlparse(urlToCheck)
    # logger.debug(f"BASEURL: {baseUrlParsed}")
    # logger.debug(f"TOCHECK: {urlToCheckParsed}")
    if urlToCheckParsed.scheme == "":
        newUrl = urllib.parse.urlunparse(
            (
                baseUrlParsed.scheme,
                baseUrlParsed.netloc,
                urlToCheckParsed.path,
                "",
                "",
                ""
            )
        )
        logger.info(f"Fixing incomplete URL; new={newUrl}")

        return newUrl
    # If nothing wrong, return the same URL
    return urlToCheck


def concatFiles(allFiles, workDir, deleteOrig=False):
    logger.info(f"Concatenating {len(allFiles)} files")
    outFile = f"{workDir}/{generateRandomInt(signed=False)}.tmp"
    with open(outFile,'wb') as wfd:
        for aFile in allFiles:
            localFile = f"{workDir}/{os.path.basename(aFile)}"
            # logger.debug(f"GOING FOR:{localFile}")
            with open(localFile,'rb') as rfd:
                shutil.copyfileobj(rfd, wfd)

    theSize = sizeofFormat(os.path.getsize(outFile))
    logger.info(f"File concatenated as '{outFile}' ({theSize})")

    if deleteOrig:
        logger.info(f"Deleting concatenation source files")
        for aFile in allFiles:
            localFile = f"{workDir}/{os.path.basename(aFile)}"
            os.unlink(localFile)

    return outFile


def getHashFromData(data):
    # logger.debug(f"Creating hash")
    md5 = hashlib.md5(data).hexdigest()
    # logger.debug(f"MD5 ->{md5}<-")
    return md5


def getHashFromFile(workDir, fileName):
    fullFilePath = os.path.join(workDir, fileName)
    with open(fullFilePath, 'rb') as f:
        theContents = f.read()

    return getHashFromData(theContents)


def createEmptyHashFile(md5, workDir):
    fullFilePath = os.path.join(workDir, f"{md5}.md5")
    with open(fullFilePath, 'a'):
        os.utime(fullFilePath, None)
    logger.debug(f"Hash file created: {md5}.md5")

    return fullFilePath


def makeHashFileFromData(data, workDir, fileName=None):
    # If fileName is provided, a file will be created and its content will be the hash
    # If no fileName is provided a zero-length file will be created; its name will be the hash
    md5Returned = getHashFromData(data)

    if fileName:
        fullFilePath = os.path.join(workDir, f"{fileName}.md5")
        # Create hash id file; open as text
        with open(fullFilePath, 'w') as f:
            # print(md5Returned, file=f)
            f.write(md5Returned)
        logger.debug(f"Hash file created: {fileName}.md5")

        return fullFilePath

    else:
        return createEmptyHashFile(md5Returned, workDir)


def findParenPairs(s, lookFor):
    if lookFor == '(':
        closingMark = ')'
    elif lookFor == '{':
        closingMark = '}'
    elif lookFor == '[':
        closingMark = ']'
    else:
        raise ValueError("Invalid delimiter")

    retVal = {}
    pstack = []
    for i, c in enumerate(s):
        if c == lookFor:
            pstack.append(i)
        elif c == closingMark:
            if len(pstack) == 0:
                raise IndexError(f"No matching closing parens at: {i}")
            retVal[pstack.pop()] = i + 1
    if len(pstack) > 0:
        raise IndexError(f"No matching opening parens at: {pstack.pop()}")

    return retVal


def getRegionCode(region: str) -> str:
    """
    Takes an expanded AWS region name and returns the corresponding region code
    If the long name is not found, the received argument is returned
    """
    regionMap = {
          "United States (N. Virginia)": "us-east-1"
        , "United States (Ohio)": "us-east-2"
        , "United States (N. California)": "us-west-1"
        , "United States (Oregon)": "us-west-2"
        , "Africa (Cape Town)": "af-south-1"
        , "Asia Pacific (Hong Kong)": "ap-east-1"
        , "Asia Pacific (Mumbai)": "ap-south-1"
        , "Asia Pacific (Hyderabad)": "ap-south-2"
        , "Asia Pacific (Singapore)": "ap-southeast-1"
        , "Asia Pacific (Sydney)": "ap-southeast-2"
        , "Asia Pacific (Jakarta)": "ap-southeast-3"
        , "Asia Pacific (Melbourne)": "ap-southeast-4"
        , "Asia Pacific (Malaysia)": "ap-southeast-5"
        , "Asia Pacific (Thailand)": "ap-southeast-7"
        , "Asia Pacific (Tokyo)": "ap-northeast-1"
        , "Asia Pacific (Seoul)": "ap-northeast-2"
        , "Asia Pacific (Osaka)": "ap-northeast-3"
        , "Canada (Central)": "ca-central-1"
        , "Canada West (Calgary)": "ca-west-1"
        , "Europe (Frankfurt)": "eu-central-1"
        , "Europe (Zurich)": "eu-central-2"
        , "Europe (Ireland)": "eu-west-1"
        , "Europe (London)": "eu-west-2"
        , "Europe (Paris)": "eu-west-3"
        , "Europe (Milan)": "eu-south-1"
        , "Europe (Spain)": "eu-south-2"
        , "Europe (Stockholm)": "eu-north-1"
        , "Middle East (Bahrain)": "me-south-1"
        , "Middle East (UAE)": "me-central-1"
        , "Mexico (Central)": "mx-central-1"
        , "South America (Sao Paulo)": "sa-east-1"
        , "Israel (Tel Aviv)": "il-central-1"
    }
    try:
        return regionMap[region]
    except:
        return region
