"""
Time handling utility methods
"""


# External libraries import statements
import os
import re
import time
import logging
import zoneinfo
import datetime as dt
from random import shuffle
from datetime import timezone


logger = logging.getLogger()


def returnYMD(timestamp):
    inDate = dt.datetime.fromtimestamp(timestamp, tz=timezone.utc)
    year = str(inDate.strftime('%Y'))
    month = str(inDate.strftime('%m'))
    day = str(inDate.strftime('%d'))

    return year, month, day


def returnYMDHMS(timestamp):
    inDate = dt.datetime.fromtimestamp(timestamp, tz=timezone.utc)
    year = str(inDate.strftime('%Y'))
    month = str(inDate.strftime('%m'))
    day = str(inDate.strftime('%d'))
    hour = str(inDate.strftime('%H'))
    mins = str(inDate.strftime('%M'))
    secs = str(inDate.strftime('%S'))

    return year, month, day, hour, mins, secs


def returnUtcUnderscores(timestamp, withSeconds=True):
    year, month, day, hour, mins, secs = returnYMDHMS(timestamp)
    if withSeconds:
        return f"{year}_{month}_{day}_{hour}_{mins}_{secs}"

    return f"{year}_{month}_{day}_{hour}_{mins}"


def returnUtcDashes(timestamp, withSeconds=True):
    year, month, day, hour, mins, secs = returnYMDHMS(timestamp)
    if withSeconds:
        return f"{year}-{month}-{day}-{hour}-{mins}-{secs}"

    return f"{year}-{month}-{day}-{hour}-{mins}"


def utcfy(theFilename, **kwargs):
    now = time.time()
    nowUscore = returnUtcUnderscores(now, **kwargs)
    ext = os.path.splitext(theFilename)[1]
    name = os.path.splitext(theFilename)[0]
    fileName = f"{name}_{nowUscore}{ext}"

    return fileName


def getHeaderLastModDateEpoch(theHeaders):
    try:
        dateTime = theHeaders['Last-Modified']
    except KeyError:
        dateTime = theHeaders['Date']

    try:
        modTime = dt.datetime.strptime(dateTime, '%a, %d %b %Y %X %Z')
    except ValueError:
        try:
            modTime = dt.datetime.strptime(dateTime, '%A, %d-%b-%Y %X %Z')
        except ValueError:
            # Example: 'Sat, 25 Feb 2023 01:58:04 CST' (China Standard Time)
            modTime = dt.datetime.strptime(dateTime, '%a, %d %b %Y %X CST')
    # Suspended TODO: Add other formats conversions
    #                 Just these for now to force an error if we see any new formats
    # logger.debug(f"modTime->{modTime}<-")

    return int(time.mktime(modTime.timetuple()))


def getAllTZs(doPrint=False):
    allZones = sorted(zoneinfo.available_timezones())
    if doPrint:
        print("====All Available Timezones====")
        for timeZone in allZones:
            print(timeZone)

    return(allZones)


def getWorkHours(when=None, workHours=None):
    # Returns the specified target's timezone time and a list of working hours
    #
    # example:
    # -INPUTS-
    # when       = dt.datetime.now()
    # workHours  = {"tz": "Pacific/Auckland", "hrs": ["0800-0955", "1300-1730"], "rndm": 15}
    #
    # -OUTPUT-
    # TargetTime = 2023-09-12 03:04:25.447934+12:00
    # WorkHours  = ['0809-1000', '1300-1728']

    try:
        targetTz = workHours['tz']
        targetTz = zoneinfo.ZoneInfo(targetTz)
    except (KeyError, TypeError) as err:
        logger.warning(f"Working Hours parameter missing; using default values: {err}")
        targetTz = zoneinfo.ZoneInfo("UTC")

    try:
        theRanges = workHours['hrs']
    except (KeyError, TypeError) as err:
        logger.warning(f"Working Hours parameter missing; using default values: {err}")
        # Default is 24hr operation
        theRanges = ['0000-2359']

    try:
        targetTime = when.astimezone(targetTz)
        # logger.debug(f"When: {when}")
    except AttributeError as err:
        when = dt.datetime.now()
        targetTime = when.astimezone(targetTz)
        # logger.debug(f"When: {when}")

    try:
        theRanges = randomizeTimeRanges(theRanges, workHours['rndm'])
    except (KeyError, TypeError):
        # Don't randomize
        pass

    logger.info(f"TargetTime:  {targetTime.strftime('%H%M (%Y-%m-%d)')} \"{targetTz}\"")
    logger.info(f"WorkHours: {theRanges}")
    return targetTime, theRanges


def randomizeTimeRanges(timeRanges: list, randomFactor: int):
    # Adds some randomization to the provided time ranges based on the randomFactor
    # Format for input time ranges is "hrStart-hrStop", i.e. ["xxxx-yyyy", "aaaa-bbbb"...]'
    # in 24hour format e.g. ["0800-1200", "1300-1730"]
    regex = r"(\d{4})-(\d{4})"

    if randomFactor > 59: randomFactor = 59
    retList = []
    for aRange in timeRanges:
        matches = re.search(regex, aRange)
        if matches:
            # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
            # for groupNum in range(0, len(matches.groups())):
            #     groupNum = groupNum + 1
            #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

            tmp = list(range(-randomFactor, randomFactor+1))
            shuffle(tmp)
            randomBuffer = tmp.pop(0)
            start = int(matches.group(1)) + randomBuffer
            if start < 0: start = 0
            if start%100 > 59: start = start - start%100 + 100

            randomBuffer = tmp.pop(0)
            stop = int(matches.group(2)) + randomBuffer
            if stop > 2399: stop = 2359
            if stop%100 > 59: stop = stop - stop%100 + 100

            retList.append(f"{start:04}-{stop:04}")

    return retList


def isTimeInRange(aTime: dt, aRange: str):
    """Returns whether aTime is within the range of aRange"""
    # Takes a time range in 24hour format e.g. "0800-1200"

    # This here just for documentation
    # startHr = int(aRange[0:2])
    # startMn = int(aRange[2:4])
    # endHr = int(aRange[5:7])
    # endMn = int(aRange[7:9])
    # start = dt.time(startHr, startMn, 0)
    # end = dt.time(endHr, endMn, 0)

    # start = dt.time(0, 0, 0)
    # end = dt.time(23, 55, 0)
    # current = dt.datetime.now().time()
    # print(start <= current <= end)

    start = dt.time(int(aRange[0:2]), int(aRange[2:4]), 0)
    end = dt.time(int(aRange[5:7]), int(aRange[7:9]), 0)
    # logger.debug(f"Is '{aTime.time()}' within [{start}-{end}]?")
 
    return start <= aTime.time() <= end


def getReducedSegmentsRange(rangeList: list, theTime: dt, tz: str, aRange: str):
    """
    Returns a reduced list of seconds that when added to theTime fit within aRange
    May return an empty list [] if none actually fit
    """
    # Time range is in 24hour format (e.g. "1700-2030")

    # example:
    # -INPUTS-
    # aRange     = "0112-0117"
    # tz         = "Pacific/Auckland"
    # theTime    = dt.datetime(2020, 10, 14, 8, 10)
    # rangeList  = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160,
    #              170, 180, 190, 200, 210, 220, 230, 240, 250, 260, 270, 280, 290, 300, 310,
    #              320, 330, 340, 350, 360, 370, 380, 390, 400, 410, 420, 430, 440, 450, 460,
    #              470, 480, 490, 500, 510, 520, 530, 540, 550, 560, 570, 580, 590, 600, 610, 620]
    # 
    # -OUTPUT-
    # newRangeList=[120, 130, 140, 150, 160, 170, 180, 190, 200, 210, 220, 230, 240, 250, 260,
    #              270, 280, 290, 300, 310, 320, 330, 340, 350, 360, 370, 380, 390, 400, 410, 420]

    newRangeList = []
    targetTz = zoneinfo.ZoneInfo(tz)
    baseTime = theTime.astimezone(targetTz)
    # logger.debug(f"baseTime: {baseTime}")
    for x in rangeList:
        newTime = baseTime + dt.timedelta(seconds=x)
        # logger.debug(f"newTime: {newTime}")

        if isTimeInRange(newTime, aRange):
            newRangeList.append(x)

    # logger.debug(f"rangeList\n {rangeList}")
    # logger.debug(f"newRangeList\n {newRangeList}")
    return newRangeList


def closeShopSecsLeft(timeRanges: list, targetTime: dt, normalWorktime: int):
    # Returns the time left (in seconds) between targetTime and the appropriate working hours
    # end if it's less than the expected normalWorktime (in minutes)

    # example:
    # -INPUTS-
    # timeRanges = ["0800-1200", "1300-1730"]
    # targetTime = dt.datetime(2020, 10, 14, 11, 55)
    # normalWorktime = 10
    # 
    # -OUTPUT-
    # 300
    # i.e.: there are 300 seconds left between 1155 and 1200; could be time to close shop

    for aRange in timeRanges:
        # Determine what working range are we on
        if isTimeInRange(targetTime, aRange):
            # targtHr = int(targetTime.strftime('%H%M'))
            # logger.debug(f"******targtHr  {targtHr}")

            rangeTime = targetTime.replace(hour=int(aRange[5:7]), minute=int(aRange[7:9]))
            # rangeHr = int(rangeTime.strftime('%H%M'))
            # logger.debug(f"******rangeHr  {rangeHr}")

            timeLeft = rangeTime - targetTime
            # logger.debug(f"timeLeft = {timeLeft}")

            # Notice we are only interested in the range of time within
            # the normalWorktime, anything larger means we run normally
            # and don't need to close shop early
            if timeLeft < dt.timedelta(minutes=int(normalWorktime)):
                return int(timeLeft.total_seconds())

    # Raising instead of returning 0 or None just to save the caller processing logic
    raise ValueError
