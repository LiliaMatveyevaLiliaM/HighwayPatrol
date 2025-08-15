# External libraries import statements
import sys
import json
import time
import logging
import os.path
import pathlib
import unittest
from moto import mock_aws
from unittest.mock import mock_open, patch, MagicMock


# This is necessary in order for the tests to recognize local utilities
testdir = os.path.dirname(__file__)
srcdir = "../../../../../stacks/collector/src/python"
absolute = os.path.abspath(os.path.join(testdir, srcdir))
sys.path.insert(0, os.path.abspath(os.path.join(testdir, srcdir)))

# This application's import statements
import stillsGrabber 
import superGlblVars as GLOBALS
import orangeUtils.timeUtils as tu
import orangeUtils.awsUtils as awsUtils
import orangeUtils.networkUtils as netUtils


class TestStillsGrabber(unittest.TestCase):
    logger = logging.getLogger(__name__)
    logging.basicConfig(format = "%(asctime)s %(module)s %(levelname)s: %(message)s",
                datefmt = "%m/%d/%Y %I:%M:%S %p", level = logging.INFO)
    GLOBALS.netUtils = netUtils.NetworkUtils


    def setUp(self):
        self.mockAws = mock_aws()
        self.mockAws.start()

        # Set up mocked SQS client here so the actual queues are not used
        GLOBALS.sqsUtils = awsUtils.SQSutils(regionName="us-east-1")
        self.sqsClient = GLOBALS.sqsUtils.sqsClient
        self.queueUrl = self.sqsClient.create_queue(QueueName="test")["QueueUrl"]

        self.stillsAimpointNotFound = self._getAimpointDict("aimpoint-stills-404.json")
        self.stillsAimpointSuccess = self._getAimpointDict("aimpoint-stills.json")


    def tearDown(self):
        self.mockAws.stop()


    # Test that the status queue is sent a "failure" message
    # when collection fails due to ConnectionError
    @patch.dict(GLOBALS.config, {"statusQueue": "test"})
    @patch("utils.hPatrolUtils.itsTimeToBail")
    @patch.object(GLOBALS.netUtils, "downloadImage")
    def test_collectStillConnectionError(self, mocked_downloadImage, mocked_itsTimeToBail) -> None:
        self.logger.info("Running test_collectStillConnectionError...")
        mocked_downloadImage.side_effect = ConnectionError
        mocked_itsTimeToBail.return_value = True
        prefixBase = self._getPrefixBase(self.stillsAimpointNotFound)
        
        stillsGrabber._collectStill(prefixBase, self.stillsAimpointNotFound, None, self.stillsAimpointNotFound["collectionType"])
        
        res = self.sqsClient.receive_message(QueueUrl=self.queueUrl)
        msg = json.loads(res["Messages"][0]["Body"])
        self.assertEqual(len(res["Messages"]), 1)
        self.assertFalse(msg["isCollecting"])
        self.assertEqual(msg["aimpoint"], self.stillsAimpointNotFound)


    # Test successful collection for _collectStill; lots of patch's for external deps
    # so we don't do things like reach out to the web or read/write files
    @patch("stillsGrabber._pushHashInContent")            
    @patch("stillsGrabber._saveWasSuccessful")
    @patch("stillsGrabber._isSameImage")
    @patch.object(GLOBALS.netUtils, "downloadImage")
    @patch("os.rename")
    @patch("builtins.open", new_callable=mock_open, read_data="mock data")
    @patch("orangeUtils.utils.makeHashFileFromData")
    def test_collectStillSuccess(self, mocked_makeHash, mocked_file, mocked_rename, mocked_downloadImage, mocked_isSameImage, mocked_saveWasSuccessful, mocked_pushHashInContent) -> None:
        self.logger.info("Running test_collectStillSuccess...")
        mocked_response = MagicMock()
        mocked_response.status_code = 200
        mocked_downloadImage.return_value = mocked_response
        mocked_isSameImage.return_value = False
        aimpoint = self.stillsAimpointSuccess
        prefixBase = self._getPrefixBase(aimpoint)

        stillsGrabber._collectStill(prefixBase, aimpoint, None, aimpoint["collectionType"])
        
        mocked_rename.assert_called_once()
        mocked_makeHash.assert_called_once()
        mocked_pushHashInContent.assert_called_once()
        mocked_saveWasSuccessful.assert_called_once()
        self.assertTrue(mocked_saveWasSuccessful.return_value)


    def _getAimpointDict(self, fileName):
        currentPath = pathlib.Path(__file__).parent.resolve()
        with open(f"{currentPath}/resources/{fileName}") as jsonFile:
            aimpoint = json.load(jsonFile)
        return aimpoint


    def _getPrefixBase(self, ap):
        deviceID = ap["deviceID"]
        year, month, day = tu.returnYMD(time.time())
        resolvedTemplate = ap["bucketPrefixTemplate"].format(
            year=year, month=month, day=day, deviceID=deviceID
        )
        prefixBase = f"{GLOBALS.landingZone}/{resolvedTemplate}"
        return prefixBase
