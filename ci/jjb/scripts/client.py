#!/usr/bin/env python
"""
client.py

A bare-bones VCO API client

Dependencies:

The only library required to use this tool is the Python requests library, which can be installed with pip, e.g.
```
    pip install requests
```

Examples:

 (1) Initialize, authenticate:
 ```
     client = VcoRequestManager("vcoXX-usvi1.velocloud.net")
     client.authenticate(os.environ["VC_USERNAME"], os.environ["VC_PASSWORD"], is_operator=True)
 ```

 (2) Get Edges
 ```
     client.call_api("enterprise/getEnterpriseEdges", { "enterpriseId": 1 })
 ```
"""


import json
import re
import requests


class ApiException(Exception):
    """An API-specific exception."""


class VcoRequestManager:
    """A class to handle requests to VCO."""

    def __init__(self, hostname, verify_ssl=True):
        self._session = requests.Session()
        self._verify_ssl = verify_ssl
        self._root_url = self._get_root_url(hostname)
        self._portal_url = self._root_url + "/portal/"
        self._livepull_url = self._root_url + "/livepull/liveData/"
        self._seqno = 0

    @staticmethod
    def _get_root_url(hostname):
        """
        Translate VCO hostname to a root url for API calls
        """
        if hostname.startswith("http"):
            re.sub("http(s)?://", "", hostname)
        proto = "https://"
        return proto + hostname

    def authenticate(self, username, password, is_operator=True):
        """
        Authenticate to API - on success, a cookie is stored in the session
        """
        path = "/login/operatorLogin" if is_operator else "/login/enterpriseLogin"
        url = self._portal_url + "rest" + path
        data = {"username": username, "password": password}
        headers = {"Content-Type": "application/json"}
        self._session.post(
            url,
            headers=headers,
            data=json.dumps(data),
            allow_redirects=True,
            verify=self._verify_ssl,
        )

    def call_api(self, method, params):
        """
        Build and submit a request
        Returns method result as a Python dictionary
        """
        self._seqno += 1
        headers = {"Content-Type": "application/json"}
        method = self._clean_method_name(method)
        payload = {
            "jsonrpc": "2.0",
            "id": self._seqno,
            "method": method,
            "params": params,
        }

        if method in (
            "liveMode/readLiveData",
            "liveMode/requestLiveActions",
            "liveMode/clientExitLiveMode",
        ):
            url = self._livepull_url
        else:
            url = self._portal_url

        response = self._session.post(
            url, headers=headers, data=json.dumps(payload), verify=self._verify_ssl
        )

        response_dict = response.json()
        if "error" in response_dict:
            raise ApiException(response_dict["error"]["message"])
        return response_dict["result"]

    @staticmethod
    def _clean_method_name(raw_name):
        """
        Ensure method name is properly formatted prior to initiating request
        """
        return raw_name.strip("/")
