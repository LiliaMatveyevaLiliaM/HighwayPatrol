# External libraries import statements
import os
import sys
import time
import logging
import logging.config


# Logging levels are: CRITICAL, ERROR, WARNING, INFO, DEBUG, NOTSET
# DEBUG 	Detailed information, typically of interest only when diagnosing problems.
# INFO 	    Confirmation that things are working as expected.
# WARNING 	An indication that something unexpected happened, or indicative of some problem in the near future
#           (e.g. ‘disk space low’). The software is still working as expected.
# ERROR 	Due to a more serious problem, the software has not been able to perform some function.
# CRITICAL 	A serious error, indicating that the program itself may be unable to continue running.


def setupLogging(logFilePath, loggerName):
    def logUncaughtExceptions(excType, excValue, excTraceback):
        logger.critical("Unexpected exception occurred: ",
                        exc_info=(excType, excValue, excTraceback))


    thisDir = os.path.dirname(os.path.realpath(__file__))
    # print(f'Curr dir: {os.getcwd()}')
    # print(f'This dir: {thisDir}')

    logger = logging.getLogger(loggerName)

    # Based on https://docs.aws.amazon.com/lambda/latest/dg/configuration-envvars.html
    # We can can used reserved environment variables to determine if running inside of an AWS lambda
    # In this case we will use LAMBDA_TASK_ROOT 
    if os.environ.get("LAMBDA_TASK_ROOT") is not None:
        # Configure console only logging
        logSettingsFile = os.path.join(thisDir, "logging-lambda.ini")
    else:
        # Configure console and file logging
        logSettingsFile = os.path.join(thisDir, "logging.ini")    
        logFileDir = os.path.dirname(logFilePath)
        if not os.path.exists(logFileDir):
            os.makedirs(logFileDir)

    if not os.path.isfile(logSettingsFile):
        print(f"ERROR: Logging ini file not found: {logSettingsFile}")
        exit(1)

    logging.config.fileConfig(logSettingsFile, defaults={'logfilename': logFilePath},
                              disable_existing_loggers=False)
    # Trying to mentally calculate local time from UTC when reading logs is a royal pain
    logging.Formatter.converter = time.localtime

    # To handle any 'unhandled' exceptions
    sys.excepthook = logUncaughtExceptions

    # Disable the really detailed logging by other packages
    logging.getLogger('boto3').setLevel(logging.WARNING)
    logging.getLogger('yt_dlp').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('pyexcel').setLevel(logging.WARNING)
    logging.getLogger('botocore').setLevel(logging.WARNING)
    logging.getLogger('s3transfer').setLevel(logging.WARNING)
    logging.getLogger('charset_normalizer').setLevel(logging.WARNING)
    logging.getLogger('pyexcel_io.plugins.NewIOManager').setLevel(logging.WARNING)
