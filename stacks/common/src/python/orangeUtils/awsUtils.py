"""
"""

# External libraries import statements
import os
import re
import time
import json
import boto3
import base64
import fnmatch
import logging
import getpass
import urllib3
import requests
import warnings
import configparser
import keyring.backends.SecretService as SS
from botocore.exceptions import ClientError
from botocore.exceptions import NoRegionError
from botocore.exceptions import ParamValidationError
from botocore.exceptions import CredentialRetrievalError


logger = logging.getLogger()

# Enable in case we want to temporarily see libraries' output
# logging.getLogger("boto3").setLevel(logging.WARNING)
# logging.getLogger("botocore").setLevel(logging.WARNING)


class EventUtils:
    """
    AWS EventBridge interface class
    """
    def __init__(self):
        logger.debug("Initializing EventBridge client ")

        try:
            self.ebClient = boto3.client('events')
        except (ClientError, NoRegionError, KeyError) as e:
            logger.critical("Error initializing EventBridge: {}".format(e))
            raise ValueError

        logger.debug("EventBridge client inited ")

    def enableEvent(self, eventName):
        logger.info(f"Enabling event '{eventName}'")
        response = self.ebClient.enable_rule(Name=eventName)

        # response = self.ebClient.describe_rule(Name=eventName)
        # logger.debug(f"******************:{response}")

    def disableEvent(self, eventName):
        logger.info(f"Disabling event '{eventName}'")
        response = self.ebClient.disable_rule(Name=eventName)

        # response = self.ebClient.describe_rule(Name=eventName)
        # logger.debug(f"******************:{response}")


class SQSutils:
    def __init__(self, profile=None, regionName=None):
        logger.debug("Initializing SQS client ")

        try:
            if profile:
                # Attempt connection using AWS profile
                boto3.setup_default_session(profile_name=profile)   # Works when on EC2 with profile set
            elif regionName:
                boto3.setup_default_session(region_name=regionName)
            else:
                # Attempt connection relying on system role permissions
                boto3.setup_default_session()                       # Works when on EC2 with role or Lambda

            self.sqsClient = boto3.client('sqs')
        except ClientError as e:
            logger.critical(e)
            logger.critical("Error accessing SQS")
            raise ValueError

        logger.debug("SQS client inited ")


    def sendMessage(self, theQueue, message, delay=0):
        try:
            resp = self.sqsClient.send_message(
                QueueUrl=theQueue,
                DelaySeconds=delay,
                MessageBody=json.dumps(message)
            )
        except Exception as e:
            logger.error(f"EXCEPTION CAUGHT: {e} Using queue '{theQueue}")
            return None

        if 200 <= resp['ResponseMetadata']['HTTPStatusCode'] < 300:
            logger.debug('Queue send status: ' + str(resp['ResponseMetadata']['HTTPStatusCode']))
        else:
            logger.warning('Failed to send status: ' + str(resp['ResponseMetadata']['HTTPStatusCode']))

        return resp


class SecretsUtils:
    def __init__(self, profile=None):
        logger.debug("Initializing Secrets Manager")

        try:
            if profile:
                # Attempt connection using AWS profile
                boto3.setup_default_session(profile_name=profile)   # Works when on EC2 with profile set
            else:
                # Attempt connection relying on system role permissions
                boto3.setup_default_session()                       # Works when on EC2 with role or Lambda

            self.smClient = boto3.client("secretsmanager")
        except ClientError as e:
            logger.critical(e)
            logger.critical("Error accessing Secrets Manager")
            raise ValueError

        logger.debug("Secrets Manager client inited")


    def getSecret(self, secretId):
        try:
            resp = self.smClient.get_secret_value(SecretId=secretId)

        except Exception as e:
            logger.error(f"EXCEPTION CAUGHT: {e} Using ID '{secretId}'")
            return None

        return json.loads(resp["SecretString"])


class SNSutils:
    """
    AWS SNS Topics interface class
    """
    def __init__(self, region):
        logger.debug("Initializing SNS client ")
        try:
            self.snsClient = boto3.client('sns', region_name=region)
        except (ClientError, NoRegionError, KeyError) as e:
            logger.critical("Error initializing SNS: {}".format(e))
            raise ValueError

        logger.debug("SNS client inited ")


    def sendData(self, theData, theTopic):
        """
        Send data to an SNS Topic
        """

        if theTopic is None:
            logger.debug("SNS not configured; will not use")
            return True

        jsonString = json.dumps(theData, ensure_ascii=False)
        try:
            logger.debug("Publishing to SNS")
            response = self.snsClient.publish(
                TopicArn=theTopic,
                Message=jsonString
            )
            # logger.debug("SNS publish response: {}".format(response))
            return True

        except (ClientError, ParamValidationError) as exception:
            logger.error("Exception Caught: {}".format(exception))
            return False


class S3utils:
    def __init__(self, accessKey, secretKey, bucketName, profile=None):
        logger.debug("Initializing S3 client ")
        try:
            if profile:
                logger.debug("Attempting connection using AWS profile")
                boto3.setup_default_session(profile_name=profile)   # Works when on EC2 with profile set
            else:
                logger.debug("Attempting connection relying on system role permissions")
                boto3.setup_default_session()                       # Works when on EC2 with role or Lambda

            if accessKey is None or secretKey is None:
                self.s3Client = boto3.client('s3', use_ssl=False)
            else:
                logger.debug("Attempting connection relying on system role permissions")
                self.s3Client = boto3.client('s3', aws_access_key_id=accessKey, aws_secret_access_key=secretKey, use_ssl=False)

            self.s3Client.head_bucket(Bucket=bucketName)
        except (ClientError, CredentialRetrievalError) as e:
            logger.critical("Error accessing S3; check credentials, tokens, and access permissions")
            logger.critical(e)
            logger.critical(f"Attemping to access '{bucketName}' bucket")
            raise ValueError

        logger.debug("S3 client inited ")


    def getFileMetadata(self, bucketName, s3Key, mtdtKey):
        # Get any requested metadata such as the ETAG, LastModified, etc.

        try:
            objDict = self.s3Client.head_object(Bucket=bucketName, Key=s3Key)
        except ClientError as e:
            logger.critical(e)
            logger.critical(f"Looking in '{bucketName}' for key '{s3Key}'")
            raise RuntimeError from None
        # logger.debug(f"objDict: {objDict}")

        try:
            mtdt = objDict[mtdtKey]
        except KeyError as err:
            raise ValueError(err) from None
        # logger.debug(f"{mtdtKey}={mtdt}")

        return mtdt


    def getEtagFileHash(self, bucketName, s3Key):
        logger.warning("CODECHANGE: *****************************************")
        logger.warning("CODECHANGE: *****************************************")
        logger.warning("CODECHANGE:  use the more flexible getFileMetadata() ")
        logger.warning("CODECHANGE: *****************************************")
        logger.warning("CODECHANGE: *****************************************")
        raise DeprecationWarning


    def getFilesAsList(self, bucketName, bucketPrefix, limit=None, justFilename=False, closedSearch=True, unique=False):
        logger.warning("CODECHANGE: *****************************************")
        logger.warning("CODECHANGE: *****************************************")
        logger.warning("CODECHANGE: use either getFilesAsStrList() or getFilesAsObjList()")
        logger.warning("CODECHANGE: *****************************************")
        logger.warning("CODECHANGE: *****************************************")
        raise DeprecationWarning


    def getFilesAsStrList(self, bucketName, bucketPrefix, limit=None, onlyFilename=False, closedSearch=True, unique=False, startAfter=None):
        objList = self.getFilesAsObjList(bucketName, bucketPrefix, limit, onlyFilename, closedSearch, unique, startAfter)
        try:
            return [ filename["Key"] for filename in objList ]
        except TypeError:
            # No files found
            return None


    def getFilesAsObjList(self, bucketName, bucketPrefix, limit=None, onlyFilename=False, closedSearch=True, unique=False, startAfter=None):
        # limit: Limits file batches to the given amount; useful to avoid timeout errors
        # onlyFilename: Returns only the file name without the key prefix
        # closedSearch: Whether or not the search returns similarly named directories
        # unique: Returns a unique list deduping by the file's Etag
        # startAfter: Only lists keys alphabetically (lexicographic order) after the given key

        if closedSearch:
            # Needed for wildcard searches; so the search doesn't return similarly named directories
            bucketPrefix = bucketPrefix if bucketPrefix[-1] == "/" else f"{bucketPrefix}/"

        paginator = self.s3Client.get_paginator('list_objects_v2')
        if startAfter:
            pages = paginator.paginate(Bucket=bucketName, Prefix=bucketPrefix, StartAfter=startAfter)
        else:
            pages = paginator.paginate(Bucket=bucketName, Prefix=bucketPrefix)
        logger.info(f"Looking in '{bucketName}' for prefix '{bucketPrefix}'")

        allFiles = []
        try:
            for page in pages:
                for obj in page["Contents"]:

                    # Sometimes when the 'folder' is created it is counted as a file; we don't want that
                    if os.path.basename(obj["Key"]) == "":
                        continue

                    if onlyFilename:
                        obj["Key"] = os.path.basename(obj["Key"])
                        allFiles.append(obj)
                    else:
                        allFiles.append(obj)

                    # Limit file batches so we don't time-out
                    if limit and len(allFiles) >= limit:
                        break

                # Two for-loops, break again
                if limit and len(allFiles) >= limit:
                    break
        except KeyError:
            logger.info("Bucket query error; possibly key not found")
            return None

        # Reduce the amount of files identified to the requested limit
        if limit and len(allFiles) > limit:
            while (len(allFiles)) > limit:
                del allFiles[-1]

        # logger.debug(allFiles)
        if allFiles is not []:
            logger.info(f"Total files found: {len(allFiles):,}")
            if unique:
                allFiles = self.deDupe(allFiles)
                logger.info(f"Total files after deduping: {len(allFiles):,}")
            return allFiles

        logger.warning("No files found")
        return None


    def deDupe(self, listOfS3Objects: list) -> list:
        """
        Remove file duplicates based on their Etag

        :param listOfS3Objects: List of S3 objects consisting of data and its descriptive metadata

        S3 Object expected dictionary with the following elements from https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/list_objects_v2.html
        (Key, LastModified , ETag, ChecksumAlgorithm, Size, StorageClass, Owner, RestoreStatus)
        """
        logging.info("Deduping from e-tag")
        # logging.debug(listOfS3Objects)
        uniques = []
        uniqueEtags = []
        try:
            for file in listOfS3Objects:
                if file["ETag"] not in uniqueEtags:
                    uniques.append(file)
                    uniqueEtags.append(file["ETag"])
                else:
                    logger.info(f'Found duplicate based on Etag -> {file["Key"]}')
            return uniques
        except Exception as err:
            logger.error("Error Deduping")
            logger.error(err)
            raise err


    def paginateFiles(self, bucketName, bucketPrefix, onlyFilename=False, startAfterKey:str = ""):
        """
        Generator that gets keys from a bucket, after performing a few checks.
        Example usage: for key in paginateFiles(...):

        :param bucketName: the name of the bucket
        :param bucketPrefix: the path in bucket to look for keys
        :param startAfterKey: optional. Only lists keys alphabetically after the given key
        """

        paginator = self.s3Client.get_paginator('list_objects_v2')
        if len(startAfterKey):
            pages = paginator.paginate(Bucket=bucketName, Prefix=bucketPrefix, StartAfter=startAfterKey)
        else:
            pages = paginator.paginate(Bucket=bucketName, Prefix=bucketPrefix)
        logger.info(f"Looking in '{bucketName}' for prefix '{bucketPrefix}'")

        for page in pages:
            if page.get("Contents"):
                for obj in page["Contents"]:

                    # Sometimes when the 'folder' is created it is counted as a file; we don't want that
                    if os.path.basename(obj["Key"]) == "":
                        continue

                    if onlyFilename:
                        yield os.path.basename(obj["Key"])
                    else:
                        yield obj["Key"]


    def wilcardFileExists(self, wildcardKey, bucketName=None):
        """
        Checks that a key matching a wildcard expression exists in a bucket

        :param wildcardKey: the path to the key
        :type wildcardKey: str
        :param bucketName: the name of the bucket
        :type bucketName: str
        """
        return self.getWildcardKey(wildcardKey=wildcardKey,
                                bucketName=bucketName) is not None


    def getWildcardKey(self, wildcardKey, bucketName, limit=None, unique=False):
        """
        Returns a list of S3 objects matching the wildcard expression

        :param wildcardKey: the path to the key
        :type wildcardKey: str
        :param bucketName: the name of the bucket
        :type bucketName: str
        """

        prefix = re.split(r'[*]', wildcardKey, 1)[0]
        # logger.debug(f"Prefix searched: {prefix}")
        kList = self.getFilesAsStrList(bucketName, prefix, limit, closedSearch=False, unique=unique)
        if kList:
            keyMatches = [k for k in kList if fnmatch.fnmatch(k, wildcardKey)]
            if keyMatches:
                logger.info(f"Total matching files: {len(keyMatches)}")
                # logger.debug(f"WildcardKey Matches:{keyMatches}")
                return keyMatches

        return None


    def createEmptyKey(self, bucketName, s3Key):
        try:
            self.s3Client.put_object(
                Bucket=bucketName,
                Key=s3Key
            )
            return True
        except ClientError as e:
            logger.critical(f"Error creating empty S3 key: {e}")
            return False


    def pushDataToS3(self, bucketName, s3Key, theData):
        try:
            self.s3Client.head_bucket(Bucket=bucketName)
        except ClientError as e:
            logger.critical("Error finding {} bucket!: {}".format(bucketName, e))
            return False

        try:
            self.s3Client.put_object(Bucket=bucketName, Key=s3Key, Body=theData)
            # logger.info(f'Uploaded file:  {s3Key}')

        except ClientError as e:
            logger.error(f'Upload failed:  {s3Key}')
            logger.error(f'Error:  {e}')
            logger.error(f'Error Response: {e.response}')
            return False

        return True


    def pushToS3(self, localFilePath, s3DirPrefix, bucketName, deleteOrig=False, s3BaseFileName=None, **kwargs):
        # Note that ExtraArgs for AWS's upload_file() can be received in the 'extras' parameter
        # This is useful for assigning ContentType or Expires tags, etc.
        # e.g. extras={'Expires': expirationDate}
        #      extras={'ContentType': 'application/json'}

        try:
            self.s3Client.head_bucket(Bucket=bucketName)
        except ClientError as e:
            logger.critical("Error finding {} bucket!: {}".format(bucketName, e))
            return False

        if not s3BaseFileName:
            s3Filename = f"{s3DirPrefix}/{os.path.basename(localFilePath)}"
        else:
            s3Filename = f"{s3DirPrefix}/{s3BaseFileName}"

        # Don't forget the encryption stuff, or you'll get AccessDenied errors on Put
        extraArgs={}
        extraArgs["ServerSideEncryption"] = "AES256"

        try:
            # Add any additional ExtraArgs received, if any
            extraArgs.update(kwargs['extras'])
        except KeyError:
            pass

        try:
            self.s3Client.upload_file(Filename=localFilePath, 
                                      Bucket=bucketName, 
                                      Key=s3Filename,
                                      ExtraArgs=extraArgs
                                      )

            logger.info(f"Successful upload of '{s3Filename}'")
        except ClientError as e:
            logger.error(f'File upload failed:  {localFilePath} -> {s3Filename}')
            logger.error(f'Error:  {e}')
            logger.error(f'Error Response: {e.response}')
            return False

        try:
            if deleteOrig:
                os.remove(localFilePath)
                # logger.debug("Deleted local file {}".format(localFilePath))
        except PermissionError:
            # Catching "file is being used by another process" error
            # Wait, try again, ignore if it happens again
            time.sleep(30)
            try:
                os.remove(localFilePath)
            except PermissionError as e:
                logger.warning("Couldn't delete file TWICE: {}".format(e))

        return True


    def getFileFromS3(self, bucketName, key, localFilenameAndPath):
        # logger.debug(f"Requesting getFile with bucketName='{bucketName}' key='{key}'")
        try:
            self.s3Client.download_file(bucketName, key, localFilenameAndPath)

        except ClientError as err:
            # An S3 interface error; file may not be in S3 but we're thinking it is
            logger.warning(err)
            logger.warning(f"Exception caught: ClientError retrieving from S3: '{key}'")
            return False

        except PermissionError:
            # "The process cannot access the file because it is being used by another process"
            # This happens when several threads are operating on the same file; especially for large files,
            # when a different thread is in the middle of encoding or deleting the file.
            logger.error(f"Exception caught: PermissionError retrieving from S3: '{key}'")
            return False

        except FileExistsError:
            # Usually when the file is already there but not necessarily in use by a different thread
            logger.info("Exception caught: FileExistsError when retrieving from S3")

        except Exception as e:
            # "[WinError 183] Cannot create a file when that file already exists"
            # Happens when different threads are trying to write the same file at exactly the same time
            logger.error(f"Exception caught retrieving from S3. EXCEPTION:{e}")
            return False

        return True


    def readFileContent(self, bucketName, key, encoding="utf-8"):
        try:
            obj = self.s3Client.get_object(Bucket=bucketName, Key=key)
            return obj['Body'].read().decode(encoding)

        except ClientError as e:
            # An S3 interface error; file may not be in S3 but we're thinking it is
            logger.warning(f"EXCEPTION CAUGHT Attempting read from '{key}': {e}")
            return None

        except PermissionError:
            # "The process cannot access the file because it is being used by another process"
            # This happens when several threads are operating on the same file; especially for large files,
            # when a different thread is in the middle of encoding or deleting the file.
            logger.error("PermissionError retrieving from S3: '%s'", key)
            return None

        except FileExistsError:
            # Usually when the file is already there but not necessarily in use by a different thread
            logger.info("FileExistsError when retrieving from S3")
            return None

        except Exception as e:
            # "[WinError 183] Cannot create a file when that file already exists"
            # Happens when different threads are trying to write the same file at exactly the same time
            logger.error("Exception caught retrieving from S3. EXCEPTION:{}".format(e))
            return None


    def isFileInS3(self, bucket, key):
        try:
            self.s3Client.head_object(Bucket=bucket, Key=key)
            # logger.debug(f"File confirmed to be in S3: {key}")
            return True
        except ClientError as e:
            if e.response['ResponseMetadata']['HTTPStatusCode'] == 404:
                # logger.debug(f"File not found in S3: {key}")
                pass
            else:
                logger.error("ClientError looking for file in S3, but it wasn't a 404 error.")
                logger.error(e)
            return False


    def isPrefixInS3(self, bucket, prefix):
        try:
            response = self.s3Client.list_objects_v2(Bucket=bucket, Prefix=prefix, Delimiter='/')
            return "Contents" in response
        except ClientError as e:
            logger.error("ClientError looking for prefix in S3.")
            logger.error(e)
            return False


    def deleteFileInS3(self, bucket, key):
        try:
            self.s3Client.delete_object(Bucket=bucket, Key=key)
            logger.debug("Key removed/non-existent in S3.")
            return True
        except ClientError as err:
            logger.error(f"ClientError looking for file in S3: {key}")
            logger.error(err)
            return False


    def deleteEntireKey(self, bucket, key):
        logger.info(f"Looking for files to delete in '{key}'")
        fileList = self.getFilesAsStrList(bucket, key)
        # logger.debug(f"fileList:{fileList}")

        try:
            for aFile in fileList:
                if not self.deleteFileInS3(bucket, aFile):
                    logger.warning(f"Unable to delete '{aFile}'")
        except TypeError as err:
            # In case fileList is empty
            # logger.info(f"Unable to delete:::{err}")
            pass


    def moveFileToDifferentKey(self, bucket, oldKey, newKey):
        if oldKey == newKey:
            logger.warning("File destination identical to source")
            return False

        try:
            # Copy object 'old' as object 'new'
            copySource = {
                'Bucket': bucket,
                'Key': oldKey
            }
            self.s3Client.copy(copySource, bucket, newKey)

            # Delete the former 'old' object
            self.s3Client.delete_object(Bucket=bucket, Key=oldKey)
            logger.info(f"Moved S3 file from '{oldKey}' to '{newKey}'")

        except ClientError as err:
            logger.warning(f"ClientError looking for file in S3: {oldKey}")
            logger.warning(err)
            return False

        return True


    def copyFileToDifferentBucket(self, srcBucketName, srcObjKey, dstBucketName, dstObjKey):
        logger.info(f"Copying {srcBucketName}/{srcObjKey} to {dstBucketName}/{dstObjKey}")

        try:
            copySource = {
                'Bucket': srcBucketName,
                'Key': srcObjKey
            }
            self.s3Client.copy_object(Bucket=dstBucketName, CopySource=copySource, Key=dstObjKey)
            logger.info(f"Copy successful")
            return True

        except ClientError as error:
            logger.warning(f"Copy failed")
            logger.warning(error.response['Error']['Message'])
            return False


class AWScreds:
    def __init__(self, configFile):
        logger.debug("Initializing AWS creds ")
        config = configparser.ConfigParser()
        try:
            with open(configFile) as f:
                config.read_file(f)
        except IOError as e:
            logger.critical(e)
            logger.critical("Unable to initialize")
            raise ValueError

        try:
            # Set up Active Directory Federation Services parameters (adfs)
            self.template = config['adfs']['URL_TEMPLATE']
            self.adfsUrl = self.template.format(config['adfs']['HOSTNAME'], config['adfs']['PROVIDER'])
            self.samlUrl = config['adfs']['SAML_URL']
            self.key0 = config['adfs']['KEY_0']
            self.key1 = config['adfs']['KEY_1']
            self.key2 = config['adfs']['KEY_2']

        except KeyError as e:
            logger.error(f"Missing key/section in init file: {e}")
            logger.critical("Unable to initialize")
            raise ValueError

        # Set up regex for extractions
        self.reFieldset = re.compile('(<div  class=\"saml-account\".*)</fieldset>', re.MULTILINE | re.IGNORECASE)

        logger.debug("AWS creds inited")


    def getUserPass(self, accntPostfix):
        """ Returns UserName and Password stored in Keyring. """
        # Initialize keyring using SecretService backend
        keyring = SS.Keyring()
        # Prep in case of errors
        un = None
        up = None

        up = keyring.get_password(self.key0, self.key1)
        un = keyring.get_password(self.key0, self.key2)

        if not un:
            logger.error('Username and Password not found in keyring...')
            un = getpass.getuser().split('-')[0] + accntPostfix
            up = getpass.getpass(prompt='Enter ' + un + '\'s password: ')

        return un, up


    def getSamlToken(self, username, password):
        """Get the SAML token to log in"""
        reqSession = requests.Session()

        # Open the ADFS page
        adfsResponse = None

        try:
            # Temporarily suspend urllib3 warnings
            # essentially, urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            # urllib3.disable_warnings is really one-line wrapper for warnings.simplefilter('ignore', category)
            # but we want to be able to restore the warnings afterwards
            warnings.simplefilter('ignore', urllib3.exceptions.InsecureRequestWarning)

            adfsResponse = reqSession.get(self.adfsUrl, verify=False)
            adfsResponse.raise_for_status()

            # Restore urllib3 warnings
            warnings.simplefilter('default', urllib3.exceptions.InsecureRequestWarning)
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting ADFS page: {e}")
            raise RuntimeError

        # Get the ADFS URL
        submitUrl = adfsResponse.url
        # Build a dictionary of the form ADFS expects
        # NOTE: Might need to update this if the ADFS URL ever changes
        payload = {
            'UserName': username,
            'Password': password,
            'Kmsi': 'true',
            'AuthMethod': 'FormsAuthentication'}

        # Post the form to the ADFS URL with the populated payload
        loginResponse = None
        try:
            # Temporarily suspend urllib3 warnings
            warnings.simplefilter('ignore', urllib3.exceptions.InsecureRequestWarning)

            loginResponse = reqSession.post(submitUrl, data=payload, verify=False)

            # Restore urllib3 warnings
            warnings.simplefilter('default', urllib3.exceptions.InsecureRequestWarning)

            loginResponse.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error posting ADFS login: {e}")
            raise RuntimeError

        # Extract the SAML assertion, fail if there's no assertion
        reSaml = re.compile('SAMLResponse\" value=\"([^\"]+)\"')
        samlMatch = reSaml.search(loginResponse.text)
        if samlMatch.group(1):
            return samlMatch.group(1)
        else:
            logger.error(f"Response did not contain a SAML assertion: {e}")
            raise RuntimeError


    def getRoles(self, samlToken):
        """
        Given a SAML Token, extract account id, role ARN, and principal ARN and return a dictionary
        with account ID as key and roleArn and principalArn as items
        """
        roles = {}

        # Decode SAML in order to parse
        # This chains several string methods to end up with the SAML converted to a list of elements
        samlList = base64.b64decode(samlToken).decode('utf-8').replace('><', '>\n<').split('\n')
        for l in samlList:
            if 'saml-provider' in l:
                # Clean the line
                l = l.replace('<AttributeValue>', '').replace('</AttributeValue>', '').strip()
                # Split line into principal and role ARNs
                principalArn, roleArn = l.split(',')
                # Extract account ID from line (could use role or principal ARN instead)
                accountId = l.split(':')[4]
                # Add account ID as key with role and principal ARNs as items
                roles[accountId] = {
                    "roleArn": roleArn.strip(),
                    "principalArn": principalArn.strip()
                }
        if roles:
            return roles
        else:
            logger.error(f"Unable to get roles from SAML token")
            raise RuntimeError


    def getProfileAccountFromSaml(self, samlToken):
        """ Given a SAML token, log into the AWS SAML Signin Page to capture account information"""
        accounts = {}
        payload = "SAMLResponse=" + requests.utils.quote(samlToken)
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        # Temporarily suspend urllib3 warnings
        warnings.simplefilter('ignore', urllib3.exceptions.InsecureRequestWarning)

        response = requests.post(self.samlUrl, data=payload, headers=headers, verify=False)

        # Restore urllib3 warnings
        warnings.simplefilter('default', urllib3.exceptions.InsecureRequestWarning)

        if 200 <= response.status_code < 300:
            rt = response.text.replace('\n', ' ')
            fsMatch = self.reFieldset.search(rt)
            fs = fsMatch.group(1).replace('> ', '>\n ')
            fs_tmp = fs.split('\n')
            for l in fs_tmp:
                if '\"saml-account-name\">Account:' in l:
                    l = l.replace('<div class=\"saml-account-name\">Account: ', '').replace('</div>', '')
                    a, b = l.split('(')
                    acntName = a.strip()
                    acntId = b.replace(')', '').strip()
                    accounts[acntName] = acntId
            if accounts:
                return accounts
            else:
                logger.error(f"Unable to get account list from {self.samlUrl}")
                raise RuntimeError
        else:
            logger.error(f'Response.status code not 200 <= response.status_code < 300: {response.status_code}')
            raise RuntimeError


    def assumeRoleWithSaml(self, roleObject, samlToken):
        """
        Use the assertion to get an AWS STS token using Assume Role with SAML
        """
        sts = boto3.client('sts')
        token = sts.assume_role_with_saml(RoleArn=roleObject.get("roleArn"),
                                        PrincipalArn=roleObject.get("principalArn"),
                                        SAMLAssertion=samlToken)
        credential = token.get("Credentials")

        return credential


    def returnSamlSession(self, creds, awsRegion='us-east-1'):
        session = boto3.session.Session(region_name=awsRegion,
                                        aws_access_key_id=creds['AccessKeyId'],
                                        aws_secret_access_key=creds['SecretAccessKey'],
                                        aws_session_token=creds['SessionToken']
                                        )
        return session
