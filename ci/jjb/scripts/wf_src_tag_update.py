#!/usr/bin/env python3
"""
Python script to add WF source tags
"""
import argparse
import sys
import os
import re
import json
import warnings
from socket import gaierror, getaddrinfo, SOCK_STREAM
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError, MaxRetryError
import requests


warnings.filterwarnings("ignore")

parser = argparse.ArgumentParser()
parser.add_argument("--vco-fqdn", type=str, help="VCO FQDN", default="ALL")
parser.add_argument(
    "--env",
    type=str,
    help="Environment",
    choices=["prod", "nonprod"],
    default="nonprod",
)
parser.add_argument(
    "--vco-username",
    type=str,
    help="Username of VCO API",
    default=os.getenv("VC_USERNAME"),
)
parser.add_argument(
    "--vco-password",
    type=str,
    help="Password of VCO API",
    default=os.getenv("VC_PASSWORD"),
)
parser.add_argument(
    "--wf-token", type=str, help="WaveFront Token", default=os.getenv("WF_TOKEN")
)
parser.add_argument(
    "--netbox-token", type=str, help="Netbox Token", default=os.getenv("NETBOX_TOKEN")
)
parser.add_argument(
    "--get-vco-list",
    help="Specify if you want to only get VCO list from Netbox",
    action="store_true",
)
parser.add_argument(
    "--data", type=str, help="Lists of VCO for source update", default=[]
)


args = parser.parse_args()

USERNAME = args.vco_username
PASSWORD = args.vco_password
WF_TOKEN = args.wf_token
NETBOX_TOKEN = args.netbox_token
VCO_FQDN = args.vco_fqdn.lower()
WF_PROD = "https://vmwareprod.wavefront.com"
WF_NONPROD = "https://vmwareprod2.wavefront.com"
WF_HOST = WF_PROD if args.env == "prod" else WF_NONPROD
WF_HEADER = {"Authorization": f"Bearer {WF_TOKEN}"}
NETBOX_API_URL = f"https://netbox.vmware-{args.env}.net/api"
NETBOX_HEADERS = {
    "Authorization": f"Token {NETBOX_TOKEN}",
    "Content-Type": "application/json",
}
NETBOX_PARAMS = {
    "fields": "name,tenant,custom_fields",
    "limit": 0,
    "role": "vco",
}


def _request_json_post(session, url, body=None, json_op=True):
    """Make a HTTP Post connection"""
    body = body or {}
    try:
        out = session.post(url, json=body)
        if json_op:
            result = out.json()
        else:
            result = out.text

    except (
        TimeoutError,
        ConnectTimeoutError,
        NewConnectionError,
        MaxRetryError,
        requests.exceptions.ConnectTimeout,
        requests.exceptions.ConnectionError,
    ):
        result = "Connection Failure"
    return result


def set_auth_cookie(fqdn, session):
    """Set Authentication cookie"""
    # Set auth cookie
    try:
        getaddrinfo(fqdn, 443, type=SOCK_STREAM)
        url = f"https://{fqdn}/login/doOperatorLogin.html"
        body = {"username": USERNAME, "password": PASSWORD}
        response = _request_json_post(session, url, body, json_op=False)
    except gaierror:
        response = "DNS Failure"
    return response


def test_auth_cookie(fqdn, session):
    """Test the Authetication"""
    # Test auth cookie
    url = f"https://{fqdn}/portal/rest/userAgreement/getUserAgreements"
    response = _request_json_post(session, url)
    try:
        response["error"]
    except (TypeError, IndexError):
        return "success"
    return None


def get_vcgs_details(fqdn, session):
    """Get the VCG details using VCO API"""
    # Set auth cookie
    url = f"https://{fqdn}/portal/rest/network/getNetworkGateways"
    response = _request_json_post(session, url, {})
    return response


def get_vcg_details(fqdn, session):
    """Get the VCG details"""
    vcgs_details = get_vcgs_details(fqdn, session)
    vcg_list = []
    if isinstance(vcgs_details, list):
        for item in vcgs_details:
            if (
                re.match(r"(?i)^vcg\d{2,3}-[a-z]{3,4}\de?$", item["name"])
                and item["activationState"] == "ACTIVATED"
                and item["serviceState"] == "IN_SERVICE"
            ):
                vcg_list.append(
                    {
                        "name": item["name"],
                        "version": item["softwareVersion"].replace(".", "_"),
                        "buildnum": item["buildNumber"],
                    }
                )
    else:
        print(f"Error retrieving gateway data of VCO {fqdn}")
    return vcg_list


def get_netbox_vcos(url, headers, params):
    """Get the VCO list from Netbox"""
    vco_list = []
    get_vco_url = f"{url}/virtualization/virtual-machines/"
    try:
        # Make the GET request to retrieve the VMs
        response = requests.get(get_vco_url, headers=headers, params=params)
        response.raise_for_status()

        # Retrieve the VMs from the response JSON
        vms = response.json()["results"]

        # Process the VMs
        for item in vms:
            vm_name = item["name"]
            custom_field = item["custom_fields"]
            tenant_name = item["tenant"]["name"] if item["tenant"] else None
            tenant_id = item["tenant"]["id"] if item["tenant"] else None

            # Fetch the tenant_group information using tenant_id
            tenant_group = None
            if tenant_id:
                tenant_response = requests.get(
                    f"{url}/tenancy/tenants/{tenant_id}/", headers=headers
                )
                if tenant_response.status_code == 200:
                    tenant = tenant_response.json()
                    tenant_group = (
                        tenant["group"]["name"] if tenant["group"] else "Not assigned"
                    )

            vco_list.append(
                {
                    "name": vm_name,
                    "fqdn": custom_field["fqdn"],
                    "version": custom_field["version"],
                    "buildnum": custom_field["buildnum"],
                    "instance_type": custom_field["instance_type"],
                    "tenant": tenant_name,
                    "tenant_id": tenant_id,
                    "tenant_group": tenant_group,
                }
            )

    except requests.exceptions.RequestException as err:
        raise SystemExit("Error occurred during API request:") from err

    return vco_list


def get_src_tags(url, headers):
    """Get the Source tags from WF"""
    try:
        vcg_tags = requests.get(url, headers=headers)
        vcg_tags.raise_for_status()
        src_tags = vcg_tags.json()
        existing_src_tags = [
            i for i in src_tags["response"]["items"] if i.startswith("vcg.")
        ]
    except requests.exceptions.RequestException:
        existing_src_tags = None
    return existing_src_tags


def create_src_tags(url, headers, src_tags):
    """Create the Source tags in WF"""
    for tags in src_tags:
        try:
            put_status = requests.put(f"{url}/{tags}", headers=headers)
            put_status.raise_for_status()
        except requests.exceptions.RequestException as err:
            print(f"Create tag {tags} failed. {err}")


def delete_src_tags(url, headers, src_tags):
    """Delete the Source tags in WF"""
    for tags in src_tags:
        try:
            delete_status = requests.delete(f"{url}/{tags}", headers=headers)
            delete_status.raise_for_status()
        except requests.exceptions.RequestException as err:
            print(f"Delete tag {tags} failed. {err}")


def print_json_in_red(json_data):
    """Print the given json in red color"""
    # Convert JSON data to a string
    json_string = json.dumps(json_data, indent=4)
    # Define the ANSI escape sequence for red color
    red_color = "\033[91m"
    # Reset the color to default after printing
    reset_color = "\033[0m"
    # Print the JSON data in red color
    print(f"{red_color}{json_string}{reset_color}")


def main():
    """Main function to update the WF source tags"""
    vco_list, failed_vcos, failed_vcgs = [], [], {}  # Setting empty values
    # Return if request is only to get VCO list
    if args.get_vco_list:
        # Get the VCO List from Netbox
        vco_list = get_netbox_vcos(NETBOX_API_URL, NETBOX_HEADERS, NETBOX_PARAMS)

        # If VCO_FQDN is not passed, all VCO is taken for tag update
        if VCO_FQDN != "all":
            # Check if given VCO is available in Netbox data
            filtered_vco_list = [vco for vco in vco_list if VCO_FQDN == vco["fqdn"]]
            if not filtered_vco_list:
                print(f"Provided VCO Name {VCO_FQDN} is incorrect")
                sys.exit(1)
            else:
                vco_list = filtered_vco_list
        else:
            # Skipping the VCO that not populated with details in netbox
            skip_vco = [
                vco for vco in vco_list if vco["fqdn"] is None or vco["fqdn"] == ""
            ]
            vco_list = [vco for vco in vco_list if vco not in skip_vco]

        print(json.dumps(vco_list, indent=4))
        return

    # Processing vco list
    if args.data:
        vco_list = json.loads(args.data)
        if not vco_list:
            print("Nothing to do!! Data is empty")
            return
    # Looping for all VCO's
    for item in vco_list:
        vco_name = item["name"]
        vco_fqdn = item["fqdn"]
        vco_type = item["tenant"].lower() if item["tenant"] else None
        customer = item["tenant_group"].lower() if item["tenant_group"] else None

        # Login to VCO & Test connectivity
        vco_session = requests.Session()
        set_auth_status = set_auth_cookie(vco_fqdn, vco_session)
        if "failure" in set_auth_status.lower():
            failed_vcos.append({"name": vco_name, "reason": set_auth_status})
            continue
        auth_status = test_auth_cookie(vco_fqdn, vco_session)
        if auth_status is None:
            failed_vcos.append({"name": vco_name, "reason": "Auth Failure"})
            continue
        # Fetch the VCG details
        vcg_details = get_vcg_details(vco_fqdn, vco_session)
        # print(
        #    f"Source tag update in progress for VCG's of VCO {vco_fqdn} "
        #    f"{[vcg['name'] for vcg in vcg_details]}"
        # )
        failed_vcg_list = []

        # Looping source tag update for all VCG's
        for vcg in vcg_details:
            req_src_tags, del_src_tags = [], []
            vcg_name = vcg["name"]
            wavefront_url = f"{WF_HOST}/api/v2/source/{vcg_name}/tag"
            # Fetch existing Source tags
            existing_src_tags = get_src_tags(wavefront_url, WF_HEADER)
            if existing_src_tags is None:
                failed_vcg_list.append(vcg_name)
                continue
            # Set source tags to be updated
            req_src_tags.append(f'vcg.version.{vcg["version"]}')
            req_src_tags.append(f'vcg.build.{vcg["buildnum"]}')
            req_src_tags.append(f"vcg.vco.name.{vco_name}")
            if vco_type is not None:
                req_src_tags.append(f"vcg.vco.type.{vco_type}")
            if customer is not None:
                req_src_tags.append(f"vcg.vco.customer.{customer}")
            # Get list of source tag to be deleted, as append is not possible in WF
            del_src_tags = list(set(existing_src_tags) - set(req_src_tags))
            # Delete the source tags if available
            if len(del_src_tags) > 0:
                delete_src_tags(wavefront_url, WF_HEADER, del_src_tags)
            # Create the required source tags
            create_src_tags(wavefront_url, WF_HEADER, req_src_tags)
        # If any of the VCG is failed, its appended to list to display at last
        if len(failed_vcg_list) > 0:
            failed_vcgs[vco_name] = failed_vcg_list
    # Display if update is failed on any VCO
    if len(failed_vcos) > 0:
        print("Failed to update source tag for following VCO's")
        print_json_in_red(failed_vcos)
    # Display if update is failed for any VCG's
    if len(failed_vcgs) > 0:
        print("Telegraf is not running on these VCG's")
        print_json_in_red(failed_vcgs)


if __name__ == "__main__":
    main()
