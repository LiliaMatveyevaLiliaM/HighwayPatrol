"""
"""

# External libraries import statements
import re
import os
import time
import json
import shutil
import pycurl
import logging
import requests
import ipaddress
import datetime as dt
from io import BytesIO
from typing import Optional
from datetime import timezone as tz
from fake_useragent import UserAgent
from requests.exceptions import SSLError
from requests.exceptions import HTTPError


logger = logging.getLogger()

class NetworkUtils:
    """
    Common methods for network operations on many of our collection efforts
    """

    def __init__(self, **kwargs):
        logger.debug("Initializing network client ")

        # These "allowedKeys" become part of the object as self.xxx
        allowedKeys = {"proxy", "sessionHeaders", "workDirectory", "verify"}
        self.__dict__.update((k, v) for k, v in kwargs.items() if k in allowedKeys)

        # Set up our requests library session
        # Single session object so we don't have to reinstantiate every time
        # If you want a different user agent string be sure to call switchAgentString()
        self.sessionObj = requests.Session()

        # For cases where we're bundling our own CA files (e.g. for VPN)
        if self.verify:
            self.sessionObj.verify = self.verify

        try:
            if self.proxy:
                logger.debug(f"PROXY: {self.proxy}")
                self.sessionObj.proxies = {"http" : self.proxy, "https" : self.proxy}
            else:
                logger.debug(f"PROXY: Not set")
                self.sessionObj.proxies = {"http" : None, "https" : None}

            # Randomize the user-agent string
            self.switchAgentString()

        except KeyError as e:
            logger.critical(e)
            logger.critical("Unable to initialize network connection")
            raise KeyError


    def restartSessionObj(self):
        logger.info("Reseting the network session object")
        tmpProxies = self.sessionObj.proxies

        self.sessionObj = requests.Session()
        self.switchAgentString()
        self.sessionObj.proxies = tmpProxies


    def disableCertCheck(self):
        # This disables cert checking for the entire session, not just one call
        logger.info("Disabling SSL (i.e. verify=False)")

        tmpProxies = self.sessionObj.proxies
        self.sessionObj = requests.Session()
        self.sessionObj.verify = False

        self.switchAgentString()
        self.sessionObj.proxies = tmpProxies


    def switchAgentString(self):
        newAgentString = self.getUserAgentString()
        logger.debug(f"Switching user agent string: {newAgentString}")
        self.sessionHeaders["User-Agent"] = newAgentString
        self.sessionObj.headers.update(self.sessionHeaders)


    def downloadImage(self, fileName, inUrl):
        logger.info(f"Downloading image: {inUrl}")

        response = requests.get(inUrl, stream=True)
        if response.status_code == 200:
            fullFilePath = os.path.join(self.workDirectory, fileName)
            # Create image file; open as binary
            with open(fullFilePath, 'wb') as f:
                shutil.copyfileobj(response.raw, f)
            return response

        else:
            logger.warning(f"Received status '{response.status_code}' trying {inUrl}")
            raise ConnectionError(f"Received status '{response.status_code}' trying {inUrl}")


    def getFileEtag(self, inUrl):
        substringPattern = "\"(.*?)\""      # to grab the string between the double-quotes
        r = self.sessionObj.head(inUrl)
        try:
            etag = re.search(substringPattern, r.headers['ETag']).group(1)
            # logger.debug(f"ETag->{etag}<-")
        except KeyError:
            etag = "NOETAGFOUND"
            logger.warning(f"KeyError trying to get ETag; will use '{etag}'")
            logger.debug(f"HEADER RECEIVED:\n{json.dumps(dict(r.headers))}")

        return etag


    def getFileLastMod(self, inUrl):
        # Get file's Last-Modified time w/out downloading it
        logger.info(f"Requesting lastModDate to '{inUrl}'")
        r = self.sessionObj.head(inUrl)
        # logger.debug(f"HEAD Response headers: {json.dumps(dict(r.headers))}")
        try:
            lastMod = r.headers['Last-Modified']
        except KeyError:
            logger.warning(f"Response does NOT include a 'Last-Modified' entry")
            logger.debug(f"HEADER RECEIVED:\n{json.dumps(dict(r.headers))}")
            raise
        except (ConnectionError, ConnectionResetError) as err:
            logger.error(f"Exception occurred obtaining HEAD info on file:::{err}")
            raise ConnectionError(f"URL head access failed")

        # logger.debug(f"lastMod->{lastMod}<-")
        return lastMod


    def getFileLastModEpoch(self, inUrl):
        # StringParseTime (strptime)
        lastMod = self.getFileLastMod(inUrl)
        try:
            modTime = dt.datetime.strptime(lastMod, '%a, %d %b %Y %X %Z')
        except ValueError:
            modTime = dt.datetime.strptime(lastMod, '%A, %d-%b-%Y %X %Z')
        # logger.debug(f"modTime->{modTime}<-")

        return int(time.mktime(modTime.timetuple()))


    def getFileLastModDate(self, inUrl):
        inEpoch = self.getFileLastModEpoch(inUrl)
        inDate = dt.datetime.fromtimestamp(inEpoch, tz=tz.utc)
        year = str(inDate.strftime('%Y'))
        month = str(inDate.strftime('%m'))
        day = str(inDate.strftime('%d'))

        return year, month, day


    def downloadFile(self, inUrl, localFileName=None):
        if not localFileName:
            localFileName = inUrl.split('/')[-1]

        r = self.sessionObj.head(inUrl)
        logger.info("Downloading target file")
        logger.info(f"File headers are:\n\n{json.dumps(dict(r.headers))}\n\n")

        theJSON = ""
        try:
            r = self.sessionObj.get(inUrl, stream=True)
            r.raise_for_status()
        except HTTPError:
            return None

        r.raw.decode_content = True
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            logger.info(f"Received file chunk of {len(chunk)} bytes")
            if chunk:  # filter out keep-alive new chunks
                theJSON += chunk.decode('utf-8')

        with open(os.path.join(self.workDirectory, localFileName), 'w') as f:
            f.write(theJSON)

        return localFileName


    def getVideoStream(self, fileName, inUrl, headers, inParams):
        response = self.sessionObj.get(url=inUrl, headers=headers, params=inParams, stream=True)
        # logger.debug(f"URL->{response.url}<-")

        if response.status_code == 200:
            with open(os.path.join(self.workDirectory, fileName), 'wb') as f:
                shutil.copyfileobj(response.raw, f)
            return True
        else:
            logger.warning(f"Received status '{response.status_code}' trying {inUrl}")
            return False


    def get(self, inUrl, timeout=20, **kwargs):
        # Make request using pycurl library
        if kwargs.pop("useCurl", False):
            buffer = BytesIO()
            c = pycurl.Curl()

            try:
                c.setopt(c.URL, inUrl)
                c.setopt(c.SSL_VERIFYPEER, 0)
                c.setopt(c.FOLLOWLOCATION, 1)
                c.setopt(c.ACCEPT_ENCODING, "")
                c.setopt(c.SSLVERSION, c.SSLVERSION_TLSv1_0)
                c.setopt(c.SSL_CIPHER_LIST, "DEFAULT:!DH")
                c.setopt(c.CONNECTTIMEOUT, 10)
                if kwargs.get('headers', False):
                    header_list = [f"{k}: {v}" for k, v in kwargs["headers"].items()]
                    c.setopt(c.HTTPHEADER, header_list)
                if self.proxy:
                    c.setopt(c.PROXY, self.proxy)
                c.setopt(c.WRITEDATA, buffer)
                c.perform()

                statusCode = c.getinfo(c.RESPONSE_CODE)
                content = buffer.getvalue()
                response = CurlResponse(content, statusCode)

                if response.statusCode != 200:
                    logger.warning(f"RESPONSE != 200: '{response.content}' attempting '{inUrl}'")
                    raise ConnectionError("NOT 200")

            except pycurl.error as e:
                code, message = e.args
                logger.warning(f"Caught Exception while attempting {inUrl} ::{code}::{message}")
                raise
            except Exception as e:
                logger.critical(f"Caught Exception while attempting {inUrl} ::{message}")
                raise
            finally:
                c.close()
            return response

        # Make get request using Requests
        try:
            p = requests.Request('GET', inUrl, **kwargs).prepare()
            logger.info(f"Trying GET access: '{p.url}'")
        except Exception:
            # prepare() can't handle things like allow_redirects=False so just ignore
            logger.info(f"Trying GET access: '{inUrl}'")
        # Not actually using this prepared request because we don't want to alter
        # the Session object or anything else in this function
        # According to documentation on 04/03/23:
        #       "the above code will lose some of the advantages of having a Requests Session
        #        object. In particular, Session-level state such as cookies will not get
        #        applied to your request. To get a PreparedRequest with that state applied,
        #        replace the call to Request.prepare() with a call to Session.prepare_request()"

        try:
            if "headers" in kwargs:
                logger.debug("Will request using new headers")
            response = self.sessionObj.get(url=inUrl, timeout=timeout, **kwargs)
            # logger.debug(response.text)
            # logger.debug(f"COOKIES: {self.sessionObj.cookies.get_dict()}")

        except SSLError as err:
            logger.warning(err)
            self.disableCertCheck()
            try:
                response = self.sessionObj.get(url=inUrl, timeout=timeout, **kwargs)
            except Exception as e:
                logger.critical(f"Caught Exception twice while attempting {inUrl} ::{e}")
                logger.info("Giving up")
                raise

        except Exception as e:
            logger.warning(f"Caught Exception while attempting {inUrl} ::{e}")
            logger.info("Sleeping for 30s to see if we can recover")
            time.sleep(30)
            logger.info("Trying again...")

            try:
                response = self.sessionObj.get(url=inUrl, timeout=timeout, **kwargs)
            except Exception as e:
                logger.critical(f"Caught Exception twice while attempting {inUrl} ::{e}")
                logger.info("Giving up")
                raise

        # logger.debug('****************************')
        # logger.debug('******ENCODING RECEIVED*****')
        # logger.debug(f'response.encoding = {response.encoding}')
        # logger.debug(f'response.apparent_encoding = {response.apparent_encoding}')
        # logger.debug('******SETTING ENCODING******')
        # response.encoding = "GBK"
        # logger.debug(f'response.encoding = {response.encoding}')
        # logger.debug('****************************')

        # Handle re-directs ourselves when requested to circumvent any anti-scraping techniques
        if response.status_code == 302:
            logger.info(f"Re-direct response detected (HTTP 302)")
            if "Location" in response.headers:
                # Need to strip the headers out of the previous request for anti-scraping
                # Seen on 08.04.23 for ivdeon.com
                if "headers" in kwargs:
                    del kwargs["headers"]
                for i in range(self.sessionObj.max_redirects):
                    redirUrl = response.headers['Location']
                    logger.info(f"URL redirected to: '{redirUrl}'")
                    response = self.sessionObj.get(url=redirUrl, timeout=timeout, **kwargs)
                    if response.status_code != 302:
                        break
                    if not "Location" in response.headers:
                        break

        if response.status_code !=200:
            logger.warning(f"RESPONSE !=200: '{response}' attempting '{inUrl}'")
            if response.request.url != inUrl:
                logger.debug("Response.request.url was different than Requested.url")
                logger.debug(f"Requested URL in Response is '{response.request.url}'")
                logger.debug(f'Request Headers:\n{json.dumps(dict(response.request.headers))}')
                logger.debug(f'Response Headers:\n{json.dumps(dict(response.headers))}')
            raise ConnectionError("NOT 200")

        return response


    def post(self, inUrl, verify=True, **kwargs):
        # Needed to separate "verify" from the args because prepare() doesn't like it
        p = requests.Request('POST', inUrl, **kwargs).prepare()
        logger.info(f"Trying POST access: '{p.url}'")
        self._prettyPrintPost(p)
        # Not actually using this prepared request because we don't want to alter
        # the Session object or anything else in this function
        # According to documentation on 04/03/23:
        #       "the above code will lose some of the advantages of having a Requests Session
        #        object. In particular, Session-level state such as cookies will not get
        #        applied to your request. To get a PreparedRequest with that state applied,
        #        replace the call to Request.prepare() with a call to Session.prepare_request()"

        try:
            if "headers" in kwargs:
                logger.debug("Will request using new headers")
            response = self.sessionObj.post(url=inUrl, verify=verify, **kwargs)
            # logger.debug(response.text)
            # logger.debug(f"COOKIES: {self.sessionObj.cookies.get_dict()}")

        except Exception as e:
            logger.warning(f'Caught Exception while attempting {inUrl} ::{e}')
            logger.info('Sleeping for 30s to see if we can recover')
            time.sleep(30)
            logger.info('Trying again...')

            try:
                response = self.sessionObj.post(url=inUrl, verify=verify, **kwargs)
            except Exception as e:
                logger.critical(f'Caught Exception twice while attempting {inUrl} ::{e}')
                logger.info('Giving up')
                raise

        if response.status_code !=200:
            logger.warning(f"RESPONSE !=200: '{response}' attempting '{inUrl}'")
            if response.request.url != inUrl:
                logger.debug("Response.request.url was different than Requested.url")
                logger.debug(f"Requested URL in Response is '{response.request.url}'")
            logger.debug(f'Request Headers:\n{json.dumps(dict(response.request.headers))}')
            logger.debug(f'Response Headers:\n{json.dumps(dict(response.headers))}')
            raise ConnectionError("NOT 200")

        return response


    def options(self, inUrl, **kwargs):
        logger.info(f"Trying OPTIONS access: '{inUrl}'")

        try:
            response = self.sessionObj.options(url=inUrl, **kwargs)
        except Exception as e:
            logger.warning(f'Caught Exception while attempting {inUrl} ::{e}')

        if response.status_code !=200:
            logger.warning(f"RESPONSE !=200: '{response}' attempting '{inUrl}'")
            raise ConnectionError("NOT 200")

        return response


    def _prettyPrintPost(self, req):
        """
        Note that formatting here is pretty printed
        and may differ slighly from the actual request
        """
        print('{}\n{}\r\n{}\r\n\r\n{}'.format(
            '-----------START-----------',
            req.method + ' ' + req.url,
            '\r\n'.join('{}: {}'.format(k, v) for k, v in req.headers.items()),
            req.body,
        ))
        print('------------END------------')


    def checkCookie(self, key, value, cookies):
        # See if we can circumvent the cookies not received for some values we have received
        # during testing...yeah, I know it's a long shot, but seems to work so far
        if key not in cookies:
            logger.warning(f"'{key}' was not in the cookies received; substituting with: {value}")
            cookies[key] = value
        return cookies


    def getUserAgentString(self):
        ua = UserAgent(os=['linux', 'windows'], browsers=['chrome', 'firefox'])
        return ua.random
    

# Possible values for the check endpoints
#DEFAULT_IPCHECK_ENDPOINT = "https://icanhazip.com"
#DEFAULT_IPCHECK_ENDPOINT = "https://api64.ipify.org"
DEFAULT_IPCHECK_ENDPOINT = "https://checkip.amazonaws.com"
#DEFAULT_IPCHECK_ENDPOINT = "https://protonwire-api.vercel.app/v1/client/ip"

def getPublicIp(
        ipCheckEndpoint: str = DEFAULT_IPCHECK_ENDPOINT, 
        session: dict = None) -> Optional[str]:
    """
    Invokes the specified endpoint, which is assumed to return
    the current public IP address. It then validates that the value
    returned is a valid IP address. Return None if the endpoint 
    cannot be contacted or if the value returned from the endpoint
    is not a valid IP address.

    ### Parameters
    - ipCheckEndpoint : str
        - [REQUIRED] Healthcheck endpoint which must return your
          public IP address (default: DEFAULT_IPCHECK_ENDPOINT).
    - proxies: dict
        - requests proxy dictionary

    ### Returns
    - str
        - Current public IP address. None if can't be determined
    """

    try:
        if session:
            response = session.get(ipCheckEndpoint)
        else:
            response = requests.get(ipCheckEndpoint)

        if response is None:
            return None
        elif response.text is None:
            return None
        elif len(response.text) == 0:
            return None
        publicIpAddress = response.text.strip()
        ipaddress.ip_address(publicIpAddress)

        return publicIpAddress

    except Exception:
        return None


class CurlResponse:
    def __init__(self, content, statusCode):
        self.content = content
        self.statusCode = statusCode
