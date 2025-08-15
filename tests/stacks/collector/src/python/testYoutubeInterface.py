# External libraries import statements
import sys
import os.path
import logging
import unittest
from os.path import exists
from yt_dlp import YoutubeDL
from unittest.mock import patch


# This is necessary in order for the tests to recognize local utilities
testdir = os.path.dirname(__file__)
srcdir = '../../../../../stacks/collector/src/python'
absolute = os.path.abspath(os.path.join(testdir, srcdir))
sys.path.insert(0, os.path.abspath(os.path.join(testdir, srcdir)))

# This application's import statements
import superGlblVars
import youtubeInterface
from exceptions import HPatrolError


class TestYoutubeInterface(unittest.TestCase):
    # Will use logger to verify output from yt_dlp
    logger = logging.getLogger(__name__)
    logging.basicConfig(format = "%(asctime)s %(module)s %(levelname)s: %(message)s",
                    datefmt = "%m/%d/%Y %I:%M:%S %p", level = logging.INFO)
    # Set handleTube variables
    prefixBase = "/tmp"
    env = {"deviceID": "bunny", "filenameBase": "bunny", "accessUrl": "https://test"}

    # Mock all external dependencies and return values 
    @patch.dict(superGlblVars.config, {"defaultWrkBucket": "test", "proxy": None})
    @patch.object(youtubeInterface, "_sendToBucket")
    @patch.object(YoutubeDL, "download")
    @patch.object(YoutubeDL, "extract_info")
    def test_handleTube(self, test_extract_info, test_download, test__sendToBucket):

        # Build sample return from yt_dlp.YoutubeDL.extract_info used in youtubeInterface
        formats = {"formats": [
            {"asr": None, "filesize": 701191, "format_id": "247", "format_note": "720p",
             "source_preference": -1, "fps": 25, 
             "resolution": "audio only",
             "audio_channels": None, "height": 720, "quality": 8.0
             },
             {"asr": None, "filesize": 701191, "format_id": "248", "format_note": "720p",
             "source_preference": -1, "fps": 25, 
             "resolution": "video only",
             "ext": "mp4",
             "audio_channels": None, "height": 720, "quality": 8.0
             }

             ],"title":"test"}
        # Set mock return to be used in youtubeInterface
        test_extract_info.return_value = formats


        # Run mock test to view logging
        youtubeInterface.handleTube(self.prefixBase,self.env)
        # Running test to ensure elements have been logged properly based on mock yt-dlp metadata
        with self.assertLogs("root", level="INFO") as lc:
            youtubeInterface.handleTube(self.prefixBase,self.env)
            self.assertTrue("youtubeFile" in lc[1][0])
            # testing for video only
            self.assertTrue("video formats" in lc[1][1])
            self.assertTrue(formats["formats"][1]["resolution"] in lc[1][1])
            # testing for audio only
            self.assertTrue("audio formats" in lc[1][2])
            self.assertTrue(formats["formats"][0]["resolution"] in lc[1][2])
            # testing title of video
            self.assertTrue("Video title" in lc[1][3])
            self.assertTrue(formats["title"] in lc[1][3])
            # testing that video was selected
            self.assertTrue("Stream selected" in lc[1][4])
            self.assertTrue(formats["formats"][1]["resolution"] in lc[1][4])

    def test_handleTubeEnvException(self):
        with self.assertRaises(HPatrolError):
            youtubeInterface.handleTube(self.prefixBase,{})
