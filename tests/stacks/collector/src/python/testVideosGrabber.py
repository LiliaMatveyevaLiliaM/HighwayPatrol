# External libraries import statements
import sys
import time
import os.path
import logging
import unittest
from unittest.mock import patch
from random import randrange, getrandbits


# This is necessary in order for the tests to recognize local utilities
testdir = os.path.dirname(__file__)
srcdir = "../../../../../stacks/collector/src/python"
absolute = os.path.abspath(os.path.join(testdir, srcdir))
sys.path.insert(0, os.path.abspath(os.path.join(testdir, srcdir)))

# This application's import statements
import superGlblVars
import videosGrabber 


class TestVideosGrabber(unittest.TestCase):
    # Will use logger to verify output from videosGrabber
    logger = logging.getLogger(__name__)
    logging.basicConfig(format = "%(asctime)s %(module)s %(levelname)s: %(message)s",
                    datefmt = "%m/%d/%Y %I:%M:%S %p", level = logging.DEBUG)

    # Helper function returns true 90% of the time
    def helperWasSaveSuccessful(self,a,b,c,d,e):   
        self.logger.info(f"working on {d}")
        time.sleep(randrange(5)+1)
        self.logger.info(f"completed working on {d}")
        if (randrange(10)+1) % 10 == 0:
            self.logger.error(f"Ignored; segment previously captured ({e})")
            return False         
        return True

    
    def helperFixTsFilesOrder(self,list):
        return list

    # Mock all external dependencies within the uploadSegments function and return values
    @patch("videosGrabber._fixTsFilesOrder")
    @patch("videosGrabber._wasSaveSuccessful")
    @patch.dict(superGlblVars.config, {"defaultWrkBucket": "test", "proxy": None, "singleCollector": True, "concatenate": False})
    def test_uploadSegments(self, mocked_wasSaveSuccessful, mocked_fixTsFilesOrder):
        mocked_wasSaveSuccessful.side_effect = self.helperWasSaveSuccessful

        tsFiles = []
        for i in range(1,101):
            file = {"file": f"test_{i:03d}.ts", "hash": format(getrandbits(128), 'x')}
            tsFiles.append(file)

        mocked_fixTsFilesOrder.side_effect = self.helperFixTsFilesOrder
        finalList = videosGrabber._uploadSegments(superGlblVars.config,"test", tsFiles, "prefixBase")
        self.assertEqual(len(tsFiles),100)
        self.assertNotEqual(len(finalList),100)
        self.logger.info(finalList)
