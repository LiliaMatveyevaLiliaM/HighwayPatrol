# External libraries import statements
import time
import logging
import urllib3
import argparse
import traceback
import datetime as dt
from time import sleep


# This application's import statements
import superGlblVars as GLOBALS
from orangeUtils import auditUtils
from orangeUtils.auditUtils import AuditLogLevel


logger = logging.getLogger()


def _preFlightSetup():
    # Record startup time
    upSince = int(time.time())

    # Initialize logging
    _setupLogging()

    toPrint = f"Starting Logger"
    logger.info(f"=={'=' * len(toPrint)}==")
    logger.info(f"= {toPrint} =")

    return upSince


def _setupLogging() -> None:
    if logger.handlers:
        for handler in logger.handlers:
            logger.removeHandler(handler)
    logging.basicConfig(
        format='%(asctime)s - %(levelname)-8s - %(module)s:%(lineno)d - %(message)s',
        level=logging.DEBUG)

    # Disable the really detailed logging by other packages
    urllib3.disable_warnings()
    logging.getLogger('nose').setLevel(logging.ERROR)
    logging.getLogger('boto3').setLevel(logging.WARNING)
    logging.getLogger('chardet').setLevel(logging.ERROR)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('smart_open').setLevel(logging.ERROR)
    logging.getLogger('botocore').setLevel(logging.WARNING)
    logging.getLogger('connectionpool').setLevel(logging.ERROR)
    logging.getLogger('charset_normalizer').setLevel(logging.WARNING)


if __name__=="__main__":
    parser = argparse.ArgumentParser(
        description='ERROR LOGGER:\n'\
            'To log to the audit service, errors by bootstrap scripts',
    )
    parser.add_argument(
        "-reporter",
        required=True,
        help=(
            "Who is reporting this"
        ),
    )
    parser.add_argument(
        "-errorMsg",
        required=True,
        help=(
            "Error message to report"
        ),
    )
    parser.add_argument(
        "-country",
        required=False,
        help=(
            "Two-letter country code"
        ),
    )
    args = parser.parse_args()

    upSince = _preFlightSetup()
    # In this case, because this script is so short,
    # there is no difference between enterTime and leaveTime

    # Audit invocation
    nownow = int(time.time())
    try:
        print() # Just a blank line for spacing
        auditUtils.logBatchJob(
            msg=args.errorMsg,
            options=args,
            taskName=args.reporter,
            subtaskName="logError.py",
            stackName=GLOBALS.baseStackName,
            enterDatetime=dt.datetime.fromtimestamp(upSince),
            leaveDatetime=dt.datetime.fromtimestamp(nownow),
            systemLevel=AuditLogLevel.ERROR,
            # **collectionSummaryArgs
        )
        print() # Just a blank line for spacing
    except Exception as e:
        traceback.print_exc()

    # Give buffered log entries an opportunity to be delivered
    logger.info("Sleeping for 10s before exiting...")
    sleep(10)
