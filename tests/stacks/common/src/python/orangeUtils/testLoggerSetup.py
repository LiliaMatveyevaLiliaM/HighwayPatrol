# External libraries import statements
import unittest
import unittest.mock
from os.path import exists


# This application's import statements
from stacks.common.src.python.orangeUtils.loggerSetup import *



class TestLoggerSetup(unittest.TestCase):
    _testLog = "/tmp/highway/test.log"

    @classmethod
    def setUpClass(self):
        if exists(self._testLog):
            os.remove(self._testLog)

    @classmethod
    def tearDownClass(self):
        if exists(self._testLog):
            os.remove(self._testLog)

    # Init logger and log some test statements
    def runLog(self):
        logger = logging.getLogger()
        logger.info("Testing logger")
        logger.debug("Testing debug statement")
    
    # Configure logging outside of lambda
    def test_setupLoggingRegular(self):        
        setupLogging(self._testLog, "test")
        self.runLog()
        # assert that there should be a log file in the filesystem
        self.assertTrue(exists(self._testLog))

    # Configure logging while mocking environments for lambda
    @unittest.mock.patch.dict(os.environ,{"LAMBDA_TASK_ROOT":"test"})
    def test_setupLoggingLambda(self):                        
        setupLogging(self._testLog, "test")
        self.runLog()
        # Assert that there should NOT be a log file in the filesystem
        self.assertFalse(exists(self._testLog))
