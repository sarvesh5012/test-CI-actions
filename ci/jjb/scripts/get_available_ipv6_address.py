""" Get available IPv6 address from given prefix """
#!/usr/bin/env python3
import sys
import argparse
from ipaddress import IPv6Network, IPv6Address

def get_base_ipv6_prefix(prefix):
    """
    Get the first 5 nibbles (10 octets) of an IPv6 prefix.

    :param prefix: The IPv6 prefix as a string.
    :return: The base address as a string.
    """
    # Parse the IPv6 prefix
    network = IPv6Network(prefix, strict=False)

    # Get the first five nibbles (80 bits of total 128 bits)
    first_five_nibbles = str(network.network_address).split(":")[:5]
    base_address = ":".join(first_five_nibbles)

    return base_address

def generate_ipv6_with_modified_6th_nibble(prefix, nibble_num):
    """
    Generate an IPv6 address with a specific number in the 6th nibble.

    :param prefix: The IPv6 prefix as a string.
    :param nibble_num: The nibble number to use for the 6th nibble.
    :return: The generated IPv6 address as a string with /96 suffix.
    """
    base_address = get_base_ipv6_prefix(prefix)
    int_to_hex = format(nibble_num, "x")
    address = f"{base_address}:{int_to_hex}:0:0"
    ipv6 = IPv6Address(address)
    return f"{str(ipv6)}"

def get_args():
    """
    Get command-line arguments.

    :return: Parsed arguments.
    """
    parser = argparse.ArgumentParser(description="Generate IPv6 addresses with specific nibble number.")
    parser.add_argument("--ipv6-cidr", required=True, help="CIDR of IPv6 prefix")
    parser.add_argument(
        "--nibble-num", default=0, type=int, help="6th nibble number to generate IPv6"
    )
    return parser.parse_args()

def main():
    """Main function"""
    args = get_args()

    ipv6_cidr = args.ipv6_cidr
    nibble_num = args.nibble_num

    if 0 < nibble_num <= 65535:
        print(generate_ipv6_with_modified_6th_nibble(ipv6_cidr, nibble_num))
    else:
        print("Nibble number is not between 1 & 65535, Please provide within this range")
        sys.exit(1)

if __name__ == "__main__":
    main()
