#!/usr/bin/env python

"""
Script to test if VM is reach at SSH port through teleport proxy server
"""

import os
import sys
import time
import argparse
import subprocess
import socket
import textwrap


def teleport_connection_check(hostname, ssh_config, svc_account):
    """Check if a host is reachable via teleport and SSH works

    Args:
        hostname (str) : The name of the host to test connectivity for
        ssh_config (str) : Path to an existing SSH config file with keys/hosts defined
        svc_account (str) : Name of service account used by Teleport
    """
    with open("teleport_error.log", "a", encoding="utf-8") as file:
        # Setting stderr to log file
        sys.stdout = file

        ssh_exec_cmd = [
            "ssh",
            "-vvv",
            "-F",
            ssh_config,
            "-l",
            svc_account,
            hostname,
            "echo",
            "'SSH connection successful'",
        ]

        try:
            result = subprocess.run(
                ssh_exec_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
            print(f"Connection successful to {hostname}:\n{result.stdout.strip()}")

        except subprocess.CalledProcessError as err:
            print(f"Connection failed to {hostname}:\n{err.stderr.strip()}")
            return False
        except Exception as err:
            print(f"Exception occurred: {err}")
            return False
        else:
            return True
        finally:
            sys.stdout = sys.__stdout__


def main():
    """
    Test Connectivity to the VM host through teleport
    """
    # ============  Argument parser ============ #

    parser = argparse.ArgumentParser(description="Check teleport connectivity")

    parser.add_argument(
        "--vm-name",
        action="store",
        type=str,
        required=True,
        help="Short Name of the VM, Example: vco5101-usor1",
    )
    parser.add_argument(
        "--domain",
        action="store",
        type=str,
        required=False,
        default="vmware-test.net",
        help="Domain Name of the vm. Required for VCO",
    )
    parser.add_argument(
        "--teleport-proxy",
        action="store",
        type=str,
        required=False,
        default="teleport-nlb.vmware-nonprod.net",
        help="Full FQDN of teleport proxy server",
    )
    parser.add_argument(
        "--svc-account",
        action="store",
        type=str,
        required=False,
        default="svc-jenkins",
        help="Name of the Teleport Service account",
    )
    parser.add_argument(
        "--private-key-file",
        action="store",
        type=str,
        required=True,
        help="Path of the private key file used to connect",
    )
    parser.add_argument(
        "--wait-for",
        action="store",
        type=int,
        required=False,
        default=30,
        help="Total time to wait to test teleport connectivity in minutes",
    )

    args = parser.parse_args()

    vm_name = args.vm_name
    domain = args.domain
    vco_fqdn = f"{vm_name}.{domain}" if "vco" in vm_name else None
    wait_for = args.wait_for
    teleport_proxy_server = args.teleport_proxy
    teleport_svc_account = args.svc_account
    teleport_proxy_port = 3023
    teleport_ssh_port = 3022
    teleport_cert_dir = os.getcwd()
    ssh_conf_file = f"{teleport_cert_dir}/ssh_config"
    teleport_svc_account_keyfile = args.private_key_file

    # Calculate the end time
    wait_time_for_portal = time.time() + (wait_for * 60)
    vco_portal_status = False

    ssh_config_file_content = textwrap.dedent(
        f"""\
        # Teleport SSH Config
        Host *
            PubkeyAcceptedKeyTypes +ssh-rsa-cert-v01@openssh.com
            HostKeyAlgorithms rsa-sha2-512-cert-v01@openssh.com,rsa-sha2-256-cert-v01@openssh.com,ssh-rsa-cert-v01@openssh.com

        # Flags for all hosts except the proxy
        Host {vm_name} !{teleport_proxy_server}
            User {teleport_svc_account}
            Port {teleport_ssh_port}
            UserKnownHostsFile /dev/null
            StrictHostKeyChecking no
            IdentityFile {teleport_svc_account_keyfile}
            CertificateFile {teleport_svc_account_keyfile}-cert.pub
            PubkeyAcceptedKeyTypes +ssh-rsa-cert-v01@openssh.com
            ProxyCommand ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -i {teleport_svc_account_keyfile} -l {teleport_svc_account} {teleport_proxy_server} -p {teleport_proxy_port} -W %h:%p
    """
    )

    with open(ssh_conf_file, "w+", encoding="utf-8") as file:
        file.write(ssh_config_file_content)

    ####################################
    # Check if VCO portal services is UP.

    if "vco" in vm_name.lower():
        while time.time() < wait_time_for_portal:
            try:
                socket.getaddrinfo(vco_fqdn, 443)
                vco_portal_status = True
                break
            except socket.gaierror:
                time.sleep(30)

        if not vco_portal_status:
            print("VCO Portal service is not up yet. Check Manually")
            sys.exit(1)
    ####################################
    # Check if teleport proxy is reachable

    try:
        socket.getaddrinfo(teleport_proxy_server, teleport_proxy_port)
    except socket.gaierror:
        print(
            f"Teleport proxy {teleport_proxy_server}:{teleport_proxy_port} connection failure"
        )
        sys.exit(1)

    ####################################
    # Set wait time to test teleport connection
    start_time = time.time()
    teleport_wait_time = start_time + (wait_for * 60)
    teleport_connect_status = False

    ####################################
    # Test connection
    # Run the loop for the specified duration
    while time.time() < teleport_wait_time:
        conn_status = teleport_connection_check(
            vm_name, ssh_conf_file, teleport_svc_account
        )
        if conn_status is False or conn_status is None:
            time.sleep(60)
        else:
            teleport_connect_status = True
            break

    print(teleport_connect_status)


if __name__ == "__main__":
    main()
