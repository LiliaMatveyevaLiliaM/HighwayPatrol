# External libraries import statements
import json
import boto3
import logging
from random import sample


# This application's import statements
try:
    # These are for when running in an EC2
    import superGlblVars as GLOBALS
    from orangeUtils import utils as ut
    from utils import hPatrolUtils as hput

except ModuleNotFoundError as err:
    # These are for when running in a Lambda
    print(f"Loading module for lambda execution: {__name__}")
    from src.python.orangeUtils import utils as ut
    from src.python import superGlblVars as GLOBALS
    from src.python.utils import hPatrolUtils as hput


logger = logging.getLogger()


def _prepareInvoke(targetConfig: dict) -> dict:
    """Prep to start the invoke the lambda."""
    acctId = GLOBALS.myArn.split(":")[4]
    aRegion = sample(targetConfig["collRegions"], 1)[0]
    aRegion = ut.getRegionCode(aRegion)
    streamCollectorArn = (
        "arn:aws:lambda:"
        + aRegion
        + ":"
        + acctId
        + ":function:"
        + f"{GLOBALS.baseStackName}_StreamVideos"
    )
    return {"invokeArn": streamCollectorArn, "invokeRegion": aRegion}


def _invokeLambda(targetConfig: dict, lambdaConfig: dict) -> bool:
    """Invoke a streaming lambda with the targetConfig as payload"""
    logger.info(f"Creating boto3 lambda client in {lambdaConfig['invokeRegion']}")
    awsLambda = boto3.client(service_name="lambda", region_name=lambdaConfig["invokeRegion"])

    logger.info(
        f"Invoking Streaming Lambda '{lambdaConfig['invokeArn']}' "
        f"for '{hput.formatNameBase(targetConfig['filenameBase'], targetConfig['deviceID'])}'"
    )
    try:
        response = awsLambda.invoke(
            FunctionName=lambdaConfig["invokeArn"],
            InvocationType="Event",
            Payload=json.dumps(targetConfig)
        )
    except Exception as e:
        logger.critical(
            f"Caught Exception attempting to invoke streaming Lambda ::{e}"
        )
        return False

    if 200 <= response["ResponseMetadata"]["HTTPStatusCode"] < 300:
        pass
    else:
        logger.warning(f"Failed invoking the streaming lambda: {response}")
        return False
    return True


def invoke(targetConfig):
    """Streaming-Lambda invocation interface"""
    invokeConfig = _prepareInvoke(targetConfig)
    _invokeLambda(
        targetConfig=targetConfig,
        lambdaConfig=invokeConfig
    )
