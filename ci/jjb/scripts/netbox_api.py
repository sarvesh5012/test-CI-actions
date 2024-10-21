#!/usr/bin/env python3

"""netbox_api is used to make changes in Netbox from Jenkins jobs."""

import os
import ast
import sys
import argparse
import json
import pynetbox

MY_PATH = os.path.dirname(os.path.realpath(__file__))

# Keys that are always copied from default into TF_VARS, local_context and custom fields during
# updates.
VCG_ALWAYS_TF_VARS = frozenset(
    (
        "salt_master_fqdn",
        "salt_master_finger",
        "salt_env",
    )
)
VCG_ALWAYS_LOCAL_CONTEXT = frozenset()
VCG_ALWAYS_CUSTOM_FIELDS = frozenset()


def netbox_action(action, netbox, vm_name, site_name, role_name, addn_args):
    """Handle requested action and dispatch the appropriate method."""
    cases = {
        "get": show_netbox_vm,
        "delete": delete_netbox_vm,
        "update": update_netbox_vm,
        "upgrade": upgrade_netbox_vm,
        "destroy": set_netbox_vm_as_destroy,
        "create": create_netbox_vm,
        "get_vm_tf_vars": get_vm_tf_vars,
    }

    # Retrieve the case function based on the action
    case_function = cases.get(action, default_switch)

    # Call the case function with the provided arguments
    case_function(netbox, vm_name, site_name, role_name, addn_args)


#
# ACTION HANDLERS
#


def default_switch(*_):
    """Default switch case when incorrect action is passed"""
    print("Invalid action -r provided")


def get_vm_tf_vars(netbox, vm_name, site_name, *_):
    """Netbox vm get action"""
    vm_info = get_vm(netbox, vm_name, site_name)
    if vm_info is not None:
        for item in vm_info:
            if item[0] == "local_context_data":
                if item[1] is not None and "TF_VAR" in item[1]:
                    print(item[1]["TF_VAR"])
                    return
        print(f"No Terraform variables in VM {vm_name} record")
    else:
        print(f"Get: virtual machine {vm_name} doesn't exist in Netbox.")
        sys.exit(1)


def show_netbox_vm(netbox, vm_name, site_name, *_):
    """Netbox vm get action"""
    vm_info = get_vm(netbox, vm_name, site_name)
    if vm_info is not None:
        for item in vm_info:
            print(item)
    else:
        print(f"Get: virtual machine {vm_name} doesn't exist in Netbox.")


def delete_netbox_vm(netbox, vm_name, site_name, *_):
    """Netbox vm delete action"""
    vm_info = get_vm(netbox, vm_name, site_name)
    if vm_info is not None:
        delete_vm(netbox, vm_info)
        print(f"{vm_name} deleted successfully from netbox")
    else:
        print(f"Delete: virtual machine {vm_name} doesn't exist in Netbox.")


def update_netbox_vm(netbox, vm_name, site_name, _, addn_args):
    """Netbox vm update action"""
    vm_info = get_vm(netbox, vm_name, site_name)
    if vm_info is not None:
        update_vm(netbox, vm_info, addn_args)
    else:
        print(f"Update: virtual machine {vm_name} doesn't exist in Netbox")


def upgrade_netbox_vm(netbox, vm_name, site_name, _, addn_args):
    """Netbox upgrade vcg vm action"""
    vm_info = get_vm(netbox, vm_name, site_name)
    if vm_info is not None:
        update_vm(netbox, vm_info, addn_args, omit_action=False)
    else:
        print(f"Upgrade: virtual machine {vm_name} doesn't exist in Netbox")


def set_netbox_vm_as_destroy(netbox, vm_name, site_name, role_name, *_):
    """Netbox vm destroy action"""
    vm_info = get_vm(netbox, vm_name, site_name)
    if role_name == "vcg" and vm_info is not None:
        set_destroy(netbox, vm_info)
        print(f"{vm_name} set to destroy in netbox")
    else:
        print("Setting VM as destroy in local context data is applicable only to VCG's")


def create_netbox_vm(netbox, vm_name, site_name, role_name, addn_args):
    """Netbox vm create action"""
    vm_info = get_vm(netbox, vm_name, site_name)
    if vm_info is not None:
        print(f"Virtual Machine {vm_name} already exists")
        sys.exit(-1)

    # Load template data
    data = _load_templates(vm_name, site_name, role_name, addn_args)
    if role_name.lower() == "vcg":
        data["local_context"]["TF_VAR"]["vcenter_vcg_name"] += "-0"
        put_vm(netbox, vm_name, site_name, role_name, data)

    elif role_name.lower() in ["vco", "vco-dr"]:
        site_info = netbox.dcim.sites.get(name=site_name)
        cluster_info = netbox.virtualization.clusters.get(name=site_name)
        if site_info is None or cluster_info is None:
            print(f"No such site {site_name} or no cluster found for site.")
            sys.exit(1)
        put_vm(netbox, vm_name, site_name, role_name, data)

    else:
        print("Only VM's with roles vcg & vcg are managed by this script")
    # Now go back and check that the record exists.
    if get_vm(netbox, vm_name, site_name) is not None:
        print(f"Succeeded creating Virtual Machine {vm_name} record in Netbox.")
    else:
        print(f"Failed to create Virtual Machine {vm_name} record in Netbox.")


#
# BUILD AND MODIFY DATA
#


# Import the POPs data.
def load_pops_data(path):
    """Obtain data on all the POPs known.

    Returns:
        A dict tree keyed on POP name, of this form:
        ```json
        {
            "atl3": {
                "datacenter_name": "ATL3",
                "mgmt_subnet": "10.49.148.0",
                "mgmt_subnet_mask": "255.255.254.0",
                "vcmp_subnet": "159.100.171.0",
                "vcmp_subnet_mask": "255.255.255.128",
                "vcmp_ip6_subnet": "2605:a7c0:a:301",
                "vcenter_hostname": "vcenter-atl3.vmware-prod.net",
                "wavefront_proxy_name": "wf-proxy-atl3.vmware-prod.net",
                "salt_master_fqdn": "salt-master-lb.vmware-nonprod.net",
                "salt_master_finger": "54:38:7f:99:10:68:01:3b:16: [...]",
                "salt_env": "sase_test"
            },
        }
        ```
    """
    with open(f"{path}/pops.json", encoding="utf-8") as file:
        pops_data = file.read()
    return json.loads(pops_data)


def _load_templates(vm_name, site_name, role_name, addn_args):
    """Load the templates for vm. Applicable only for vco & vcg instances

    TODO if we ever had more than one cluster per site, would it be the name of the _cluster_ or
         the _site_ that we'd want to use for `site_name`.

    Args:
        vm_name: Name of the VM
        site_name: Site or cluster Name or POP region
        role_name: role of the VM
        addn_args: Additional args required to manage vm object

    Return:
        template: Generated json template
    """
    if role_name.lower() == "vcg":
        # Set up the context data to send to Netbox for the VCG creation.
        try:
            pop_site_data = load_pops_data(MY_PATH)[site_name]
        except KeyError as error:
            raise KeyError(f"No such site {site_name}") from error
        template = build_template_vcg(pop_site_data, vm_name, addn_args)

    elif role_name.lower() in ["vco", "vco-dr"]:
        template = build_template_vco(addn_args)

    else:
        template = {}

    return template


def build_template_vco(additional_args):
    """Generate the Netbox Context Data for a VCO.

    TODO turn this structure into a class and keep it in a EdgeOps/Netbox module.

    Args:
        additional_args: list of key/value pairs from args.o

    Returns:
        config context suitable for passing to Netbox in VCO creation.
    """
    with open("templates/veco-gcp-terraform-vars.json", "r", encoding="utf-8") as file:
        json_content = file.read()

    default_tf_vars = json.loads(json_content)

    template = {
        "local_context": {"env0_deployment": "false", "TF_VAR": default_tf_vars},
        "custom_fields": {
            "version": "",
            "buildnum": "",
            "fqdn": "",
            "domain": "",
            "instance_type": "",
        },
    }

    for key, value in additional_args:
        cf_key = key.replace("cf_", "")
        if key.startswith("cf_"):
            if cf_key in template["custom_fields"]:
                template["custom_fields"][cf_key] = value
            else:
                print(f'WARNING: unknown key "{key}" in custom fields additional args')
        elif key in template["local_context"]:
            template["local_context"][key] = convert_string_to_type(value)
        elif key in template["local_context"]["TF_VAR"]:
            template["local_context"]["TF_VAR"][key] = convert_string_to_type(value)
        else:
            print(f'WARNING: unknown key "{key}" in additional args')

    # if template key value is None remove it from template
    # Create a list of keys to remove
    keys_to_remove = [
        key
        for key, value in template["local_context"]["TF_VAR"].items()
        if value is None or value == ""
    ]

    # Remove the keys from the nested dictionary
    for key in keys_to_remove:
        del template["local_context"]["TF_VAR"][key]

    return template


def build_template_vcg(pop_site_data, vcg_name, additional_args):
    """Generate the Netbox Context Data for a VCG.

    TODO turn this structure into a class and keep it in a EdgeOps/Netbox module.

    Args:
        pop_site_data: one of the dicts representing a POP from `load_pops_data`.
        vcg_name: e.g. `vcg30-sjc2e`
        additional_args: list of key/value pairs from args.o.

    Returns:
        config context suitable for passing to Netbox in VCG creation.
    """
    template = {
        "local_context": {
            "terraform": "true",
            "terraform_enabled": "true",
            "pool": "Default",
            "action": "create",
            "validation": "create",
            "TF_VAR": {
                "content_library_item": "",
                "content_library_name": "",
                "datacenter_name": pop_site_data["datacenter_name"],
                "mgmt_subnet": pop_site_data["mgmt_subnet"],
                "mgmt_subnet_mask": pop_site_data["mgmt_subnet_mask"],
                "vcenter_vcg_name": vcg_name,
                "vcg_activation_key": "",
                "vco_name": "",
                "vcmp_ip6_subnet": pop_site_data["vcmp_ip6_subnet"],
                "vcmp_subnet": pop_site_data["vcmp_subnet"],
                "vcmp_subnet_mask": pop_site_data["vcmp_subnet_mask"],
                "vsphere_server": pop_site_data["vcenter_hostname"],
                "wavefront_proxy_name": pop_site_data["wavefront_proxy_name"],
                "teleport_auth_token": "",
                "enable_nni": "false",
                "nni_interface": "",
                "salt_master_fqdn": pop_site_data["salt_master_fqdn"],
                "salt_master_finger": pop_site_data["salt_master_finger"],
                "salt_env": pop_site_data["salt_env"],
                "deletion_protection": "",
                "name": "",
                "environment": "",
                "project": "",
                "region": "",
                "zone": "",
                "image": "",
                "subnetwork": "",
                "subnetwork_project": "",
                "boot_disk_size": "",
                "labels": "",
                "tags": "",
                "vcmp_access_config": "",
                "vcmp_nat_ip": "",
                "vcmp_nat_ipv6": "",
                "nsd_ip": "",
                "machine_type": "",
            },
        },
        "custom_fields": {
            "version": "",
            "buildnum": "",
            "fqdn": "",
            "domain": "",
            "instance_type": "",
            "order_serial_number": "",
        },
    }

    for key, value in additional_args:
        cf_key = key.replace("cf_", "")
        if key in ("num_cpus", "num_cores_per_socket", "cpu_reservation"):
            template["local_context"]["TF_VAR"][key] = value
        if key.startswith("cf_"):
            if cf_key in template["custom_fields"]:
                template["custom_fields"][cf_key] = value
            else:
                print(f'WARNING: unknown key "{key}" in custom fields additional args')
        elif key in template["local_context"]:
            template["local_context"][key] = value
        elif key in template["local_context"]["TF_VAR"]:
            if key in ("num_cpus", "num_cores_per_socket"):
                value = int(value)
            template["local_context"]["TF_VAR"][key] = convert_string_to_type(value)
        else:
            print(f'WARNING: unknown key "{key}" in additional args')

    # if template key value is None remove it from template
    # Create a list of keys to remove
    keys_to_remove = [
        key
        for key, value in template["local_context"]["TF_VAR"].items()
        if value is None or value == ""
    ]

    # Remove the keys from the nested dictionary
    for key in keys_to_remove:
        del template["local_context"]["TF_VAR"][key]

    return template


def _updated_vcenter_vcg_name(context_data: dict, vm_name: str) -> str:
    """Build a new vcenter_vcg_name from existing one.

    In config context it should be of the form `vcg123-abcd4-0`, the trailing digit is the number of
    times it's been (re)built.

    Args:
        context_data: local config context from VCG record.
        vm_name: original VCG name, in case it's needed.
    Raises:
        ValueError: if it thinks that it's been called for something other than a VCG, which it
            determines by looking at the beginning of the VM's name, either from local context or
            `vm_name`.
    """
    vcenter_vcg_name = context_data.get("TF_VAR", {}).get("vcenter_vcg_name", "")
    orig_vcg_name = vm_name.lower()
    try:
        orig_vcg_name, postfix = vcenter_vcg_name.lower().rsplit("-", 1)
        vcg_index_new = int(postfix) + 1
    except ValueError:
        # If VCG name not in local context, or with incorrect format, create it afresh.
        vcg_index_new = 0
    if not orig_vcg_name.startswith("vcg"):
        msg = f'vcenter_vcg_name is for VCGs, not whatever "{orig_vcg_name}" is.'
        raise ValueError(msg)
    return f"{orig_vcg_name}-{vcg_index_new}"


def _ensure_vcg_defaults(
    default_template,
    origin_context,
    origin_cf_fields,
    tf_var_default_keys=frozenset(),
    custom_field_default_keys=frozenset(),
    local_context_default_keys=frozenset(),
):
    """Ensure that certain local_context, TF_VARs and custom fields always have values.

    Args:
        default_template: typically from `build_template_vco` or `build_template_vcg`.
        origin_context: the local_context, including TF_VAR, from Netbox.
        origin_cf_fields: the custom fields from Netbox.
        tf_var_default_keys: the names of the TF_VARs fields that will be copied into Netbox TF_VARs
            from default if not already present.
        custom_field_default_keys: the names of the custom fields that will be copied into Netbox
            custom fields from default if not already present.
        local_context_default_keys: the names of the local_context fields (outside TF_VAR) that will
            be copied into Netbox local context from default if not already present.
    """
    mappings = (
        (
            tf_var_default_keys,
            default_template.get("local_context", {}).get("TF_VAR", {}),
            origin_context.get("TF_VAR", {}),
        ),
        (
            custom_field_default_keys,
            default_template.get("custom_fields", {}),
            origin_cf_fields,
        ),
        (
            local_context_default_keys,
            default_template.get("local_context", {}),
            origin_context,
        ),
    )
    for keys, defaults, to_update in mappings:
        for key in keys:
            if key in defaults and key not in to_update:
                to_update[key] = defaults[key]


def _find_req_changes(vm_record, role, additional_args, omit_action):
    """Determine changes needed in Netbox for a VM record considering the args passed in.

    Args:
        vm_record: a Netbox VM data structure.
        role: Netbox role VM is part of (Eg, vcg or VCO)
        additional_args: fields to change in the VM's data. See`build_context_data`.
        omit_action: remove the "action" key if it was passed in.
    Returns:
        req_update: the keys from config context or custom fields that are to be updated.
        origin_context: Local context data with required changes
        origin_cf_fields: Custom fields data with required changes
    """
    req_update = []

    vm_name = vm_record.name
    site_name = str(vm_record.cluster)

    origin_context = vm_record.local_context_data  # local context
    origin_cf_fields = vm_record.custom_fields  # custom fields

    default_template = _load_templates(vm_name, site_name, role, additional_args)

    # Ensure certain defaults are ALWAYS present.
    # If they're not already in the Netbox data, copy them from the defaults.
    if role == "vcg":
        _ensure_vcg_defaults(
            default_template,
            origin_context,
            origin_cf_fields,
            tf_var_default_keys=VCG_ALWAYS_TF_VARS,
            custom_field_default_keys=VCG_ALWAYS_CUSTOM_FIELDS,
            local_context_default_keys=VCG_ALWAYS_LOCAL_CONTEXT,
        )

    for key, value in additional_args:
        # Process a custom field change.
        if key.startswith("cf_"):
            cf_key = key.replace("cf_", "")
            if cf_key in origin_cf_fields:
                if origin_cf_fields[cf_key] != value:
                    origin_cf_fields[cf_key] = convert_string_to_type(value)
                    req_update.append(key)

        elif key in origin_context:
            if not (omit_action and key == "action"):
                if origin_context[key] != value:
                    origin_context[key] = convert_string_to_type(value)
                    req_update.append(key)

        # Check if key is in Netbox TF_VAR local context.
        elif key in origin_context["TF_VAR"]:
            if origin_context["TF_VAR"][key] != value:
                origin_context["TF_VAR"][key] = convert_string_to_type(value)
                req_update.append(key)

        # If key doesn't exist in netbox, check if its in default template local context.
        elif key in default_template["local_context"]:
            origin_context[key] = convert_string_to_type(value)
            req_update.append(key)

        # If key doesn't exist in netbox, check if its in default template TF_VAR local context.
        elif key in default_template["local_context"]["TF_VAR"]:
            origin_context["TF_VAR"][key] = convert_string_to_type(value)
            req_update.append(key)

        else:
            print(f'WARNING: I don\'t know where key "{key}" comes from.')

    return req_update, origin_context, origin_cf_fields


def convert_string_to_type(data):
    """
    Converts a string representation to its actual type, handling nested structures.

    Args:
        data: A string representation of a dictionary, list, numerical value, or nested structure.

    Returns:
        The converted value or the original string if conversion fails.
    """

    if isinstance(data, dict):
        for key, value in data.items():
            data[key] = convert_string_to_type(value)
    elif isinstance(data, list):
        data = [convert_string_to_type(item) for item in data]
    elif data.strip().lower() in ["true", "false"]:
        return str(data.strip()).lower()
    elif isinstance(data, str):
        stripped = data.strip()
        # Handle as JSON (object or array)
        try:
            parsed_data = json.loads(stripped)
            # Re-serialize to enforce double quotes
            return json.loads(json.dumps(parsed_data))
        except json.JSONDecodeError:
            pass  # If JSON parsing fails, proceed to next check

        # Handle as Python literal
        try:
            parsed_data = ast.literal_eval(stripped)
            # Convert to JSON string and then back to Python object to ensure double quotes
            return json.loads(json.dumps(parsed_data))
        except (ValueError, SyntaxError):
            pass  # If parsing fails, do nothing

    return data


#
# NETBOX OPERATIONS
#

# TODO move these Netbox operations into a module.


def delete_vm(netbox, vm_info):
    """Delete a VM from Netbox.

    Args:
        netbox: authenticated `pynetbox.api` instance.
        vm_info: a Virtual Machine data struct as returned from Netbox
    """
    netbox.virtualization.virtual_machines.delete([vm_info.id])


def get_role_id(netbox, role_name):
    """Return the internal Netbox ID of the role with the given name.

    Args:
        netbox: authenticated `pynetbox.api` instance.
        role_name: name of the role whose ID we want.
    """
    role = netbox.dcim.device_roles.get(name=role_name)
    if role is not None:
        return role.id
    return None


def get_cluster_id(netbox, cluster_name):
    """Return the internal Netbox ID of the cluster with the given name.

    Args:
        netbox: authenticated `pynetbox.api` instance.
        cluster_name: name of the cluster whose ID we want.
    """
    cluster = netbox.virtualization.clusters.get(name=cluster_name)
    if cluster is not None:
        return cluster.id
    return None


def get_vm(netbox, vm_name, cluster_name):
    """Get a Netbox API data structure corresponding to the named VM at the named cluster.

    Args:
        netbox: authenticated `pynetbox.api` instance.
        vm_name: name of the VM to find.
        cluster_name: name of the cluster whose ID we want.
    """
    vm_detail = netbox.virtualization.virtual_machines.get(name=vm_name)
    if vm_detail is not None and vm_detail.cluster.name == cluster_name:
        return vm_detail
    return None


def put_vm(netbox, vm_name, cluster_name, role_name, data):
    """Create a VM record in Netbox API.

    Args:
        netbox: authenticated `pynetbox.api` instance.
        vm_name: name of the VM to create.
        cluster_name: name of the cluster the VM is deployed in.
        role_name: name of the VM's role.
        data: VM data, see`build_context_data`.
    """
    cluster_id = get_cluster_id(netbox, cluster_name)
    role_id = get_role_id(netbox, role_name)

    if cluster_id is None or role_name is None:
        return

    template = {
        "name": vm_name,
        "cluster": cluster_id,
        "role": role_id,
        "local_context_data": data["local_context"],
        "custom_fields": data["custom_fields"],
    }
    netbox.virtualization.virtual_machines.create(template)


def update_vm(netbox, vm_record, additional_args, omit_action=True):
    """Update a VM's record in Netbox.

    The vcenter_vcg_name of the TF_VAR config context data is of the form "vcg12-sin1-0" where the
    trailing digit indicates index value which helps to identify/manage lifecycle of VCG in
    Terraform. When updating a VCG record, this trailing digit is updated to show "vcg12-sin1-1".

    Args:
        netbox: authenticated `pynetbox.api` instance.
        vm_record: a Netbox VM data structure.
        additional_args: keys/values of fields to change in the VM's data.
        omit_action: remove the "action" key if it was passed in.
    """
    vm_role = str(vm_record.role).lower()
    updated, context_data, cf_fields = _find_req_changes(
        vm_record, vm_role, additional_args, omit_action
    )

    # Just return now if there are no changes to make
    if not updated:
        print("No changes to update")
        return

    update_count = len(updated)

    if vm_role == "vcg":
        # Update the VCG's name as it is in vCenter.
        # Increment the value with 1 for vcg name so TF can manage lifecycle.
        try:
            vcenter_vcg_name = _updated_vcenter_vcg_name(context_data, vm_record.name)
        except ValueError as error:
            # Bail out immediately if the VCG name update fails, because it means this record is
            # not for a VCG, which is a fatal error.
            print(str(error))
            return
        context_data["TF_VAR"]["vcenter_vcg_name"] = vcenter_vcg_name
        updated.append("vcenter_vcg_name")
        update_count += 1

    print(f"updating {update_count} values")
    result = netbox.virtualization.virtual_machines.update(
        [
            {
                "id": vm_record.id,
                "local_context_data": context_data,
                "custom_fields": cf_fields,
            }
        ]
    )
    if isinstance(result, list):
        print("updated keys: " + ", ".join(updated))
        print("success in update")
    else:
        print("failure in update")


def set_destroy(netbox, vm_record):
    """Mark a VM for destruction.

    Args:
        netbox: authenticated `pynetbox.api` instance.
        vm_record: a Netbox VM data structure.
    """
    keep = vm_record.local_context_data
    keep["action"] = "destroy"
    keep["validation"] = "destroy"
    netbox.virtualization.virtual_machines.update(
        [{"id": vm_record.id, "local_context_data": keep}]
    )


#
# CLI
#


def main():
    """Main function: Authenticate to Netbox & manage netbox objects"""
    netbox = pynetbox.api(os.environ["NETBOX_URL"], token=os.environ["NETBOX_TOKEN"])

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-a",
        "--action",
        required=True,
        dest="action",
        choices=(
            "get",
            "delete",
            "update",
            "upgrade",
            "destroy",
            "create",
            "get_vm_tf_vars",
        ),
        help="action for script to perform",
    )
    parser.add_argument(
        "-v", "--vm", required=True, dest="vmName", help="Virtual Machine Name"
    )
    parser.add_argument(
        "-s", "--site", dest="siteName", required=True, help="site name for actions"
    )
    parser.add_argument(
        "-r", "--role", dest="roleName", required=True, help="role to assign to vm"
    )
    parser.add_argument(
        "-o", action="append", nargs="+", help="additional args for patching/creating"
    )
    parser.add_argument(
        "-t",
        "--template",
        dest="templateFile",
        help="template file for local_context_data",
    )
    parser.add_argument(
        "--add-backup-tags",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Add backup tag to VM record",
    )
    parser.add_argument(
        "--add-dedicated-tag",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Add dedicated tag for gateway",
    )
    args = parser.parse_args()

    action = args.action
    vm_name = args.vmName
    site_name = args.siteName
    role_name = args.roleName

    # Parse the additional args into key/value pairs, checking that they are in the correct form.
    additional_args = []
    for item in args.o or []:
        try:
            key, value = " ".join(item).split(":", 1)
        except ValueError:
            print(
                f'Argument "{item}" in additional args should have at least one colon (":") in it, '
                f"splitting the key from the value."
            )
            return
        additional_args.append((key, value))

    # Currently this netbox script manages only vcg & vco vm's.
    # Validating if current role is passed
    if role_name.lower() not in ("vcg", "vco", "vco-dr"):
        print(f'Unexpected role name: "{role_name}"')
        return

    netbox_action(action, netbox, vm_name, site_name, role_name, additional_args)

    # Temporary tasks added to update tags
    backup_tag_name = "veco-backup-enable"
    if action in ["create", "update"]:
        all_tags = netbox.extras.tags.all()
        vm_detail = netbox.virtualization.virtual_machines.get(name=vm_name)
        vm_tags_ids = [tag.id for tag in vm_detail.tags]
        vm_tags_name = [tag.name for tag in vm_detail.tags]

        if args.add_backup_tags:
            backup_tags_id = [tag.id for tag in all_tags if tag.name == backup_tag_name]
            if backup_tag_name not in vm_tags_name:
                vm_tags_ids.extend(backup_tags_id)
                vm_detail.tags = vm_tags_ids
                print(f"Backup tag {backup_tag_name} added to the vm {vm_name}")
        else:
            if backup_tag_name in vm_tags_name and len(backup_tags_id) > 0:
                vm_tags_ids.remove(backup_tags_id[0])
                vm_detail.tags = vm_tags_ids
                print(f"Backup tag {backup_tag_name} removed from the vm {vm_name}")
    
        if args.add_dedicated_tag: 
            vcg_dedicated_tag_name = "vcg-dedicated"
            vcg_dedicated_tag_id = [tag.id for tag in all_tags if tag.name == vcg_dedicated_tag_name]

            if vcg_dedicated_tag_name not in vm_tags_name:
                vm_tags_ids.extend(vcg_dedicated_tag_id)
                vm_detail.tags = vm_tags_ids
                print(f" tag {vcg_dedicated_tag_name} added to the vm {vm_name}")

        vm_detail.save()

if __name__ == "__main__":
    main()
