#!/usr/bin/env python3
"""Trigger resource check from webhook.
"""

import argparse
import requests

parser = argparse.ArgumentParser(description="trigger resource check from webhook")
parser.add_argument("-u", "--url", help="url to post to", dest="url", required=True)
args = parser.parse_args()

# base = "https://runway-ci.eng.vmware.com/api/v1/teams/velo-techops-vcg/pipelines/"\
#        "tf-worker/resources/netbox/check/webhook?webhook_token=netbox"
response = requests.post(args.url, verify=False)
if response.status_code < 300:
    print(response.json())
