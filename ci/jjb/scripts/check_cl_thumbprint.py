"""Validate a Content Library's SSL thumbprint matches the actual SSL
thumbprint of the server it is subscribed to.

Usage:
python ci/jjb/scripts/check_cl_thumbprint.py \
--vcenter-fqdn vcenter-sjc2-qe.vmware-nonprod.net \
--library-name vsphere-content-sebu-edgeops-nonprod \
--vcenter-user 'user@vsphere.local' \
--vcenter-password 'pass'
"""

import hashlib
import socket
import ssl
import argparse
import re
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


ALLOWED_DOMAINS = ("vmware-prod.net", "vmware-nonprod.net")
session = CustomRequest()


def parse_fqdn(vcenter_fqdn, allowed_domains=ALLOWED_DOMAINS):
    """Get the hostname and domain name from FQDN.
    Returns:
        Cleaned fqdn
    Raises:
        ValueError if given FQDN is invalid.
    """

    # Strip off scheme if there is one, strip trailing slashes
    # if there are any, and ensure it is all lower case.
    vc_fqdn = re.sub(r"^https?://", "", vcenter_fqdn.rstrip("/")).lower()

    # Check the domain.
    domain = ".".join(vc_fqdn.rsplit(".", 2)[1:])
    if domain not in allowed_domains:
        raise ValueError(
            f"Domain not supported in {vc_fqdn}; " f"expected one of {ALLOWED_DOMAINS}"
        )

    return vc_fqdn


def generate_api_token(vcenter_fqdn, vcenter_username, vcenter_password):
    """Generate API token for vCenter"""
    response = session.post(
        f"https://{vcenter_fqdn}/api/session",
        headers={"Content-Type": "application/json"},
        auth=(vcenter_username, vcenter_password),
    )

    if response.status_code == 201:
        return response.json()
    raise RuntimeError("API token generation failed")


def get_cl_info(vcenter_fqdn, token, library_name):
    """Get Content Library info for a given library name"""
    lib_id_d = session.post(
        f"https://{vcenter_fqdn}/api/content/library?action=find",
        headers={
            "Content-Type": "application/json",
            "vmware-api-session-id": token,
        },
        data='{"name": "' + library_name + '", "type":"SUBSCRIBED"}',
    )
    lib_id = lib_id_d.json()[0]

    lib_info_d = session.get(
        f"https://{vcenter_fqdn}/api/content/subscribed-library/{lib_id}",
        headers={
            "Content-Type": "application/json",
            "vmware-api-session-id": token,
        },
    )
    lib_info = lib_info_d.json()
    return lib_info


def get_server_thumbprint(lib_info):
    """Get the actual SSL thumbprint of the server the Content Library is subscribed to"""
    hostname = lib_info["subscription_info"]["subscription_url"].split("/")[2]
    port = 443

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    context = ssl.create_default_context()
    secure_sock = context.wrap_socket(sock, server_hostname=hostname)
    secure_sock.connect((hostname, port))

    cert = secure_sock.getpeercert(binary_form=True)
    thumb_sha1 = hashlib.sha1(cert).hexdigest()

    return re.sub(r"(..)", r"\1:", thumb_sha1)[:-1]


def update_lib_thumbprint(vcenter_fqdn, token, lib_info, thumbprint):
    """Update the Content Library's SSL thumbprint if it does not match
    the actual SSL thumbprint of the server it is subscribed to"""

    if thumbprint == lib_info["subscription_info"]["ssl_thumbprint"]:
        print(
            f"Current stored thumbprint: {lib_info['subscription_info']['ssl_thumbprint']}"
        )
        print(f"Actual server-side thumbprint: {thumbprint}")
        print("Thumbprints match. No update needed.")
    else:
        print(
            f"Current stored thumbprint: {lib_info['subscription_info']['ssl_thumbprint']}"
        )
        print(f"Actual server thumbprint: {thumbprint}")
        print("Thumbprints do not match, updating thumbprint.")
        update_thumbprint = session.patch(
            f"https://{vcenter_fqdn}/api/content/subscribed-library/{lib_info['id']}",
            headers={
                "Content-Type": "application/json",
                "vmware-api-session-id": token,
            },
            json={
                "server_guid": str(lib_info["server_guid"]),
                "subscription_info": {"ssl_thumbprint": thumbprint},
            },
        )
        print(update_thumbprint.text)


def parse_arguments():
    """Parse arguments"""
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--vcenter-fqdn",
        help="vCenter FQDN, no https:// etc. Example: vcenter-sjc2-qe.vmware-nonprod.net",
        required=True,
    )
    parser.add_argument(
        "--library-name", help="Content Library name to check", required=True
    )
    parser.add_argument("--vcenter-user", help="vCenter username", required=True)
    parser.add_argument("--vcenter-password", help="vCenter password", required=True)

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_arguments()

    try:
        fqdn = parse_fqdn(args.vcenter_fqdn)
    except ValueError as e:
        print(f"Invalid FQDN: {e}", file=sys.stderr)
        sys.exit(1)

    access_token = generate_api_token(fqdn, args.vcenter_user, args.vcenter_password)
    cl_info = get_cl_info(fqdn, access_token, args.library_name)
    server_thumb = get_server_thumbprint(cl_info)

    update_lib_thumbprint(fqdn, access_token, cl_info, server_thumb)
