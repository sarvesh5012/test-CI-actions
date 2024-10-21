#!/usr/bin/env python3
"""Update network-related sysprop."""

import os
import sys
import time
import socket
import argparse
from edgeops_vco.vco import Vco, Property, VcoRequestError
from edgeops_vco.property import PropertyNotFound, PropertyFieldNotPresent


def check_auth_status(vco: Vco, user, password, max_time=300) -> bool:
    """Allow a given time window for auth to succeed on VCO"""
    start_time = time.time()
    while True:
        try:
            # Attempt login
            vco.operator_login_password(user, password)
        except VcoRequestError as error:
            # Handle DNS error
            if "nodename nor servname" in error.args[0]:
                print(f"Unable to login to {vco.name}: {error}")
                return False
            time.sleep(10)
            continue

        # Return success auth'd VCO object 
        if vco.check_operator_authenticated():
            return True
        # Fail if login window expires
        if time.time() - start_time >= max_time:
            return False
        time.sleep(5)


def check_vco_in_standby(vco: Vco) -> bool:
    """Check if VCO is standby"""
    role = vco.get_vco_role().lower()
    return role == "standby"


def update_network_replication_address_sysprop(vco: Vco, priv_ip: str = ""):
    """Update the network-related sysprops."""
    if check_vco_in_standby(vco):
        print(f"{vco.fqdn} is in standby mode. Exiting.")
        return

    nra: Property = vco.get_system_property("network.replication.address")
    print(f"network.replication.address = {nra.value}")
    if priv_ip:
        try:
            socket.inet_aton(priv_ip)
        except socket.error:
            raise ValueError(f"Provided ip {priv_ip} is not valid")
        nra.value = priv_ip
        print(f"network.replication.address = {nra.value}")
        vco.update_system_property(nra)
    else:
        nra.value = vco.fqdn
        print(f"network.replication.address = {nra.value}")
        vco.update_system_property(nra)


def update_network_public_address_sysprop(vco:  Vco, aws_vco: str):
    """Update the network.public.address to the AWS FQDN"""
    public_address = vco.get_system_property("network.public.address")
    public_address.value = f"{aws_vco}.velocloud.net"
    vco.update_system_property(public_address)


def update_network_websocket_address_sysprop(vco: Vco, aws_vco: str):
    """
    Update the "network.portal.websocket.address" system properties after promoting.
    """
    names = [aws_vco, vco.name, vco.name.split("-")[0]]
    fqdns = [f"{name}.velocloud.net" for name in names]

    websocket_addresses = vco.get_system_property("network.portal.websocket.address")

    # If there is an existing string value for the websocket, add it to the
    # list and recreate the property
    if websocket_addresses.fields['dataType'] == "STRING":
        if websocket_addresses.value not in fqdns:
            fqdns += websocket_addresses.value

        websocket_fields_json = websocket_addresses.fields

        for k in ("id", "created", "modified", "etag"):
            websocket_fields_json.pop(k)

        websocket_fields_json["dataType"] = "JSON"
        websocket_fields_json["value"] = fqdns
        vco._post("systemProperty/deleteSystemProperty", payload={"id": websocket_addresses.fields["id"]})
        websocket_addresses_replace = Property(websocket_fields_json)
        vco.create_system_property(websocket_addresses_replace)

    # If there is an existing JSON value for the websocket, add it to the
    # list and recreate the property
    elif websocket_addresses.fields["dataType"] == "JSON":
        if sorted(websocket_addresses.value) == sorted(fqdns):
            print("Websocket URLs are up to date")
        else:
            for fqdn in fqdns:
                if fqdn not in websocket_addresses.value:
                    websocket_addresses.value.append(fqdn)
            vco.update_system_property(websocket_addresses)

    # If there is no websocket address then create one
    elif not websocket_addresses:
        websocket_addresses = Property(
            {
                'name': 'network.portal.websocket.address',
                'value': fqdns,
                'defaultValue': None,
                'isReadOnly': False,
                'isPassword': False,
                'dataType': 'JSON',
                'description': 'address of the realtime server for websocket requests from the browser',
            }
        )
        vco.create_system_property(websocket_addresses)


def update_mail_properties(vco, sendgrid_api_key):
    """Update the mail and SMTP related system properties."""
    if check_vco_in_standby(vco):
        print(f"{vco.fqdn} is in standby mode. Exiting.")
        return

    # Define properties and their new values
    properties_to_update = {
        "mail.from": "no-reply@velocloud.net",
        "mail.replyTo": "no-reply@velocloud.net",
        "mail.support.from": "support@velocloud.net",
        "mail.smtp.host": "smtp.sendgrid.net",
        "mail.smtp.port": 25,
        "mail.smtp.secureConnection": False,
        "mail.smtp.auth.user": "apikey",
        "mail.smtp.auth.pass": sendgrid_api_key,
    }

    # Update each property with the new value
    for prop_name, new_value in properties_to_update.items():
        prop: Property = vco.get_system_property(prop_name)
        print(f"Current value of {prop_name} = {prop.value}")

        # Check if read-only flag is set and VCO is new enough to support
        # read-only properties
        try:
            handle_readonly = prop.is_read_only
        except PropertyFieldNotPresent:
            handle_readonly = False

        if handle_readonly:
            prop.is_read_only = False
            vco.update_system_property(prop)

        prop.value = new_value
        print(f"New value of {prop_name} = {prop.value}")

        if prop.name == "mail.smtp.auth.pass":
            prop.is_password = True

        vco.update_system_property(prop)

        if handle_readonly:
            prop.is_read_only = True
            vco.update_system_property(prop)


def update_ignore_version_property(vco: Vco):
    """Allow active & standby to have different builds"""
    prop = {
        "name": "vco.disasterRecovery.allowDifferentVersionVcoForStandby",
        "value": True,
        "default_value": True,
        "is_readonly": False,
        "is_password": False,
        "data_type": "BOOLEAN",
        "description": "Allow different version/build for standby",
    }
    try:
        vco_property = vco.get_system_property(prop["name"])
        vco_property.value = prop["value"]
        if vco_property.has_any_changes():
            vco.update_system_property(vco_property)
    except PropertyNotFound:
        new_property = Property.create(**prop)
        vco.create_system_property(new_property)


def check_edge_gw_counts(vco: Vco):
    """
    Check replication is configured and edge/gateway counts match on active & standby
    """
    replication_status = vco.get_replication_status_raw()
    state = replication_status["drState"]
    counts = replication_status["clientCount"]

    if state == "STANDBY_PROMOTED":
        print("Standby VCO has already been promoted. Continuing.")
        return True

    if state != "STANDBY_RUNNING":
        print("DR state is not STANDBY_RUNNING.")
        return False

    edge_match = counts["currentActiveEdgeCount"] == counts["currentStandbyEdgeCount"]
    gateway_match = (
        counts["currentActiveGatewayCount"] == counts["currentStandbyGatewayCount"]
    )

    if edge_match and gateway_match:
        print("Edge and gateway counts match.")
        return True
    if not edge_match:
        print("Active and standby edge counts do not match.")
    if not gateway_match:
        print("Active and standby gateway counts do not match.")
    return False


def parse_args() -> argparse.Namespace:
    """Parse args"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--vco", type=str.strip, required=True)
    parser.add_argument("--domain", type=str.strip, required=True)
    parser.add_argument("--user", type=str.strip, default=os.getenv("VCO_API_USER"))
    parser.add_argument("--password", default=os.getenv("VCO_API_PASSWORD"))
    parser.add_argument("--ip", type=str.strip, required=False, default="")
    parser.add_argument("--sendgrid-api-key", required=False, default="")
    parser.add_argument("--aws-vco", help="AWS Old FQDN", required=False, default="")
    parser.add_argument(
        "--check-counts", action="store_true", help="Check edge and gateway counts"
    )
    parser.add_argument("--vco-version", action="store_true", help="Get VCO build")
    parser.add_argument(
        "--ignore-version",
        action="store_true",
        help="Ignore build number difference between active & standby",
    )
    args = parser.parse_args()
    return args


def start():
    """Main function"""
    args = parse_args()
    vco_name = args.vco
    domain = args.domain
    fqdn = f"{args.vco}.{args.domain}"
    user = args.user
    password = args.password
    priv_ip = args.ip
    sendgrid_api_key = args.sendgrid_api_key
    aws_vco = args.aws_vco

    vco = Vco(fqdn)

    # Log into GCP VCO 
    if not check_auth_status(vco, user, password):
        print("VCO authentication not successful. Exiting.")
        sys.exit(1)

    # Check edge counts between active/standby
    if args.check_counts:
        if check_edge_gw_counts(vco):
            sys.exit(0)
        else:
            sys.exit(1)

    # Get the VCO version
    if args.vco_version:
        if check_vco_in_standby(vco):
            print("standby")
            sys.exit(0)
        print(vco.get_system_version().version)
        sys.exit(0)

    # Ignore version check when configuring replication
    if args.ignore_version:
        update_ignore_version_property(vco)
        sys.exit(0)

    # Set nra to private IP if specified
    if priv_ip != "":
        update_network_replication_address_sysprop(vco, priv_ip)
    
    # Set nra to fqdn and update websocket address
    if (
        priv_ip == ""
        and sendgrid_api_key == ""
        and not (args.check_counts or args.vco_version or args.ignore_version)
    ):
        update_network_replication_address_sysprop(vco)
        if aws_vco != "":
            update_network_public_address_sysprop(vco, aws_vco)
            update_network_websocket_address_sysprop(vco, aws_vco)

    # Update sendgrid settings
    if sendgrid_api_key != "":
        update_mail_properties(vco, sendgrid_api_key)


if __name__ == "__main__":
    start()
