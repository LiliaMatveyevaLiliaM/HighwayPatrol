"""
"""

# External libraries import statements
import os
import json
import datetime as dt
from enum import IntEnum


# This application's import statements
from . import networkUtils


class AuditLogLevel(IntEnum):
    """
    Each operation should result in a single call to log its
    outcome. Such calls should be made with log levels representing
    the status of both the collection system (system status) and
    the status of the target system (data status).
    """
    INFO = 20
    WARN = 30
    ERROR = 40
    CRITICAL = 50


def __makeLogEntry(
    eventSubtype: str,
    stackName: str,
    taskName: str,
    subtaskName: str,
    enterDatetime: dt.datetime,
    leaveDatetime: dt.datetime,
    systemLevel: AuditLogLevel = None,
    systemCode: int = None,
    dataLevel: AuditLogLevel = None,
    dataCode: int = None,
    msg: str = None,
    **collectionSummaryArgs) -> dict:

    entry = {}
    entry['timestamp'] = enterDatetime.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    entry['eventType'] = 'audit'
    entry['eventSubtype'] = eventSubtype
    if systemLevel:
        entry['systemLevel'] = systemLevel.value
    if systemCode:
        entry['systemCode'] = systemCode
    if dataLevel:
        entry['dataLevel'] = dataLevel.value
    if dataCode:
        entry['dataCode'] = dataCode
    if msg:
        entry['msg'] = msg

    # Collector info
    collectorInfo = {}
    collectorInfo['taskName'] = taskName
    collectorInfo['stackName'] = stackName
    collectorInfo['subtaskName'] = subtaskName
    entry['collectorInfo'] = collectorInfo

    # Lambda operation summary details
    operationSummary = {}
    operationSummary['enterDatetime'] = entry['timestamp']
    operationSummary['leaveDatetime'] = leaveDatetime.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    elapsedTimeDelta = leaveDatetime - enterDatetime
    elapsedTimeMillis = elapsedTimeDelta.total_seconds() + (elapsedTimeDelta.microseconds / 1000)
    operationSummary['elapsedTimeMillis'] = int(elapsedTimeMillis)
    entry['operationSummary'] = operationSummary

    # Collection summary
    collectionSummary = {}
    for key, value in collectionSummaryArgs.items():
        collectionSummary[key] = value
    entry['collectionSummary'] = collectionSummary

    return entry


def logFromLambda(
    ip: str,
    arn: str,
    event: dict,
    lambdaContext,
    taskName: str,
    stackName: str,
    subtaskName: str,
    enterDatetime: dt.datetime,
    leaveDatetime: dt.datetime,
    msg: str = None,
    dataCode: int = None,
    systemCode: int = None,
    dataLevel: AuditLogLevel = None,
    systemLevel: AuditLogLevel = None,
    **collectionSummaryArgs) -> dict:

    """
    Constructs an audit log entry representing a Lambda invocation.
    The log entry is written to the Lambda's CloudWatch log stream.

    ### Parameters
    event : dict
        [REQUIRED] The 'event' parameter with which invocation occured
    lambdaContext
        [REQUIRED] The 'context' parameter with which invocation occured
    ip : str
         [REQUIRED] Executing system's IP Address
    arn : str
         [REQUIRED] AWS's ARN identifier of the executing instance
    stackName : str
        [REQUIRED] Name of the Lambda's CDK stack
    taskName : str
        [REQUIRED] Name of collection task
    subtaskName : str
        [REQUIRED] Name of collection sub-task
    enterDatetime : datetime
        [REQUIRED] Timestamp at which invocation occured
    leaveDatetime : datetime
        [REQUIRED] Timestamp at which execution completed
    systemLevel : AuditLogLevel
        [REQUIRED] Indicates the severity of the log entry (default: AuditLogLevel.INFO)
    systemCode : int
        Indicates the specific system error code
    dataLevel : AuditLogLevel
         Indicates the severity of the log entry
    dataCode : int
         Indicates the specific data error code
    msg : str
         Optional message to be included with the log entry
    **collectionSummaryArgs
         Collection summary metrics, as appropriate. For example:
             numChangesDetected: int
             numPageVisits: int
             numSearchResults: int
             numDocsPulled: int

    ### Returns
    dict
        The constructed audit log entry
    """

    entry = __makeLogEntry(
        eventSubtype='lambda',
        stackName=stackName,
        taskName=taskName,
        subtaskName=subtaskName,
        enterDatetime=enterDatetime,
        leaveDatetime=leaveDatetime,
        systemLevel=systemLevel,
        systemCode=systemCode,
        dataLevel=dataLevel,
        dataCode=dataCode,
        msg=msg,
        **collectionSummaryArgs)

    # AWS context
    awsContext = {}
    awsContext['region'] = arn.split(":")[3]
    awsContext['accountId'] = arn.split(":")[4]
    entry['awsContext'] = awsContext

    # Lambda context
    context = {}
    if lambdaContext:
        context['function_name'] = lambdaContext.function_name
        context['log_group_name'] = lambdaContext.log_group_name
        context['aws_request_id'] = lambdaContext.aws_request_id
        context['log_stream_name'] = lambdaContext.log_stream_name
        context['function_version'] = lambdaContext.function_version
        context['memory_limit_in_mb'] = lambdaContext.memory_limit_in_mb
        context['invoked_function_arn'] = lambdaContext.invoked_function_arn
    entry['lambdaContext'] = context

    # IP address
    entry['ipAddress'] = ip

    # Event
    entry['lambdaEvent'] = event

    print(json.dumps(entry))

    return entry


def logBatchJob(
    msg: str,
    taskName: str,
    stackName: str,
    subtaskName: str,
    enterDatetime: dt.datetime,
    leaveDatetime: dt.datetime,
    dataCode: int = None,
    options: list = None,
    systemCode: int = None,
    dataLevel: AuditLogLevel = None,
    systemLevel: AuditLogLevel = None,
    **collectionSummaryArgs) -> dict:       

    """
    Constructs an audit log entry representing an invocation from a
    batch job using the information provided. If configured properly the
    log stream entry is delivered to a destination which aggregates and
    persists such entries and makes them available for subsequent reporting.

    ### Parameters
    1. stackName : str
        - [REQUIRED] Name of the Lambda's CDK stack
    2. taskName : str
        - [REQUIRED] Name of collection task
    3. subtaskName : str
        - [REQUIRED] Name of collection sub-task
    4. enterDatetime : datetime
        - [REQUIRED] Timestamp at which Lambda was invoked
    5. leaveDatetime : datetime
        - [REQUIRED] Timestamp at which Lambda execution completed
    7. systemLevel : AuditLogLevel
        - [REQUIRED] Indicates the severity of the log entry (default: AuditLogLevel.INFO)
    8. systemCode : int
        - Indicates the specific system error code
    9. dataLevel : AuditLogLevel
        - Indicates the severity of the log entry
    10. dataCode : int
        - Indicates the specific data error code
    11. options : list
         - List of option value pairs passed to the program
    12. msg : str
         - Optional message to be included with the log entry
    13. **collectionSummaryArgs
         - Collection summary metrics, as appropriate. For example:
         -     numChangesDetected: int
         -     numPageVisits: int
         -     numSearchResults: int
         -     numDocsPulled: int

    ### Returns
    - dict
        - The constructed audit log entry
    """

    entry = __makeLogEntry(
        eventSubtype="batch",
        stackName=stackName,
        taskName=taskName,
        subtaskName=subtaskName,
        enterDatetime=enterDatetime,
        leaveDatetime=leaveDatetime,
        systemLevel=systemLevel,
        systemCode=systemCode,
        dataLevel=dataLevel,
        dataCode=dataCode,
        msg=msg,
        collectionSummaryArgs=collectionSummaryArgs)

    # AWS context
    awsContext = {}
    envDict = dict(os.environ)
    for k in envDict:
        if k.startswith("AWS_"):
            awsContext[k] = envDict[k]
    entry['awsContext'] = awsContext

    # IP address
    ipAddr = networkUtils.getPublicIp()
    if ipAddr is not None:
        entry['ipAddress'] = ipAddr
    else:
        print("Error retrieving public IP address")

    # Options
    # opts = {}
    # if options:
    #     for opt, arg in options:
    #         opts[opt] = arg
    opts = vars(options)
    entry['options'] = opts

    print(json.dumps(entry))

    return entry
