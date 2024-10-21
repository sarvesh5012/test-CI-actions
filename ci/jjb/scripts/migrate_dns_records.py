#!/usr/bin/env python3
"""
Script that handles migrating DNS records for AWS VCOs to their corresponding
VCO in GCP.

Args:
    aws-vco (required): AWS VCO shortname i.e. vco58-usvi1
    active-gcp-vco (required): Active GCP VCO shortname
    standby-gcp-vco (optional): Standby GCP VCO shortname
    aws-dr-vco (optional): Standby AWS VCO shortname
    zone-id (optional): Cloudflare DNS Zone ID
    auth-key (optional): Cloudflare Auth Key
"""

import argparse
import os
from itertools import chain
from cloudflare_api import CloudflareHandler


def repoint_handler(
    dns_mgr: CloudflareHandler,
    active_gcp_vco: str,
    aws_dr_vco: str = "",
    aws_domain: str = "",
):
    """
    Find any existing records for the AWS DR and retire them.
    Then CNAME that FQDN to the Active GCP FQDN.
    """
    if not aws_dr_vco:
        print("AWS DR not specified. No DNS to repoint.")
        print("Complete manually if necessary.")
        return

    aws_dr_fqdn = f"{aws_dr_vco}.{aws_domain}"

    records = list(
        chain(
            dns_mgr.get_dns_records(record_name=aws_dr_fqdn),
            dns_mgr.get_dns_records(content=aws_dr_fqdn),
        )
    )

    for record in records:
        dns_mgr.update_dns_record(
            record_id=record["id"],
            data={
                "name": f"old-{record['name']}",
                "content": record["content"],
                "type": record["type"],
            },
        )

    dns_mgr.create_dns_record(
        data={
            "name": aws_dr_fqdn,
            "content": f"{active_gcp_vco}.velocloud.net",
            "type": "CNAME",
        }
    )


def cutover_handler(
    dns_mgr: CloudflareHandler,
    active_gcp_vco: str,
    standby_gcp_vco: str = "",
    aws_vco: str = "",
    aws_dr_vco: str = "",
    aws_domain: str = "",
):
    """Migrates, retires, and updates A/AAAA/CNAME records for GCP VCOs"""
    active_gcp_fqdn = f"{active_gcp_vco}.velocloud.net"
    active_gcp_cname = f"{active_gcp_vco.split('-')[0]}.velocloud.net"
    aws_fqdn = f"{aws_vco}.{aws_domain}"

    standby_gcp_fqdn = ""
    standby_gcp_cname = ""
    aws_dr_fqdn = ""

    if standby_gcp_vco:
        standby_gcp_fqdn = f"{standby_gcp_vco}.velocloud.net"
        standby_gcp_cname = f"{standby_gcp_vco.split('-')[0]}-standby.velocloud.net"
    if aws_dr_vco:
        aws_dr_fqdn = f"{aws_dr_vco}.{aws_domain}"
        recs = dns_mgr.get_dns_records(
            record_name=aws_dr_fqdn, content=active_gcp_fqdn, record_type="CNAME"
        )
        for rec in recs:
            dns_mgr.update_dns_record(
                record_id=rec["id"],
                data={
                    "name": f"old-{rec['name']}",
                    "content": rec["content"],
                    "type": rec["type"],
                },
            )

    print("Configuring Standby DNS records")
    if standby_gcp_vco and aws_dr_vco:
        dns_mgr.migrate_records(
            url_to_repoint=aws_dr_fqdn,
            new_base_url=standby_gcp_fqdn,
            new_cname=standby_gcp_cname,
        )

    elif aws_dr_vco and not standby_gcp_vco:
        print(f"Retiring AWS DR records for {aws_dr_fqdn}")
        dns_mgr.retire_aws_records(aws_dr_fqdn)

    elif standby_gcp_vco and not aws_dr_vco:
        print(
            f"Creating standby CNAME record: {standby_gcp_cname} -> {standby_gcp_fqdn}"
        )
        dns_mgr.create_dns_record(
            data={
                "content": standby_gcp_fqdn,
                "name": standby_gcp_cname,
                "type": "CNAME",
            }
        )

    print("Configuring Active DNS records")
    dns_mgr.migrate_records(
        url_to_repoint=aws_fqdn,
        new_base_url=active_gcp_fqdn,
        new_cname=active_gcp_cname,
    )


def parse_args() -> argparse.Namespace:
    """Parse args"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--action",
        required=True,
        help="Action to perform i.e. repoint/cutover",
        choices=["repoint", "cutover"],
    )
    parser.add_argument(
        "--aws-vco",
        required=True,
        help="AWS VCO Records to migrate",
    )
    parser.add_argument(
        "--active-gcp-vco",
        required=True,
        help="Active GCP VCO name",
    )
    parser.add_argument(
        "--standby-gcp-vco",
        required=False,
        default="",
        help="Standby GCP VCO Name",
    )
    parser.add_argument(
        "--aws-dr-vco", required=False, default="", help="AWS DR VCO Name"
    )
    parser.add_argument("--aws-domain", required=False)
    parser.add_argument("--zone-id", required=False, default="")
    parser.add_argument("--auth-key", required=False, default="")
    parser.add_argument("--cloudflare-email", required=False, default="")
    args = parser.parse_args()
    return args


def start():
    """Main function"""
    args = parse_args()

    action = args.action.lower()

    aws_vco = args.aws_vco.lower()
    active_gcp_vco = args.active_gcp_vco.lower()

    standby_gcp_vco = args.standby_gcp_vco.lower()
    aws_dr_vco = args.aws_dr_vco.lower()

    aws_domain = args.aws_domain

    # need to handle empty values here for some cases

    zone_id = os.getenv("CLOUDFLARE_ZONE_ID", args.zone_id)
    auth_key = os.getenv("CLOUDFLARE_API_TOKEN", args.auth_key)
    email = "velo-ops-staff@vmware.com"

    if not all([zone_id, auth_key, email]):
        raise ValueError(
            "Missing required Cloudflare configuration. "
            "Please ensure CLOUDFLARE_ZONE_ID, CLOUDFLARE_API_TOKEN, "
            "and CLOUDFLARE_EMAIL are set."
        )

    dns_manager = CloudflareHandler(email, auth_key, zone_id)

    if action == "repoint":
        repoint_handler(dns_manager, active_gcp_vco, aws_dr_vco, aws_domain)

    if action == "cutover":
        cutover_handler(
            dns_manager,
            active_gcp_vco,
            standby_gcp_vco,
            aws_vco,
            aws_dr_vco,
            aws_domain,
        )


if __name__ == "__main__":
    start()
