#!/usr/bin/env python3
"""Handles configuring DR on VECOs"""

import sys
import argparse
import time
import socket
from enum import Enum
from json.decoder import JSONDecodeError
import requests
from edgeops_vco.vco import Vco, Property
from edgeops_vco.vco import (
    VcoResponseError,
    VcoNoSuchUser,
    VcoRequestError,
    VcoResponseEmpty,
    VcoReplicationError,
)
from edgeops_vco.property import PropertyNotFound

LOGIN_MAX_WAIT_TIME = 120
ROLE_CHANGE_MAX_WAIT_TIME = 180


class VcoRole(Enum):
    """All possible VCO roles"""

    # Enum values remain unchanged for VECO roles
    ACTIVE = "ACTIVE"
    STANDALONE = "STANDALONE"
    STANDBY_CANDIDATE = "STANDBY_CANDIDATE"
    STANDBY = "STANDBY"
    UNCONFIGURED = "UNCONFIGURED"


# Configuration properties
PROMOTE_SLEEP_TIME = 60
primary_veco_properties = [
    {
        "name": "vco.disasterRecovery.standbyRestartStateSecs",
        "value": 300,
        "default_value": 60,
        "is_readonly": False,
        "is_password": False,
        "data_type": "NUMBER",
        "description": "period for which active will not flag error trying to contact "
        + "standby in state involving restart",
    },
    {
        "name": "vco.disasterRecovery.transientErrorToleranceSecs",
        "value": 900,
        "default_value": 900,
        "is_readonly": False,
        "is_password": False,
        "data_type": "NUMBER",
        "description": "seconds during which sync errors can be ignored on the standby/active",
    },
    {
        "name": "vco.disasterRecovery.allowedStandbySecsBehindActive",
        "value": 300,
        "default_value": 300,
        "is_readonly": False,
        "is_password": False,
        "data_type": "NUMBER",
        "description": "if standby further behind than this value, error reported",
    },
]

secondary_veco_properties = []


def check_auth_status(vco: Vco, user, password, max_time=LOGIN_MAX_WAIT_TIME):
    """Allow a given time window for auth to succeed on VCO"""
    start_time = time.time()
    print(f"Attempting to log in to {vco.fqdn}")
    while True:
        if time.time() - start_time >= max_time:
            print(f"Max time of {max_time} seconds to log in exceeded.")
            return False
        try:
            vco.operator_login_password(user, password)
        except VcoRequestError:
            print("Login failed. Retrying.")
            time.sleep(10)
            continue
        if vco.check_operator_authenticated():
            print("Login succeeded. Proceeding.")
            return True
        time.sleep(5)


def _configure_role(veco, role):
    """Set VCO to specified role"""
    current_role = veco.get_vco_role()
    print(f"Configuring {veco.name} as role {role}. Current role {current_role}")
    if role == current_role:
        print("Role already set correctly.")
        return True
    if role == VcoRole.STANDBY.value and current_role in [
        VcoRole.STANDALONE.value,
        VcoRole.UNCONFIGURED.value,
    ]:
        veco.set_vco_role_standby()
        return True
    if role == VcoRole.STANDALONE.value and current_role in [
        VcoRole.ACTIVE.value,
        VcoRole.STANDBY.value,
        VcoRole.UNCONFIGURED.value,
    ]:
        veco.set_vco_role_standalone()
        return True
    return False


def _wait_for_role_change(
    veco, role, username, password, role_change_wait_secs=ROLE_CHANGE_MAX_WAIT_TIME
):
    """Wait for VCO to get to specified role"""
    end_time = time.monotonic() + role_change_wait_secs
    while time.monotonic() < end_time:
        print(
            f"Waiting for {veco.fqdn} to become {role} for another {round(end_time - time.monotonic(),0)} seconds"
        )
        time.sleep(5)
        try:
            check_auth_status(veco, username, password)
            if veco.get_vco_role() == role:
                return True
        except (VcoRequestError, VcoResponseEmpty, VcoReplicationError) as err:
            print("Portal not responding - retrying.")
            print(err)
            continue

    print(f"Portal for {veco.fqdn} did not come up within 3 minutes.")
    return False


def _update_or_create_properties(veco, properties):
    """Upsert system properties on VCO"""
    print(f"Configuring properties on {veco.name}")
    print(properties)
    for prop in properties:
        try:
            veco_property = veco.get_system_property(prop["name"])
            veco_property.value = prop["value"]
            if veco_property.has_any_changes():
                veco.update_system_property(veco_property)
        except PropertyNotFound:
            new_property = Property.create(**prop)
            veco.create_system_property(new_property)


def _create_db_replication_user(veco, user, password):
    """(Re)create the replication user"""
    try:
        veco.get_user_id(user)
    except VcoNoSuchUser:
        print("Creating replication user")
        veco.create_operator_superuser(
            username=user, password=password, first_name="DR", last_name="Replication"
        )
    else:
        print("Recreating replication user")
        veco.delete_operator_user(user)
        veco.create_operator_superuser(
            username=user, password=password, first_name="DR", last_name="Replication"
        )


def _get_active_standby_fqdn(vco: Vco) -> str:
    """
    Get active/standby VCO name for given VCO. Will figure out based on role.
    """
    rep_status = vco.get_replication_status_raw()
    role = rep_status["role"]

    print(f"Role details for {vco.fqdn}:")
    print(role)

    if role in ("STANDALONE", "ZOMBIE"):
        return ""

    if rep_status["drState"] == "UNCONFIGURED":
        return ""

    if not rep_status["drState"] == "STANDBY_RUNNING":
        return ""

    if role == "ACTIVE":
        return rep_status["standbyList"][0]["standbyAddress"]
    elif role == "STANDBY":
        return rep_status["activeAddress"]


def _get_active_edge_count(vco: Vco) -> tuple[int, int, int]:
    """Get the number of CONNECTED, DOWN, DEGRADED edges connected to the Active VCO"""
    all_edges = vco._post("network/getNetworkEnterprises", payload={"with": ["edges"]})
    num_connected_edges = sum(
        1
        for account in all_edges
        for edge in account["edges"]
        if edge["edgeState"] == "CONNECTED"
    )
    num_down_edges = sum(
        1
        for account in all_edges
        for edge in account["edges"]
        if edge["edgeState"] == "OFFLINE"
    )
    num_degraded_edges = sum(
        1
        for account in all_edges
        for edge in account["edges"]
        if edge["edgeState"] == "DEGRADED"
    )

    return (num_connected_edges, num_down_edges, num_degraded_edges)


def _monitor_edge_count(pre_count: tuple[int, int, int], promoted_vco: Vco):
    """
    Poll the edge data on a newly promoted VCO and compare to the edge counts on the
    previously active VCO. Passing criteria are;
    - Connected edges are at least 95% of what they were on the previous, or
    - After 2 minutes, the number of connected edges are at least 90% of what they were.

    - If the number of connected edges decreases compared to the previous count 3 times,
      it's considered a failure.
    """
    timeout, interval, fail_count = 300, 5, 0
    start_time = time.time()
    conn, deg, down = 0, 0, 0
    pre_conn, pre_deg, pre_down = pre_count

    while time.time() - timeout <= start_time:
        curr_conn, curr_deg, curr_down = _get_active_edge_count(promoted_vco)
        conn, deg, down = curr_conn, curr_deg, curr_down

        if curr_conn / 0.95 >= pre_conn:
            print("Edge count within acceptable range, continuing.")
            break

        if not curr_conn >= conn:
            fail_count += 1
            if fail_count == 3:
                print(
                    "Connected Edge counts have decreased compared to previous check 3 times."
                )
                return False
            print(f"Edge counts have decreased since last check. {fail_count}/3 times.")

        time.sleep(interval)

    if conn / 0.9 < pre_conn:
        print("Connected edges are missing from the promoted VCO")
        return False

    if deg / 0.9 < pre_deg or down / 0.9 < pre_down:
        print(
            "Some previously disconnected or degraded edges are missing from the promoted VCO."
        )

    return True


def configure_vecos_and_assign_standby(
    veco,
    secondary_veco,
    replication_user,
    replication_pass,
    force,
):
    """Prepare 2 VCOs for replication"""
    primary = primary_veco_properties[:]
    secondary = secondary_veco_properties[:]

    secondary_role = secondary_veco.get_vco_role()
    print(f"Secondary VECO role: {secondary_role}")

    if secondary_role in [
        VcoRole.UNCONFIGURED.value,
        VcoRole.STANDALONE.value,
    ]:
        _create_db_replication_user(veco, replication_user, replication_pass)
        _create_db_replication_user(secondary_veco, replication_user, replication_pass)

        _update_or_create_properties(veco, primary)
        _update_or_create_properties(secondary_veco, secondary)

        if force or not secondary_veco.get_client_count():
            if _configure_role(secondary_veco, VcoRole.STANDBY.value):
                return True
            print(f"Failed to configure {secondary_veco.name} to STANDBY")
        else:
            print(f"Secondary VECO has clients: {secondary_veco.get_client_count()}")
    elif secondary_role == VcoRole.STANDBY.value:
        return True
    else:
        print(
            f"Secondary VECO is not in STANDBY or UNCONFIGURED state: {secondary_role}"
        )
    return False


def break_veco(primary_veco, secondary_veco, username, password):
    """Set VCO(s) to standalone"""
    results = {primary_veco.name: False}

    def break_(veco):
        """Inner function to handle breaking"""
        print(f"Configuring {veco.name} as {VcoRole.STANDALONE.value}")
        if _configure_role(veco, VcoRole.STANDALONE.value):
            results[veco.name] = bool(
                _wait_for_role_change(
                    veco, VcoRole.STANDALONE.value, username, password
                )
            )

    break_(primary_veco)

    if secondary_veco:
        break_(secondary_veco)

    print(results)
    for k, v in results.items():
        if v:
            print(f"{k} has entered {VcoRole.STANDALONE.value}")
        else:
            print(f"{k} failed to enter {VcoRole.STANDALONE.value}")
    return all(results.values())


def revert_veco(veco, args):
    """Revert VCO to standalone. Break preferred."""
    print(f"Reverting {veco.name} to standalone.")
    try:
        if veco.get_vco_role() in [
            VcoRole.ACTIVE.value,
            VcoRole.STANDBY.value,
            VcoRole.UNCONFIGURED.value,
        ]:
            return _configure_role(
                veco, VcoRole.STANDALONE.value
            ) and _wait_for_role_change(
                veco, VcoRole.STANDALONE.value, args.username, args.password
            )
    except VcoRequestError:
        return False


def promote_veco(veco, username, password):
    """Promote secondary VCO to active. Zombify former active."""
    role = veco.get_vco_role()
    if role in [VcoRole.STANDBY.value, VcoRole.STANDBY_CANDIDATE.value]:
        try:
            veco.promote_vco_to_active(True)
        except VcoRequestError as err:
            print(f"Error connecting to API on {veco.fqdn}")
            print(err)
            print("Continuing.")
            pass
        for _ in range(5):
            time.sleep(5)
            _wait_for_role_change(veco, "STANDALONE", username, password)
        return _wait_for_role_change(veco, "STANDALONE", username, password)
    if role in [VcoRole.STANDALONE.value]:
        print("VCO is already in standalone")
        return True

    print(f"Current role of {veco.fqdn}: {role}")
    print(f"Will not promote a VCO with role {role}")
    return False


def configure_dr(veco, secondary_veco, replication_user, replication_pass):
    """Configure replication between 2 VCOs"""
    if veco.get_vco_role() != VcoRole.ACTIVE.value:
        dr_status = secondary_veco.get_replication_status_raw()
        try:
            veco.configure_veco_for_dr(
                standby_address=dr_status["vcoIp"],
                standby_replication_address=dr_status["vcoReplicationIp"],
                standby_uuid=dr_status["vcoUuid"],
                dr_vco_user=replication_user,
                dr_vco_password=replication_pass,
            )
        except VcoResponseError as err:
            print(f"Configure DR call failed on {veco.name}: {err}")
            return False
        return True
    return False


def break_handler(veco, args):
    """Handles break logic"""
    secondary_veco = None
    if args.secondary_orchestrator:
        secondary_veco = Vco(f"{args.secondary_orchestrator}.{args.secondary_domain}")
        print(f"Breaking DR on {veco.fqdn} and {secondary_veco.fqdn}")
        print("This will set both VECOs to standalone.")
        try:
            print(f"Logging into {secondary_veco.fqdn} as {args.username}")
            check_auth_status(secondary_veco, args.username, args.password)
        except VcoRequestError as error:
            print(f"Unable to login to {secondary_veco.name} {error}")
            sys.exit(1)
    if break_veco(veco, secondary_veco, args.username, args.password):
        sys.exit(0)
    else:
        print("Failed to break DR")
        sys.exit(1)


def establish_handler(veco, args):
    """Handles establish logic"""
    if not args.secondary_orchestrator:
        print("Establish option selected, but no Secondary VECO name provided.")
        sys.exit(2)
    if not args.fqdn:
        try:
            socket.inet_aton(args.primary_ip)
            socket.inet_aton(args.secondary_ip)
        except OSError:
            print("Provided IP is invalid")
            sys.exit(6)

    secondary_veco = Vco(f"{args.secondary_orchestrator}.{args.secondary_domain}")
    try:
        check_auth_status(secondary_veco, args.username, args.password)
    except VcoRequestError as error:
        print(f"Unable to login to {secondary_veco.name} {error}")
        sys.exit(1)

    if not secondary_veco.check_operator_authenticated():
        print(f"{args.username} failed to Authenticate on {secondary_veco.name}")
        sys.exit(1)

    if not configure_vecos_and_assign_standby(
        veco,
        secondary_veco,
        args.replication_user,
        args.replication_password,
        args.force,
    ):
        print("DR Preparation failed, exiting")
        sys.exit(1)

    end_time = time.monotonic() + 300
    while time.monotonic() < end_time:
        time.sleep(5)
        try:
            check_auth_status(secondary_veco, args.username, args.password)
            if secondary_veco.get_vco_role() == VcoRole.STANDBY.value:
                break
        except (
            JSONDecodeError,
            VcoResponseEmpty,
            VcoReplicationError,
            VcoRequestError,
        ):
            print(f"Waiting for {secondary_veco.fqdn} to become standby.")
    else:
        print(f"Configuration of {secondary_veco.name} failed")
        sys.exit(3)

    if configure_dr(
        veco, secondary_veco, args.replication_user, args.replication_password
    ):
        sys.exit(0)
    else:
        print("Replication Setup failed. Reverting VECOs")
        revert_veco(veco, args)
        revert_veco(secondary_veco, args)
        sys.exit(3)


def revert_handler(veco, args):
    """Handles revert logic"""
    if revert_veco(veco, args):
        print("Completed")
        sys.exit(0)
    else:
        print("Unable to complete Stand Alone call, exiting")
        sys.exit(4)


def promote_handler(veco: Vco, args):
    """Handles promote logic"""
    current_active_fqdn = _get_active_standby_fqdn(veco)
    if not current_active_fqdn:
        print(f"DR is not correctly configured on {veco.fqdn}")
        sys.exit(1)
    else:
        current_active = Vco(current_active_fqdn)

    if not (current_active, args.username, args.password):
        print(f"Cannot log into current primary VCO for {veco.fqdn}")
    current_active.operator_login_password(args.username, args.password)
    pre_count = _get_active_edge_count(current_active)

    if promote_veco(veco, args.username, args.password):
        # Wait timer before proceeding with checks
        time.sleep(PROMOTE_SLEEP_TIME)
        if _monitor_edge_count(pre_count, veco):
            print(
                "Promotion completed and edge counts are passing. Please check edges again"
            )
            sys.exit(0)
        else:
            print("Edge counts aren't the same as before the cutover, please review")
            sys.exit(1)
    else:
        print("Unable to complete promotion action, exiting")
        sys.exit(5)


def parse_args():
    """Retrieve arguments"""
    parser = argparse.ArgumentParser(
        description="establish, break, revert or promote actions on VECOs"
    )
    parser.add_argument(
        "-o", "--orchestrator", required=True, type=str.strip, help="Primary VECO"
    )
    parser.add_argument(
        "-s",
        "--secondary-orchestrator",
        dest="secondary_orchestrator",
        type=str.strip,
        help="Secondary VECO",
    )
    parser.add_argument(
        "-d",
        "--domain",
        required=True,
        type=str.lower,
        help="Domain for the Primary VECO",
    )
    parser.add_argument(
        "-u",
        "--username",
        required=True,
        type=str.strip,
        help="Service account username",
    )
    parser.add_argument(
        "-p", "--password", required=True, type=str, help="Service account password"
    )
    parser.add_argument(
        "-a",
        "--action",
        required=True,
        choices=["break", "establish", "revert", "promote"],
        type=str.lower,
        help="Action",
    )
    parser.add_argument(
        "--fqdn", action="store_true", help="Set replication address to FQDN value"
    )
    parser.add_argument("--primary-ip", type=str, help="IP address for Primary VECO")
    parser.add_argument(
        "--secondary-ip", type=str, help="IP address for Secondary VECO"
    )
    parser.add_argument(
        "--secondary-domain", type=str.strip, help="Domain for the Secondary VECO"
    )
    parser.add_argument(
        "--replication_user",
        type=str.strip,
        help="VECO Operator for DR Database operations",
    )
    parser.add_argument(
        "--replication_password",
        type=str,
        help="VECO Operator user password for DR Database operations",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force establish operation even if Edges are detected on Secondary VECO",
    )
    args = parser.parse_args()

    return args


def main():
    """Main function"""
    args = parse_args()

    veco = Vco(f"{args.orchestrator}.{args.domain}")
    print(f"Attempting {args.action} on {veco.fqdn}")
    try:
        print(f"Logging into {veco.fqdn} as {args.username}")
        check_auth_status(veco, args.username, args.password)
    except VcoRequestError as error:
        print(f"Unable to login to {veco.name} {error}")
        sys.exit(1)
    if not veco.check_operator_authenticated():
        print(f"{args.username} failed to Authenticate on {veco.name}")
        sys.exit(1)

    if args.action == "break":
        break_handler(veco, args)

    if args.action == "establish":
        establish_handler(veco, args)

    if args.action == "revert":
        revert_handler(veco, args)

    if args.action == "promote":
        promote_handler(veco, args)


if __name__ == "__main__":
    main()
