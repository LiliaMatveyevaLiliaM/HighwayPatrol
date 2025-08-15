#
# This module holds all the "super-global" shtuff
# Very similar to the settings.py file, in terms of accesibility of values to all the code
# but this one is not to be edited dependent on different configurations

# Make the configuration in systemSettings.py available to all
config = {}

# Specify whether we are running in PRODUCTION or not
# This also affects how many pages we go through on the target site; if we're not on
# production, we don't go through all when paginating
onProd = False

# Self-awareness for audit logs, and some CDK stack settings
projectName = "hPatrol"

# Each audit-writing element in the system (e.g. lambda) should set these for themselves
# To help in identification and reporting of each part
taskName= None
subtaskName= None

# When in test, system won't reach out on the net, but will instead
# use the files in the testResources directory
useTestData = False

# AWS access objects
S3utils = None

# The IP address used when executing
perceivedIP = None

# This system's software version
myVersion = None

# AWS system's ARN where we're running from (lambda or EC2)
myArn = None

# The http session object to enable re-use between separate calls
netUtils = None

# Number of parallel threads to use when uploading segments to S3
upThreads = 4

# Default FFMPEG deduplication mechanism
ffmpegDedup = None

# Range of time in seconds that the enabler lambda analyzes for successful collections
enablerLookBack = 300      # 300s == 5m

# Range of time in seconds that the disabler lambda analyzes for failed collections
disablerLookBack = 1800      # 1800s == 30m

# Frequency in hours to process aimpoints set to "monitor" status
monitorFrequency = 12

# AWS S3 bucket key-prefixes; some are used as inputs, some as outputs
selectTrgts = 'selections'  # individual devices selected; used for the aimpoint producers
targetFiles = 'aimpoints'   # config files indicating what we're going after
monitorTrgt = 'monitored'   # config files for down devices that are periodically monitored
landingZone = 'lz'          # start point for videos; collected videos go here
stillImages = 'stillsLz'    # start point for still images; collected stills go here
deliveryKey = 'up'          # default downstream delivery prefix; can be overriden by aimpoint
audiosPlace = 'audios'      # default downstream audio delivery prefix; overriden by aimpoint
s3Hashfiles = 'hashfiles'   # md5 hash files of collected data for deduplication
mtdtReports = '0_Metadata'  # available devices' historical data and reports
hpResources = 'resources'   # resources to aid in system execution (e.g. mitmproxy-ca.pem file)
aimpointSts = 'aimpointStatus' # collection status for all aimpoints (success/fail)

# PEM Certificate Authority filename for the MITM proxy for VPNs
# File is created on first run of MITM; then it can be reused every time
# To be placed in the system's S3 resources folder
proxyCaFile = "mitmproxy-ca.pem"

# When running as a lambda, the received 'context'
lambdaContext = None

# Our stack name for the Dispatcher to call the correct Collector
# This is used internally and during deployment as a CDK parameter
baseStackName = "highwayPatrol"
