# External libraries import statements
import sys
import json
import os.path
import logging
import unittest
import datetime as dt
from unittest.mock import patch

# This is necessary in order for the tests to recognize local utilities
testdir = os.path.dirname(__file__)
srcdir = "../../../../../stacks/drover/src/python"
common = "../../../../../stacks/common/src/python"
absolute = os.path.abspath(os.path.join(testdir, srcdir))
#absolute2 = os.path.abspath(os.path.join(testdir, common))
sys.path.insert(0, absolute)
#sys.path.append(absolute2)

# This application's import statements
import superGlblVars
import main as drover
import orangeUtils.awsUtils as awsUtils


superGlblVars.S3utils = awsUtils.S3utils
superGlblVars.sqsUtils = awsUtils.SQSutils


class TestMain(unittest.TestCase):
    # Will use logger to verify output from yt_dlp
    logger = logging.getLogger(__name__)
    logging.basicConfig(format = "%(asctime)s %(module)s %(levelname)s: %(message)s",
                    datefmt = "%m/%d/%Y %I:%M:%S %p", level = logging.DEBUG)

    timeGrid = {}
    # Build logic grid for calculating if transcoding is needed based on
    # the current time and the interval example
    #                  INTERVAL                             TIME
    #	1	2	3	4	5	6	10	12	15	20	30	60		Minute
    #---------------------------------------------------------------
    #	T	T	T	T	T	T	T	T	T	T	T	T		0
    #	T	F	F	F	F	F	F	F	F	F	F	F		1
    #	T	T	F	F	F	F	F	F	F	F	F	F		2
    #	T	F	T	F	F	F	F	F	F	F	F	F		3
    #	T	T	F	T	F	F	F	F	F	F	F	F		4
    #	T	F	F	F	T	F	F	F	F	F	F	F		5
    #	T	T	T	F	F	T	F	F	F	F	F	F		6
    #	T	F	F	F	F	F	F	F	F	F	F	F		7
    #	T	T	F	T	F	F	F	F	F	F	F	F		8
    #	T	F	T	F	F	F	F	F	F	F	F	F		9
    #	T	T	F	F	T	F	T	F	F	F	F	F		10
    #	T	F	F	F	F	F	F	F	F	F	F	F		11
    #	T	T	T	T	F	T	F	T	F	F	F	F		12
    #	T	F	F	F	F	F	F	F	F	F	F	F		13
    #	T	T	F	F	F	F	F	F	F	F	F	F		14
    #	T	F	T	F	T	F	F	F	T	F	F	F		15
    #   .   .   .   .   .   .   .   .   .   .   .   .       .
    #   .   .   .   .   .   .   .   .   .   .   .   .       .
    #   .   .   .   .   .   .   .   .   .   .   .   .       .
    #   T   F   F   F   F   F   F   F   F   F   F   F       59 

    @classmethod
    def setUpClass(self):
        # List of intervals in
        for interval in drover.DroverInterval:
            row = []
            for minute in range(0, 59):
                row.append(minute%interval==0)
            self.timeGrid[drover.DroverInterval(interval)] = row

    # Test the following
    #     if vMin < 15:
    #         vMinStart = 45
    #         vMinEnd = 60
    #     elif vMin < 30:
    #         vMinStart = 0
    #         vMinEnd = 15
    #     elif vMin < 45:
    #         vMinStart = 15
    #         vMinEnd = 30
    #     elif vMin < 60:
    #         vMinStart = 30
    #         vMinEnd = 45
    def test_getLastIntervalSpanFor15(self):
        #if vMin < 15:
        for i in range(0, 15):
            vMinStart, vMinEnd = drover._getLastIntervalSpan(i, drover.DroverInterval.FIFTEEN)
            self.assertEqual(vMinStart, 45)
            self.assertEqual(vMinEnd, 60)
        #elif vMin < 30:
        for i in range(15, 30):
            vMinStart, vMinEnd = drover._getLastIntervalSpan(i, drover.DroverInterval.FIFTEEN)
            self.assertEqual(vMinStart, 0)
            self.assertEqual(vMinEnd, 15)
        #elif vMin < 45:
        for i in range(30, 45):
            vMinStart, vMinEnd = drover._getLastIntervalSpan(i, drover.DroverInterval.FIFTEEN)
            self.assertEqual(vMinStart, 15)
            self.assertEqual(vMinEnd, 30)
        #elif vMin < 60:
        for i in range(45, 60):
            vMinStart, vMinEnd = drover._getLastIntervalSpan(i, drover.DroverInterval.FIFTEEN)
            self.assertEqual(vMinStart, 30)
            self.assertEqual(vMinEnd, 45)


    # Test the following
    #     if vMin < 20:
    #         vMinStart = 40
    #         vMinEnd = 60
    #     elif vMin < 40:
    #         vMinStart = 0
    #         vMinEnd = 20
    #     elif vMin < 60:
    #         vMinStart = 20
    #         vMinEnd = 40  
    def test_getLastIntervalSpanFor20(self):   
        #if vMin < 20:
        for i in range(0, 20):
            vMinStart, vMinEnd = drover._getLastIntervalSpan(i, drover.DroverInterval.TWENTY)
            self.assertEqual(vMinStart, 40)
            self.assertEqual(vMinEnd, 60)
        #elif vMin < 40:
        for i in range(20, 40):
            vMinStart, vMinEnd = drover._getLastIntervalSpan(i, drover.DroverInterval.TWENTY)
            self.assertEqual(vMinStart, 0)
            self.assertEqual(vMinEnd, 20) 
        #elif vMin < 60:
        for i in range(40, 60):
            vMinStart, vMinEnd = drover._getLastIntervalSpan(i, drover.DroverInterval.TWENTY)
            self.assertEqual(vMinStart, 20)
            self.assertEqual(vMinEnd, 40)    

    ###################################################
    #       GENERIC TESTING OF ALL POSIBILITIES       #
    ###################################################
    def test_getLastIntervalSpanForAll(self):
        # Iterate through all of the allowed time intervals
        for interval in  list(map(int, drover.DroverInterval)): 
            self.logger.info(f"testing the following time interval {interval}")
            iterations = int(60 / interval)
            loops = 1
            previousIntervalLogic = 0
            # Iterate through all the possible triggers in that interval. Example: if 15, then 15, 30, 45, 60/0
            for i in range(1, iterations + 1):
                if loops == 1:
                    vMinStart, vMinEnd = drover._getLastIntervalSpan(i*interval-1, interval)
                    intervalLogic = interval * loops
                    logic = f"\n if vMin < {intervalLogic} \n    vMinStart = {vMinStart}  \n      vMinEnd = {vMinEnd}"
                    self.logger.info(logic)
                    # Within the time range, assert the results are valid for the time window logic
                    for t in range(previousIntervalLogic, intervalLogic):
                        testvMinStart, testvMinEnd = drover._getLastIntervalSpan(t, interval)
                        self.assertEqual(testvMinStart, vMinStart)
                        self.assertEqual(testvMinEnd, vMinEnd)
                    previousIntervalLogic = intervalLogic

                else:
                    vMinStart, vMinEnd = drover._getLastIntervalSpan(i*interval-1, interval)
                    intervalLogic = interval * loops
                    logic = f"\n elif vMin < {intervalLogic} \n      vMinStart = {vMinStart}  \n        vMinEnd = {vMinEnd}"
                    self.logger.info(logic)
                    for t in range(previousIntervalLogic, intervalLogic):
                        testvMinStart, testvMinEnd = drover._getLastIntervalSpan(t, interval)
                        self.assertEqual(testvMinStart, vMinStart)
                        self.assertEqual(testvMinEnd, vMinEnd)
                    previousIntervalLogic = intervalLogic
                loops += 1

    # Test calculate min range functionality
    def test_calculateMinRange(self):
        datetimeStr = "02/01/24 09:00:00"
        datetimeObj = dt.datetime.strptime(datetimeStr, "%m/%d/%y %H:%M:%S")
        timeChange = dt.timedelta(minutes=1)

        for interval in list(map(int, drover.DroverInterval)):
            for i in range(0, 60):
                if i == 0:
                    datetimeObj = dt.datetime.strptime(datetimeStr, "%m/%d/%y %H:%M:%S")
                tgtDay, fromTimeInEpoch, clipLen = drover._calculateMinRange(datetimeObj, interval)
                self.logger.info(f"tgtDay ==> {tgtDay}")
                self.logger.info(f"fromTimeInEpoch ==> {fromTimeInEpoch}")
                self.logger.info(f"clipLen ==> {clipLen}")
                # If minute less than interval, the previous hour should be used
                if i < interval:
                    self.assertNotEqual(datetimeObj.hour, tgtDay.hour)
                # Otherwise, the hours should match
                else:    
                    self.assertEqual(datetimeObj.hour, tgtDay.hour)
                datetimeObj = datetimeObj+timeChange


    def test_compatibilityGetLast15mSpan(self):
        for t in range(0, 60):
            oldpMinStart, oldpMinEnd = drover._getLast15mSpan(t)
            newpMinStart, newpMinEnd  = drover._getLastIntervalSpan(t, drover.DroverInterval.FIFTEEN)
            self.assertEqual(oldpMinStart, newpMinStart)
            self.assertEqual(oldpMinEnd, newpMinEnd)

    # Test backward compatibility with previous functionality
    def test_compatibilityCalculate15minRange(self):
        datetimeStr = "02/01/24 09:00:00"
        datetimeObj = dt.datetime.strptime(datetimeStr, "%m/%d/%y %H:%M:%S")
        timeChange = dt.timedelta(minutes=1)
        # Loop through an entire day
        for i in range(0, 1440):
            oldtgtDay, fromTimeInEpoch, clipLen = drover._calculate15minRange(datetimeObj)
            newtgtDay, newfromTimeInEpoch, newclipLen = drover._calculateMinRange(datetimeObj, drover.DroverInterval.FIFTEEN)
            self.assertEqual(oldtgtDay, newtgtDay)
            self.assertEqual(fromTimeInEpoch, newfromTimeInEpoch)
            self.assertEqual(clipLen, newclipLen)
            datetimeObj = datetimeObj + timeChange


    def test_DroverInterval(self):
        self.assertEqual(drover.DroverInterval(1), drover.DroverInterval.ONE) 
        self.assertEqual(drover.DroverInterval(2), drover.DroverInterval.TWO)
        self.assertEqual(drover.DroverInterval(3), drover.DroverInterval.THREE)
        self.assertEqual(drover.DroverInterval(4), drover.DroverInterval.FOUR)
        self.assertEqual(drover.DroverInterval(5), drover.DroverInterval.FIVE)
        self.assertEqual(drover.DroverInterval(6), drover.DroverInterval.SIX)
        self.assertEqual(drover.DroverInterval(10), drover.DroverInterval.TEN)
        self.assertEqual(drover.DroverInterval(12), drover.DroverInterval.TWELVE)
        self.assertEqual(drover.DroverInterval(15), drover.DroverInterval.FIFTEEN)
        self.assertEqual(drover.DroverInterval(20), drover.DroverInterval.TWENTY)
        self.assertEqual(drover.DroverInterval(30), drover.DroverInterval.THIRTY)
        self.assertEqual(drover.DroverInterval(60), drover.DroverInterval.SIXTY)

    # Test transcoder trigger for 15 intervals
    # Mock config to return defaultWrkBucket equal to test and proxy to None
    # Mock sqsUtils object function sendMessage (mock objects are required to be included in test parameters)
    # Mock S3utils object function readFileContent (mock objects are required to be included in test parameters)
    @patch.dict(superGlblVars.config, {"defaultWrkBucket": "test", "proxy": None})
    @patch.object(superGlblVars.sqsUtils, "sendMessage")
    @patch.object(superGlblVars.S3utils, "readFileContent")
    def test_sendTaskings(self, test_readFileContent, test_sendMessage):
        theTask = drover.DroverTask.TRANSCODE
        fileList = ["Test"]

        # Sample aimpoint
        aimpoint = {
            "deviceID": "test",
            "enabled": True,
            "collRegions": ["test"],
            "collectionType": "test",
            "accessUrl": "http://test.com",
            "pollFrequency": 28,
            "waitFraction": 0.16,
            "concatenate": False,
            "transcodeExt": "mp4",
            "filenameBase": "{deviceID}",
            "finalFileSuffix": "_{year}-{month}-{day}-{hour}-{mins}",
            "bucketPrefixTemplate": "test/{year}/{month}/{day}",
            "deliveryKey": "post",
            "headers": {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/88.0.4324.150 Safari/537.36",
                "Accept": "*/*",
                "DNT": "1",
                "Connection": "keep-alive",
                "Pragma": "no-cache",
                "Cache-Control": "no-cache"
            }
        }

        # Set mock return to be used in transcoding task
        test_readFileContent.return_value = json.dumps(aimpoint)

        # Test that no transcoding is taking place
        datetimeStr = "02/01/24 09:01:00"
        datetimeObj = dt.datetime.strptime(datetimeStr, "%m/%d/%y %H:%M:%S")
        with self.assertLogs("root", level="DEBUG") as lc:
            drover._sendTaskings(theTask, fileList, datetimeObj)
            self.assertTrue("time range from :30 to :45" in lc[1][0] and "from '1706794200' to '1706795100'" in lc[1][1] )

        # Test that transcoding is taking place at the hour mark  
        datetimeStr = "02/01/24 09:00:00"
        datetimeObj = dt.datetime.strptime(datetimeStr, "%m/%d/%y %H:%M:%S")
        with self.assertLogs("root", level="DEBUG") as lc:
            drover._sendTaskings(theTask, fileList, datetimeObj)
            # Make sure clip length is at 15 minutes and 20 seconds
            self.assertTrue('"clipLengthSecs": 920,' in lc[1][5])
            # Make sure clip start is at 02/01/24 08:29:50
            self.assertTrue('"clipStart": "1706794190",' in lc[1][5])

        # Test that no transcoding is taking place
        datetimeStr = "02/01/24 09:14:40"
        datetimeObj = dt.datetime.strptime(datetimeStr, "%m/%d/%y %H:%M:%S")
        drover._sendTaskings(theTask, fileList, datetimeObj)
        with self.assertLogs("root", level="DEBUG") as lc:
            drover._sendTaskings(theTask,fileList,datetimeObj)
            self.assertTrue("time range from :30 to :45" in lc[1][0] and "from '1706794200' to '1706795100'" in lc[1][1] )

        # Test transcoding is taking place at the 15 minute mark
        datetimeStr = "02/01/24 09:15:00"
        datetimeObj = dt.datetime.strptime(datetimeStr, "%m/%d/%y %H:%M:%S")
        with self.assertLogs("root", level="DEBUG") as lc:
            drover._sendTaskings(theTask,fileList,datetimeObj)
            # Make sure clip length is at 15 minutes and 20 seconds
            self.assertTrue('"clipLengthSecs": 920,' in lc[1][5])
            # Make sure clip start is at 02/01/24 08:44:50 
            self.assertTrue('"clipStart": "1706795090",' in lc[1][5])

    # Test transcoder trigger for 10 minute intervals
    # Mock config to return defaultWrkBucket equal to test and proxy to None
    # Mock sqsUtils object function sendMessage (mock objects are required to be included in test parameters)
    # Mock S3utils object function readFileContent (mock objects are required to be included in test parameters)
    @patch.dict(superGlblVars.config, {"defaultWrkBucket": "test", "proxy": None})    
    @patch.object(superGlblVars.sqsUtils, "sendMessage")
    @patch.object(superGlblVars.S3utils, "readFileContent")
    def test_sendTaskingsInterval(self,test_readFileContent, test_sendMessage):
        self.logger.info("testing the following time interval ")
        theTask = drover.DroverTask.TRANSCODE
        fileList = ["Test"]

        # Build sample return from yt_dlp.YoutubeDL.extract_info used in youtubeInterface
        aimpoint = {
            "deviceID": "test",
            "enabled": True,
            "collRegions": ["test"],
            "collectionType": "test",
            "accessUrl": "http://test.com",
            "pollFrequency": 28,
            "waitFraction": 0.16,
            "concatenate": False,
            "transcodeExt": "mp4",
            "filenameBase": "{deviceID}",
            "finalFileSuffix": "_{year}-{month}-{day}-{hour}-{mins}",
            "bucketPrefixTemplate": "test/{year}/{month}/{day}",
            "deliveryKey": "post",
            "transcoderInterval": 10,
            "headers": {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/88.0.4324.150 Safari/537.36",
                "Accept": "*/*",
                "DNT": "1",
                "Connection": "keep-alive",
                "Pragma": "no-cache",
                "Cache-Control": "no-cache"
            }
        }

        # Set mock return to be used in transcoding task
        test_readFileContent.return_value = json.dumps(aimpoint)

        # Test that no transcoding is taking place
        datetimeStr = "02/01/24 09:01:00"
        datetimeObj = dt.datetime.strptime(datetimeStr, "%m/%d/%y %H:%M:%S")
        with self.assertLogs("root", level="DEBUG") as lc:
            drover._sendTaskings(theTask, fileList, datetimeObj)
            self.assertFalse('"clipLengthSecs": 620,' in lc)

        # Test that transcoding is taking place
        datetimeStr = "02/01/24 09:00:00"
        datetimeObj = dt.datetime.strptime(datetimeStr, "%m/%d/%y %H:%M:%S")
        with self.assertLogs("root", level="DEBUG") as lc:
            drover._sendTaskings(theTask, fileList, datetimeObj)
            # Make sure clip length is at 10 minutes and 20 seconds
            self.assertTrue('"clipLengthSecs": 620,' in lc[1][7])
            # Make sure clip start is at 02/01/24 08:39:50 
            self.assertTrue('"clipStart": "1706794790",' in lc[1][7])

        # Test that no transcoding is taking place
        datetimeStr = "02/01/24 09:15:00"
        datetimeObj = dt.datetime.strptime(datetimeStr, "%m/%d/%y %H:%M:%S")
        with self.assertLogs("root", level="DEBUG") as lc:
            drover._sendTaskings(theTask, fileList, datetimeObj)
            self.assertFalse('"clipLengthSecs": 620,' in lc)

        # Test that transcoding is taking place
        datetimeStr = "02/01/24 09:10:00"
        datetimeObj = dt.datetime.strptime(datetimeStr, "%m/%d/%y %H:%M:%S")
        with self.assertLogs("root", level="DEBUG") as lc:
            drover._sendTaskings(theTask, fileList, datetimeObj)
            # Make sure clip length is at 10 minutes and 20 seconds
            self.assertTrue('"clipLengthSecs": 620,' in lc[1][7])
            # Make sure clip start is at 02/01/24 08:49:50 
            self.assertTrue('"clipStart": "1706795390",' in lc[1][7])
