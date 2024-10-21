"""
Finds the latest CWS version running in a given POP.
"""

import sys
import requests
import urllib3


class CustomRequest(requests.Session):
    """
    Custom request class to disable SSL warnings
    and raise exceptions on HTTP errors
    """

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def request(self, method, url, **kwargs):
        response = super().request(method, url, timeout=10, verify=False, **kwargs)
        response.raise_for_status()
        return response


session = CustomRequest()


def generate_api_token(vcenter_fqdn, vcenter_username, vcenter_password):
    """Generate API token for vCenter"""
    response = session.post(
        f"https://{vcenter_fqdn}/api/session",
        headers={"Content-Type": "application/json"},
        auth=(vcenter_username, vcenter_password),
    )

    if response.ok:
        return response.json()
    raise RuntimeError("API token generation failed")


def fetch_vms(vc_host, session_token_):
    """Get all VMs running vCenter for a POP"""
    vms = session.get(
        f"https://{vc_host}/api/vcenter/vm",
        headers={
            "Content-Type": "application/json",
            "vmware-api-session-id": session_token_,
        },
    )
    return vms.json()


def filter_and_extract_version(vms):
    """Produce a list of versions of CWS nodes running in the POP"""
    vers = []
    for vm_ in vms:
        name = vm_.get("name", "")
        if "inboundgateway_" in name and "_1a" in name:
            version = name.split("inboundgateway_")[1].split("_1a")[0]
            vers.append(version)
    return vers


def get_highest_version(vers):
    """Choose the highest CWS version"""
    # return sorted(vers, key=lambda x: [int(num) for num in x.split(".")])[-1]
    # convert list of strings to list of lists of digits
    as_numeric = [[int(x) for x in item.split(".")] for item in vers]

    # sort the list and get the last item
    highest = sorted(as_numeric)[-1]

    # convert back to string
    result = ".".join(str(digit) for digit in highest)

    return result


if __name__ == "__main__":
    VC_HOST = sys.argv[1]
    USER = sys.argv[2]
    PASS = sys.argv[3]

    session_token = generate_api_token(VC_HOST, USER, PASS)
    if not session_token:
        print("Failed to authenticate to vCenter.")
        sys.exit(1)

    vm_list = fetch_vms(VC_HOST, session_token)
    versions = filter_and_extract_version(vm_list)
    highest_version = get_highest_version(versions)
    print(highest_version)
