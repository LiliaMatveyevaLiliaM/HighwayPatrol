"""
Module to compare the latest list of IDs with a master list
and note any differences. A copy of the master list file is on
the metadataReports S3 folder while date-stamped copies are
written to subfolders within it.

Script also indicates whether aimpoints should be re-created.

If there is no master list (the first time this script is
run) aimpoints are to be created.

The use of the marker "Deleted" here, in reality marks when a certain
device was "Last Seen". That is, "Deleted" doesn't indicate
when it was deleted, but when it was actually last seen.
"""

# External libraries import statements
import os
import math
import logging
import datetime as dt


# This application's import statements
try:
    # These are for when running in an EC2
    from exceptions import *
    import superGlblVars as GLOBALS
    from superGlblVars import config
    from orangeUtils import utils as ut
    from orangeUtils import timeUtils as tu

except ModuleNotFoundError as err:
    # These are for when running in a Lambda
    print(f"Loading module for lambda execution: {__name__}")
    from src.python.exceptions import *
    from src.python.superGlblVars import config
    from src.python.orangeUtils import utils as ut
    from src.python import superGlblVars as GLOBALS
    from src.python.orangeUtils import timeUtils as tu


logger = logging.getLogger()
extraColumns = ("Added", "Modified", "Last Seen")


def writeAPs(upSince, population, keysAndTitles, domFolder, masterFile, bucketName=config["defaultDstBucket"], selectedList=None):
    """
    Compares current camera population metadata with previously collected metadata
    If changes are found, the new metadata will be written to S3
    To indicate interest in all aimpoints, leave selectedList as None
    Returns:
        boolean; indicates whether or not metadata has changed and if aimpoints should be re-written
    """

    writeAimpoints = False

    # Convert all values to string 'cause we compare against the TSV, which is all text
    # Later when comparing lonLat we do switch those to floats
    population = [{key: str(val) for key, val in dict.items()} for dict in population]

    currentTupleList = _getCurrentTupleList(population, keysAndTitles[0])
    if not currentTupleList:
        logger.error("No current tuple list returned")
        raise HPatrolError("No tuple list")

    masterTupleList = _getMasterTupleList(domFolder, masterFile, bucketName)

    if not masterTupleList:
        writeAimpoints = True
        logger.info("No master list; creating the initial one")
        if _masterListWritten(upSince, domFolder, currentTupleList, masterFile, keysAndTitles[1], bucketName):
            _writeMasterJson(population, domFolder, masterFile, bucketName)
        else:
            logger.error("Unable to create master metadata list")
        return writeAimpoints

    logger.info("Comparing against master list")

    lastMod = GLOBALS.S3utils.getFileMetadata(bucketName,
                            f"{domFolder}/{GLOBALS.mtdtReports}/{masterFile}.tsv",
                            "LastModified"
                             )
    lastMod = int(lastMod.timestamp())
    writeAimpoints, newTupleList = _compareTupleLists(upSince, lastMod, masterTupleList, currentTupleList, selectedList, keysAndTitles)

    if newTupleList:
        if _masterListWritten(upSince, domFolder, newTupleList, masterFile, keysAndTitles[1], bucketName):
            _writeMasterJson(population, domFolder, masterFile, bucketName)
        else:
            logger.error("Unable to create master metadata list")

    return writeAimpoints


def _getCurrentTupleList(allCamsDict, keys):
    logger.info("Creating current comparison list")

    idTupleList = []
    for idx, aCamDict in enumerate(allCamsDict, start=1):
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and idx == 6:
            logger.debug(f"Not running on PROD; exiting at population device #{idx}")
            break

        oneCam = []
        try:
            for aKey in keys:
                oneCam.append(aCamDict[aKey])
            # Adding Added, Modified, Deleted, etc. just to maintain the overal data structure
            # "None" here in all since we're creating this one from scratch
            # Note that "None" here is used only in memory; not exported
            extras = ["None" for x in range(len(extraColumns))]
            oneCam.extend(extras)
        except KeyError:
            logger.debug(f"Unable to create data; missing key '{aKey}' in {aCamDict}")
            continue
        idTupleList.append(tuple(oneCam))

    idTupleList = sorted(idTupleList, key=_naturalKeys)
    return idTupleList


def _getMasterTupleList(domFolder, masterFileName, dstBucket):
    # The first time this is run, there will be no master list
    masterTpls = []
    masterReportFile = f"{masterFileName}.tsv"

    logger.info(f"Reading previous master file '{masterReportFile}'")
    respText = GLOBALS.S3utils.readFileContent(
        dstBucket, 
        f"{domFolder}/{GLOBALS.mtdtReports}/{masterReportFile}")
    if not respText:
        return masterTpls

    # Convert file contents to a list of strings
    tmpList = respText.split('\n')

    for lin in tmpList[1:]: # Start looping but skip the title line
        if not lin:
            # Skip any possible blank lines
            continue

        allItems = lin.split('\t')
        # Adding "Nones"; note that "None" here is used only in memory; not exported
        allItems = [x if x!="" else "None" for x in allItems]

        try:
            masterTpls.append(tuple(allItems))
        except Exception as err:
            logger.warning(f"Unable to append tuple from master for line: {lin}:::{err}")
            continue

    masterTpls = sorted(masterTpls, key=_naturalKeys)
    return masterTpls


def _naturalKeys(theTuple):
    # For human sort (natural sort) of alphanumeric strings or numbers
    # FIXME: Improve naturalKeys logic; hasn't been thoroughly tested
    #        Will break if it receives an int as input
    # Original attempt was:
    # return int(theTuple[0]) if theTuple[0].isdigit() else theTuple[0]

    try:
        if(theTuple.isdigit()):
            return int(theTuple[0])
        else:
            return str(theTuple[0]) if theTuple[0].isdigit() else theTuple[0]
    except AttributeError:
        return str(theTuple[0]) if theTuple[0].isdigit() else theTuple[0]


def _compareTupleLists(upSinceSecs, prevDate, inMasterTuples, inCurrentTuples, selected, keysAndTitles):
    # This method returns 2 values:
    # 1 - whether to re-write aimpoints (boolean)
    # 2 - list of tuples if the master list is to be re-written (list)
    newTpls = []
    writeAimpoints = False
    writeMasterList = False

    selectedIds = None
    if selected:
        selectedIds = [key for key,value in selected.items()]

    prevDateStamp = dt.datetime.fromtimestamp(prevDate).strftime("%m/%d/%Y")
    todayDateStamp = dt.datetime.fromtimestamp(upSinceSecs).strftime("%m/%d/%Y")

    masterTuples = sorted(inMasterTuples, key=_naturalKeys)
    currentTuples = sorted(inCurrentTuples, key=_naturalKeys)

    crntIter = iter(currentTuples)
    mstrIter = iter(masterTuples)
    crntTpl = next(crntIter)
    mstrTpl = next(mstrIter)
    crntDone = mstrDone = False

    try:
        latIndex = keysAndTitles[1].index("Latitude")
        lonIndex = keysAndTitles[1].index("Longitude")
    except ValueError:
        latIndex = None
        lonIndex = None

    totalKeys = len(keysAndTitles[0])
    # Added, Modified, and Deleted are the last index positions
    addPos = totalKeys + 0
    modPos = totalKeys + 1
    delPos = totalKeys + 2

    while not crntDone and not mstrDone:
        coreTuple = crntTpl[:totalKeys] # Grab only the core elements of the current tuple

        if crntTpl[0] < mstrTpl[0]:
            _logTuple("This missing from master list; adding it", crntTpl, totalKeys, todayDateStamp)
            writeMasterList = True
            if selectedIds:
                if crntTpl[0] in selectedIds:
                    writeAimpoints = True
            else:
                writeAimpoints = True

            addTpl = coreTuple + (todayDateStamp, "None", "None")
            newTpls.append(addTpl)   # Add missing item

            try:
                crntTpl = next(crntIter)
            except StopIteration:
                crntDone = True
            continue

        elif crntTpl[0] > mstrTpl[0]:
            # If master tuple already Not Seen, just maintain tuple as is
            # Note that delPosition is the last column, i.e. "Last Seen"
            if len(mstrTpl) > delPos and mstrTpl[delPos] != "None":
                newTpls.append(mstrTpl)
                try:
                    mstrTpl = next(mstrIter)
                except StopIteration:
                    mstrDone = True
                continue

            _logTuple("This missing from current list, marking it as Last Seen", mstrTpl, totalKeys, mstrTpl[addPos], mstrTpl[modPos], prevDateStamp)
            writeMasterList = True
            if selectedIds:
                if mstrTpl[0] in selectedIds:
                    writeAimpoints = True
            else:
                writeAimpoints = True
    
            mstrCore = mstrTpl[:totalKeys] # Grab only the core elements of the *master* tuple
            delTpl = mstrCore + (mstrTpl[addPos], mstrTpl[modPos], prevDateStamp)
            newTpls.append(delTpl)   # Mark item deleted
            try:
                mstrTpl = next(mstrIter)
            except StopIteration:
                mstrDone = True
            continue

        # IDs are equal
        # First, check if master is marked as last seen; if so, re-add that ID
        if len(mstrTpl) > delPos and mstrTpl[delPos] != "None":
            _logTuple("This Last Seen being re-added", mstrTpl, totalKeys, mstrTpl[addPos], mstrTpl[modPos], prevDateStamp)
            writeMasterList = True

            addTpl = coreTuple + (todayDateStamp, "None", "None")
            newTpls.append(addTpl)   # Add 'new' item

            if selectedIds:
                if mstrTpl[0] in selectedIds:
                    writeAimpoints = True
            else:
                writeAimpoints = True
            try:
                crntTpl = next(crntIter)
            except StopIteration:
                crntDone = True
            try:
                mstrTpl = next(mstrIter)
            except StopIteration:
                mstrDone = True
            continue

        # IDs are equal
        # Master not marked "Last Seen"; check for differences
        diffStr = ""
        for idx, aTitle in enumerate(keysAndTitles[1]):
            if crntTpl[idx] != mstrTpl[idx]:
                if idx == latIndex or idx == lonIndex:  # only for longitude or latitude
                    if not math.isclose(float(crntTpl[idx]), float(mstrTpl[idx])):
                        writeMasterList = True
                        diffStr += f" {aTitle}"
                else:
                    writeMasterList = True
                    diffStr += f" {aTitle}"

        if diffStr:
            if selectedIds:
                if mstrTpl[0] in selectedIds:
                    writeAimpoints = True
            else:
                writeAimpoints = True

            extras = (mstrTpl[addPos], todayDateStamp, mstrTpl[delPos])
            modTpl = coreTuple + extras
            newTpls.append(modTpl)
            
            logger.info(f"ID with differing elements:{diffStr}")
            mstrCore = mstrTpl[:totalKeys]  # Grab only the core elements of the master tuple
            logger.info(f"Old values: {mstrCore}")
            logger.info(f"New values: {coreTuple}")
        else:
            newTpls.append(mstrTpl)

        try:
            crntTpl = next(crntIter)
        except StopIteration:
            crntDone = True
        try:
            mstrTpl = next(mstrIter)
        except StopIteration:
            mstrDone = True

    # If current list not exhausted, add remaining values
    while not crntDone:
        writeMasterList = True
        if selectedIds:
            if crntTpl[0] in selectedIds:
                writeAimpoints = True
        else:
            writeAimpoints = True
        _logTuple("This missing from master list; adding it", crntTpl, totalKeys, todayDateStamp)
        coreTuple = crntTpl[:totalKeys] # Grab only the core elements of the current tuple
        addTpl = coreTuple + (todayDateStamp, "None", "None")
        newTpls.append(addTpl)

        try:
            crntTpl = next(crntIter)
        except StopIteration:
            crntDone = True

    # Remaining items on master list must be deleted items
    while not mstrDone:
        # If master tuple already Not Seen, just maintain tuple as is
        # Note that delPosition is the last column, i.e. "Last Seen"
        if len(mstrTpl) > delPos and mstrTpl[delPos] != "None":
            newTpls.append(mstrTpl)
            try:
                mstrTpl = next(mstrIter)
            except StopIteration:
                mstrDone = True
            continue

        _logTuple("This missing from current list, marking it as Last Seen", mstrTpl, totalKeys, mstrTpl[addPos], mstrTpl[modPos], prevDateStamp)
        writeMasterList = True
        if selectedIds:
            if mstrTpl[0] in selectedIds:
                writeAimpoints = True
        else:
            writeAimpoints = True

        mstrCore = mstrTpl[:totalKeys] # Grab only the core elements of the *master* tuple
        delTpl = mstrCore + (mstrTpl[addPos], mstrTpl[modPos], prevDateStamp)
        newTpls.append(delTpl)   # Mark item deleted
        try:
            mstrTpl = next(mstrIter)
        except StopIteration:
            mstrDone = True

    logger.info(f"Write aimpoints? {writeAimpoints}")
    if not writeMasterList:
        # Throughout this f() we're re-creating the master list
        #   but if it hasn't really changed, don't send it back
        return writeAimpoints, []
    return writeAimpoints, newTpls


def _logTuple(msg, tpl, keys, added='', modified='', deleted=''):
    if added == "None":
        added = "--"
    if modified == "None":
        modified = "--"
    if deleted == "None":
        deleted = "--"

    coreTuple = tpl[:keys]  # Grab only the core elements of the tuple
    allIn = " ".join(coreTuple)
    logger.info(f"{msg} \"{allIn}\" A:{added} M:{modified} D:{deleted}")


def _writeMasterJson(population, domFolder, masterFileName, bucket):
    masterFileName = f"{masterFileName}.json"
    filePath = os.path.join(config['workDirectory'], masterFileName)

    try:
        ut.writeJsonDataToFile(population, filePath)
    except Exception as err:
        logger.exception(f"Error creating report master file:::{err}")
        raise HPatrolError("Error creating report master")

    result = GLOBALS.S3utils.pushToS3(filePath,
                            f"{domFolder}/{GLOBALS.mtdtReports}",
                            bucket,
                            s3BaseFileName=masterFileName,
                            deleteOrig=GLOBALS.onProd,
                            extras={'ContentType': 'application/json'})


def _masterListWritten(upSince, domFolder, inList, masterFileName, rprtTitles, bucket=config["defaultDstBucket"]):
    coreCols = len(rprtTitles)
    masterFileName = f"{masterFileName}.tsv"

    outStrList = []
    TAB = '\t'  # workaround; prior to Python 3.12 f-string expression part cannot include backslashes
    outStrList.append(f"{TAB.join(rprtTitles)}{TAB}{TAB.join(extraColumns)}")

    for aDevice in inList:
        extras = []

        for mtdtElement in aDevice[coreCols:]:  # focus only on the extra columns on this loop
            if mtdtElement != "None":
                extras.append(mtdtElement)
            else:
                extras.append("")

        newTpl = aDevice[:coreCols] + tuple(extras) # notice we select only the core part of aDevice
        outStr = "\t".join(map(str, newTpl))    # using map(str) in case there are any numbers in newTpl
        outStrList.append(outStr)

    filePath = os.path.join(config['workDirectory'], f"{masterFileName}")

    if not ut.writeFile("\n".join(outStrList), filePath):
        logger.exception("Unable to write master list")
        return False

    # Upload current file
    GLOBALS.S3utils.pushToS3(filePath,
                            f"{domFolder}/{GLOBALS.mtdtReports}",
                             bucket,
                             s3BaseFileName=masterFileName)

    # Upload historical date-stamped copy
    year, month, day = tu.returnYMD(upSince)
    splitted = os.path.splitext(masterFileName)
    dateStampFileName = f"{splitted[0]}_{year}{month}{day}{splitted[1]}"
    dateStampFolder = f"{domFolder}/{GLOBALS.mtdtReports}/{year}/{month}"
    GLOBALS.S3utils.pushToS3(filePath, 
                             dateStampFolder,
                             bucket,
                             deleteOrig=GLOBALS.onProd,
                             s3BaseFileName=dateStampFileName)
    return True
