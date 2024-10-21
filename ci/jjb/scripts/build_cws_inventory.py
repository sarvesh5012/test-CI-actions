"""Create a CWS inventory file for a POP.
"""
import json
import os
import argparse
import jinja2

# Constants
CURRENT_DIR = os.path.dirname(os.path.realpath(__file__))
TEMPLATE_DIR = f"{CURRENT_DIR}/templates/cws_templates"
CWS_VM_NAME_PREFIXES = ("helper", "surrogate", "proxy", "inboundgateway")
NONPROD_POPS = ["sjc2-qe", "sjc2"]


# Utility functions
def add_slash_to_path(path):
    """ Append slash delimiter to path """
    return path if path.endswith("/") else path + "/"


def validate_folder_path(path):
    """ Validate folder path """
    return os.path.isdir(path)


# Main functional parts
def cws_site_from_site(site_name):
    """ Return site name """
    if site_name == "sjc2q":
        site_name = "sjc2-qe"
    return site_name


def populate_inventory_file(vm_node_type, node_count, version, node_key,
                            cluster_network, path, site, env):
    """ Populate inventory """
    template_loader = jinja2.FileSystemLoader(searchpath=TEMPLATE_DIR)
    template_env = jinja2.Environment(
        loader=template_loader,
        autoescape=True,
        keep_trailing_newline=True
    )
    template = template_env.get_template(f"{vm_node_type}.j2")

    cws_site = cws_site_from_site(site)

    template_params = {
        "pop_name": cws_site,
        "node_type": vm_node_type,
        "cws_ver": version,
        "node_count": node_count,
        "key_id": node_key,
        "cluster_network": cluster_network,
        "tld": "com" if env == "prod" else "io",
    }

    output = template.render(**template_params)
    output_filename = f"{site}_{version}_{vm_node_type}.yml"

    path = add_slash_to_path(path)

    if not validate_folder_path(path):
        raise FileNotFoundError(f"{path} is not a valid path")

    output_path = f"{path}{output_filename}"

    if os.path.isfile(output_path):
        os.remove(output_path)

    with open(output_path, "w", encoding="UTF-8") as file:
        file.write(output)


def build_inventory_files(site, path, target_version, cluster_network):
    """ Build inventory """
    cws_site = cws_site_from_site(site)
    env = "nonprod" if cws_site in NONPROD_POPS else "prod"

    with open(f"{TEMPLATE_DIR}/keys.json", encoding="UTF-8") as file:
        keys = json.load(file)

    path = add_slash_to_path(path)
    if not validate_folder_path(path):
        raise FileNotFoundError(f"{path} is not a valid path")

    for node_type in CWS_VM_NAME_PREFIXES:
        version = target_version

        type_count = {
            "helper": keys[cws_site]["h_count"],
            "inboundgateway": keys[cws_site]["i_count"],
            "proxy": keys[cws_site]["p_count"],
            "surrogate": keys[cws_site]["s_count"],
        }
        node_count = type_count[node_type]
        try:
            node_key = keys[cws_site][node_type]
        except KeyError as exc:
            raise KeyError(
                f"Please update pops.json with the CWS keys for {cws_site}"
            ) from exc

        populate_inventory_file(node_type, node_count, version, node_key,
                                cluster_network, path, site, env)


# Argparse setup
def main():
    """ Build CWS Inventory"""
    parser = argparse.ArgumentParser(
        description="Create a CWS inventory file for a POP")
    parser.add_argument("-p", "--pop", help="POP name", required=True)
    parser.add_argument("-f", "--path", help="Path to write inventory files to",
                        required=True)
    parser.add_argument("-v", "--version", help="Target CWS version", required=True)
    parser.add_argument("-c", "--cluster-network", help="Cluster network, a or b",
                        required=False, default=None)

    args = parser.parse_args()
    build_inventory_files(args.pop, args.path, args.version, args.cluster_network)


if __name__ == "__main__":
    main()
