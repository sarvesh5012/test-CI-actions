import argparse
import re
import os
import zipfile
import json
import hashlib
from typing import Optional, List, Dict
from zipfile import BadZipFile
from enum import Enum
import pynetbox
from packaging.version import Version
from edgeops_vco.vco import Vco
from edgeops_vco.vco import VcoResponseError
from edgeops_vco.vco import VcoResponseEmpty
from edgeops_vco.vco import VcoRequestError
from edgeops_vco.vco import VcoConfigUpdateError


# Enums
class VecoUploadError(Enum):
    IMAGE_EXISTS = "The selected Image already exists"
    DOES_NOT_MATCH = "does not match"


# Constants
IMAGE_UPLOAD_RETRIES = 5
EDGE_MANIFEST_FILE = "MANIFEST.json"
EDGE_TYPE_LIST = ["EDGE500", "EDGE5X0", "EDGE6X0", "EDGE7X0", "EDGE8X0",
                  "EDGE1000", "EDGE3X00", "VC_VMDK", "VC_XEN_AWS", "VC_KVM_GUEST"]
EDGE_DEVICE_MAP = {
    "EDGE5X0": ["EDGE500", "EDGE5X0", "EDGE6X0"],
    "EDGE7X0": ["EDGE7X0"],
    "EDGE1000": ["EDGE8X0", "EDGE1000", "EDGE3X00"],
    "VC_VMDK": ["VC_VMDK"],
    "VC_XEN_AWS": ["VC_XEN_AWS"],
    "VC_KVM_GUEST": ["VC_KVM_GUEST"]
}
VECO_ENTERPRISE_TENANTS = ["Preview", "Shared"]

upload_retry_errors = [VecoUploadError.DOES_NOT_MATCH.value]
upload_skip_errors = [VecoUploadError.IMAGE_EXISTS.value]
edge_image_regex = re.compile("edge-imageupdate-.*zip")
edge_firmware_regex = re.compile("edge-imageupdate-.*MR.*zip")


class newVco(Vco):
    def get_veco_version(self) -> str:
        """Gets a list of Edge Profiles from the VECO.

        Args:
            veco: Vco object from the EdgeOps VCO SDK
        Returns:
            Version string from VECO
        Raises:
            VcoResponseError: If there's an error included in the response message
        """
        try:
            version = self._post("/system/getVersionInfo")
        except VcoResponseError as error:
            message = f"VECO Version request error: {error}"
            raise VcoResponseError(message) from error
        return version['version']

    def get_application_map_raw(self) -> list:
        """Gets the list of Application Maps from the VECO.

        Args:
            veco: Vco object from the EdgeOps VCO SDK
        Returns:
            List of Dictionaries from the VECO of Application Map configuration objects
        Raises:
            VcoResponseError: If there's an error included in the response message
        """
        response = None
        try:
            response: list = self._post("configuration/getApplicationMaps")
        except VcoResponseError as error:
            message = f"Get Application Map error: {error}"
            raise VcoResponseError(message) from error
        if response is None:
            raise VcoResponseEmpty
        return response

    def get_application_map_id(self, manifest: str) -> Optional[dict]:
        """Gets a single Application Map from the VECO by matching the manfiest
        Returns the id and logicalId or None if unable to match

        Args:
            veco: Vco object from the EdgeOps VCO SDK
            manifest: String of the sha1 hash to match to return the id and logicalId
        Returns:
            List of Dictionaries from the VECO of Application Map configuration objects
        Raises:
            VcoResponseError: If there's an error included in the response message
        """
        try:
            response: list = self.get_application_map_raw()
        except VcoResponseEmpty:
            return None
        # Using a get for the nested key because not all AppMaps have the 'hash' field
        else:
            try:
                return next(
                    {
                        k: outer_item.get(k)
                        for k in ('id', 'logicalId')
                    }
                    for outer_item in response if
                    outer_item['uploadDetails'].get('hash') == manifest
                )
            except StopIteration:
                return None

    def assign_application_map(self, operator_appmap_config_id: int,
                               application_config_logical_id: str) -> bool:
        """Updates a Software Image Configuration object on a VECO
        Args:
            operator_appmap_config_id: id of the Application Map object inside an Operator Profile configuration module
            application_config_logical_id: Application Map logical id - GUID

        Returns:
            True if the Update Configuration Module call succeeds and the profile is updated
            False a blank response
        Raises:
            VcoResponseError in case the call fails
        """
        update_configuration_module_parameters = {
            "id": operator_appmap_config_id,
            "_update": {
                "name": "metaData",
                "description": "",
                "data": {
                    "applications": {"logicalId": application_config_logical_id,
                                     "type": "APPLICATION_MAP"}
                }
            }
        }
        try:
            response = self._post(
                "configuration/updateConfigurationModule",
                payload=update_configuration_module_parameters
            )
        except VcoResponseError as error:
            message = f"Configuration Module Update error: {error}"
            raise VcoConfigUpdateError(message) from error
        if response is None:
            raise VcoResponseEmpty
        return "rows" in response and response["rows"] == 1

    def rename_application_map(self, application_config_id: int, manifest: str) -> bool:
        """Updates an existing Application Map Configuration name and description
        Args:
            application_config_id: id of the Application Map object
            application_config_logical_id: Application Map logical id - GUID

        Returns:
            True if the Update Configuration Module call succeeds and the profile is updated
            False a blank response
        Raises:
            VcoResponseError in case the call fails
        """
        update_appmap_config_parameters = {
            "id": application_config_id,
            "_update": {
                "name": f"Release {manifest} Default Application Map",
                "description": "Uploaded using veco-edge-config Jenkins job - "
                               f"schema version {manifest}",
            }
        }
        try:
            response = self._post(
                "configuration/updateApplicationMap",
                payload=update_appmap_config_parameters
            )
        except VcoResponseError as error:
            message = f"Application Map Update error: {error}"
            raise VcoApplicationMapUpdateError(message) from error
        if response is None:
            raise VcoResponseEmpty
        return "rows" in response and response["rows"] == 1

    def get_managed_enterprise_list(self) -> list:
        """Gets a list of all Enterprise Ids that have edgeImageManagement property enabled

        Args:
            veco: Vco object from the EdgeOps VCO SDK
        Returns:
            List of Enterprise ids (int)
        Raises:
            VcoResponseError: If there's an error included in the response message
        """
        payload = {
            "name": "vco.enterprise.edgeImageManagement.enable",
            "value": "true"
        }
        try:
            response: list = self._post(
                "enterprise/getEnterprisesWithProperty",
                payload=payload
            )
        except VcoResponseError as error:
            if "Got an empty response" in error.args[0]:
                return []
            message = f"Get Enterprise List error: {error}"
            raise VcoResponseError(message) from error
        return list(enterpriseId["id"] for enterpriseId in response if enterpriseId.get("id"))

    def get_full_enterprise_list(self) -> list:
        """Gets a list of all Enterprise Ids on the VECO

        Args:
            veco: Vco object from the EdgeOps VCO SDK
        Returns:
            List of Enterprise ids (int)
        Raises:
            VcoResponseError: If there's an error included in the response message
        """
        payload = {
            "networkId": 1,
        }
        try:
            response: list = self._post("network/getNetworkEnterprises", payload=payload)
        except VcoResponseError as error:
            if "Got an empty response" in error.args[0]:
                return []
            message = f"Get Enterprise List error: {error}"
            raise VcoResponseError(message) from error
        return list(enterpriseId["id"] for enterpriseId in response if
                    enterpriseId.get("enterpriseProxyId") is None)

    def get_enterprise_proxy_list(self) -> list:
        """Gets a list of all Partner Enterprises from the VECO

        Args:
            veco: Vco object from the EdgeOps VCO SDK
        Returns:
            List of Partner Enterprise ids (int)
        Raises:
            VcoResponseError: If there's an error included in the response message
        """
        payload = {
            "networkId": 1,
        }

        try:
            response: list = self._post("network/getNetworkEnterpriseProxies", payload=payload)
        except VcoResponseError as error:
            if "Got an empty response" in error.args[0]:
                return []
        return list(enterpriseId["id"] for enterpriseId in response)

    def get_enterprise_proxy_operator_profiles(self, enterprise_proxy_id) -> list:
        """Gets a list of all Operator Profiles associated with an Enterprise Proxy

        Args:
            veco: Vco object from the EdgeOps VCO SDK
        Returns:
            List of Operator Profiles
        Raises:
            VcoResponseError: If there's an error included in the response message
        """
        payload = {
            "enterpriseProxyId": enterprise_proxy_id,
        }

        try:
            response: list = self._post("enterpriseProxy/getEnterpriseProxyOperatorProfiles",
                                        payload=payload)
        except VcoResponseError as error:
            if "Got an empty response" in error.args[0]:
                return []
            message = f"Get Enterprise Proxy List error: {error}"
            raise VcoResponseError(message) from error
        return list(operator_profile_id["id"] for operator_profile_id in response)

    def add_enterprises_to_op(self, operator_profile_id: int, enterprise_lst: list) -> bool:
        """Assigns an existing operator profile to a list of Enterprise ids

        Args:
            veco: Vco object from the EdgeOps VCO SDK
            operator_profile_id: id of the Operator Profile to assign the Enterprises to
            enterprise_lst: list of Enterprise ids
        Returns:
            True on success
        Raises:
            VcoResponseError: If there's an error included in the response message
        """
        payload = {
            "configurationId": operator_profile_id,
            "enterpriseIds": enterprise_lst
        }

        try:
            self._post("enterprise/addEnterpriseOperatorConfiguration", payload=payload)
        except VcoResponseError as error:
            message = f"Add Enterprises to op profile error: {error}"
            raise VcoResponseError(message) from error
        return True

    def update_op_list_for_proxy_enterprises(self, enterprise_proxy_id,
                                             operator_profiles_lst: list) -> bool:
        """Assigns a list of operator profiles to a Proxy (MSP) Enterprise

        Args:
            veco: Vco object from the EdgeOps VCO SDK
            enterprise_proxy_id: id of the Proxy Enterprise to update
            operator_profiles_lst: list of Operator Profile ids
        Returns:
            True on success
        Raises:
            VcoResponseError: If there's an error included in the response message
        """
        payload = {
            "enterpriseProxyId": enterprise_proxy_id,
            "_update": {
                "configurationId": operator_profiles_lst
            }
        }

        try:
            self._post("enterpriseProxy/updateEnterpriseProxy", payload=payload)
        except VcoResponseError as error:
            message = f"Update Proxy Enterprises error: {error}"
            raise VcoResponseError(message) from error
        return True

    def get_network_configurations_raw(self) -> dict:
        """Gets a list of Edge Profiles from the VECO.

        Args:
            veco: Vco object from the EdgeOps VCO SDK
        Returns:
            List of Dictionaries from the VECO of Operator Profiles, including the image files and versions
        Raises:
            VcoResponseError: If there's an error included in the response message
        """
        response = None
        try:
            response: list = self._post(
                "network/getNetworkConfigurations", payload={"with": ["counts", "imageInfo"]}
            )
        except VcoResponseError as error:
            message = f"Profile Request error: {error}"
            raise VcoResponseError(message) from error
        if response is None:
            raise VcoResponseEmpty
        return response

    def get_network_configurations_summary(self) -> List[Dict]:
        """Gets a list of Edge Profiles from the VECO.

        Args:
            veco: Vco object from the EdgeOps VCO SDK
        Returns:
            A mapping of Profiles from the VECO by Profile Name, id, any included Software, versions and deprecation state
            We return deprecation state instead of filtering because that should be handled by the caller
        """
        try:
            operator_profiles = self.get_network_configurations_raw()
        except VcoResponseEmpty:
            return [{}]
        return [
            {
                k: item.get(k)
                for k in ('id', 'name', 'imageInfo', 'enterpriseCount')
            }
            for item in operator_profiles
        ]

    def get_software_updates_list_raw(self, search_string: str = ""):
        """Gets a list of Software Images from the VECO.

        Args:
            veco: Vco object from the EdgeOps VCO SDK
            search_string: (Optional) String to send to quickSearch field
        Returns:
            List of Dictionaries from the VECO of software images
        Raises:
            VcoResponseError: If there's an error included in the response message
        """
        response = None
        payload = None
        if search_string:
            payload = {
                "quickSearch": search_string
            }
        try:
            response: dict = self._post(
                "softwareUpdate/getSoftwareUpdatesList", payload=payload
            )
        except VcoResponseError as error:
            message = f"Software List Request error: {error}"
            raise VcoResponseError(message) from error
        if response is None:
            raise VcoResponseEmpty
        return list(response["data"])

    def get_software_updates_list_summary(self, profile: str = "") -> List[Dict]:
        """Gets a list of Software Images from the VECO.

        Args:
            veco: Vco object from the EdgeOps VCO SDK
            profile: (Optional) build number/profile string to search for
        Returns:
            A mapping of Profiles from the VECO by Profile Name, id, any included Software, versions
            and deprecation state. We return deprecation state instead of filtering because that
            should be handled by the caller.
        """
        try:
            if profile:
                software_images_list = self.get_software_updates_list_raw(profile)
            else:
                software_images_list = self.get_software_updates_list_raw()
        except VcoResponseEmpty:
            return [{}]
        return [
            {
                k: item.get(k)
                for k in ('id', 'buildNumber', 'deviceFamily',
                          'deviceCategory', 'deprecated', 'fileName')
            }
            for item in software_images_list
        ]

    def create_operator_profile(self, profile_name: str = "") -> int:
        """Creates a new operator profile on a VECO based on the existing Segmented Network profile
        Args:
            profile_name: The name for a new Operator Profile

        Returns:
            An int matching the created Operator Profile id

        Raises:
            VcoResponseError in case the call fails
        """
        msg = ""

        if not profile_name:
            msg = "Must provide a profile_name: New Operator Profile name"
            raise ValueError(msg)

        network_template_payload = {
            "networkId": 1,
            "name": profile_name,
            "configurationType": "SEGMENT_BASED"
        }
        try:
            response: dict = self._post(
                "configuration/cloneNetworkTemplate",
                payload=network_template_payload
            )
        except VcoRequestError as error:
            message = f"Error creating Operator Profile {error}"
            raise VcoRequestError(message) from error
        return response["id"]


class VcoApplicationMapUpdateError(VcoResponseError):
    """There was a problem indicated in the response to the Application Map Update from the VCO."""


# Netbox methods
def netbox_object(netbox_url, edgeops_token):
    """Return an autheticate netbox object after performing a login.

    Args:
        netbox_url: The URL to the netbox instance to use
        edgeops_token: Token to allow user to send requests
    """
    return pynetbox.api(netbox_url, token=edgeops_token)


def check_netbox(netbox):
    """Checks that our netbox session is authenticated

    Args:
        netbox: `pynetbox.api` instance.

    Returns:
        True: Successful authentication and login
        False: Failed to connect/login
    """
    try:
        netbox.ipam.ip_addresses.choices()
    except Exception:
        return False
    return True


def get_vm_tenant_id(netbox, vm_name) -> Optional[int]:
    """Get the netbox virtualmachine tenant ID corresponding to the named VM.

    Args:
        netbox: `pynetbox.api` instance.
        vm_name: name of the VM to find.

    Returns:
        Integer id of the VM's associated tenant id
        None if the call failed

    Raises:
        ValueError if no vm_name is provided
    """
    if not vm_name:
        msg = "vm_name not provided"
        raise ValueError(msg)
    try:
        vm_detail = netbox.virtualization.virtual_machines.get(name=vm_name)
    except pynetbox.RequestError:
        return None
    try:
        return vm_detail.tenant.id
    except AttributeError:
        # Just in case we have an incorrectly configured VECO object in Netbox
        return None


def get_tenant_type(netbox, tenant_id: int) -> Optional[str]:
    """Get the Netbox Tenant type. Will return a Group if it exists, or Name if no group

    Args:
        netbox: `pynetbox.api` instance
        tenant_id: id to a Tenant object in Netbox

    Returns:
        String of the Tenant Group or Name (in that order)
        None if the call failed

    Raises:
    """
    try:
        tenant_detail = dict(netbox.tenancy.tenants.get(tenant_id))
    except pynetbox.RequestError:
        return None
    if tenant_detail.get('group'):
        return tenant_detail.get('group', {})['name']
    return tenant_detail['name']

def get_veco_list_by_tag(netbox, search_tag: str) -> list:
    """Get a list of VMs from netbox based on the tag

    Args:
        netbox: `pynetbox.api` instance
        search_tag: String to search against (passed as a key to the filter method)

    Returns:
        List of VECOs by name
        None if the call failed

    Raises:
    """
    try:
        vm_list = list(netbox.virtualization.virtual_machines.filter(tag=search_tag))
    except pynetbox.RequestError:
        return None
    return vm_list

# Utility functions from build_cws_inventory.py

def add_slash_to_path(path):
    return path if path.endswith("/") else path + "/"


def validate_path(path):
    return os.path.isdir(path)


def get_edge_profiles(veco, profile_str: str):
    """Gets a list of Edge Profiles from the VECO. Optionally: Returns a single profile if provided.

    Args:
        veco: Vco object from the EdgeOps VCO SDK
            Vco object should be authenticated and tested before handling to this function
        profile_str: String containing a profile name to match against

    Returns:
        profile: The first profile from the VECO that matches 'profile'
        None if there are no matching Operator Profiles from the VECO
        False for any Exceptions
    """
    try:
        operator_profiles = veco.get_network_configurations_summary()
    except Exception as error:
        print(f'Unable to get profiles from {veco.name}: {error}')
        return False
    return next((item for item in operator_profiles if profile_str in item["name"]), None)


def zip_files_test(file_lst):
    """Verifies that all zip files in a directory are valid and that there is at least one.

    Args:
        file_lst: List Constructor that is a list of zip files

    Returns:
        True: At least one valid zip file exists in the directory specified
        False: Empty directory or no valid zip file
    """
    for file_with_path in file_lst:
        try:
            test_zip_file = zipfile.ZipFile(file_with_path)
            test_return = test_zip_file.testzip()
        except BadZipFile:
            return False
        if test_return is not None:
            print(f"{file_with_path} failed zip-file test: {test_return}")
            return False
    return True


def extract_profile_name(file_lst, directory):
    """Extracts the MANIFEST file from an Edge Image zip and retrieve the version string

    Args:
        file_lst: List Constructor that is a list of tested zip files

    Returns:
        Profile: Version string from MANIFEST.JSON file
        None: No MANIFEST.JSON in any edgeimage file
    """
    profile_str, manifest_file_path = None, None
    # Target only edgeimage files for searching for the Manfest file
    edge_image_lst = [f for f in file_lst if re.search(edge_image_regex, f)]
    for file_with_path in edge_image_lst:
        test_zip_file = zipfile.ZipFile(file_with_path)
        try:
            test_zip_file.extract(EDGE_MANIFEST_FILE, directory)
        except KeyError:
            # We may have files without Manifests, we can skip these and look for the first zip
            # with a Manifest
            pass
        else:
            # Break out if we get a Manifest file
            break
    # Note at this point, we may not have a Manfiest file, so check before proceeding
    image_files = os.listdir(directory)
    manifest_file_path = [
        directory + f
        for f in image_files
        if (os.path.isfile(directory + f) and f.endswith(EDGE_MANIFEST_FILE))
    ]
    if manifest_file_path is None:
        return None
    try:
        manifest_file = open(manifest_file_path[0])
    except Exception as error:
        print(f"Unable to open {manifest_file_path}: {error}")
        return None
    try:
        data = json.load(manifest_file)
    except Exception as error:
        print(f"{EDGE_MANIFEST_FILE} not in valid JSON format: {error}")
        return None
    try:
        profile_str = data["buildNumber"]
    except Exception as error:
        print(f"{EDGE_MANIFEST_FILE} missing buildNumber: {error}")
        return None
    return profile_str


def get_file_sha1_hash(file_name: str):
    """Returns the calculated md5 hash for a file

    Args:
        file: String of a path to a file

    Returns:
        md5 hash of the provided file
        None for any failure
    """
    with open(file_name, 'rb') as file_to_check:
        try:
            data = file_to_check.read()
        except OSError as error:
            print(f"Unable to open file {file_name}: {error}")
            return None
        return hashlib.sha1(data).hexdigest() or None


def perform_file_upload(veco, image_lst, profile: str = "", image_type: str = ""):
    """Takes a list of image files and uploads them onto a VECO.
    Due to the unreliability of image uploads, this function will handle various failure types and
    retry multiple times before erroring out.

    Args:
        veco: Authenticated Vco class object
        imageLst: List of paths of files that will be uploaded to the VECO
            Note - This function also supports application.json if appmap is passed to imageType
        profile: (Optional) string that contains the manifest buildNumber
            This is searched on the VECO to make sure we don't attempt uploading duplicates
        image_type: (Optional) One of the following types of files to handle:
            edge: Edge images - Default
            firmware: Edge Firmware images
            appmap: Application map

    Returns:
        True: File is uploaded successfully
        False: File upload failed
    """
    device_type_missing_list = []
    existing_veco_images = []
    image_files_to_upload = []
    file_image_list = []
    if not image_type or image_type == "edge":
        file_image_list = [f for f in image_lst if re.search(edge_image_regex, f)]
    elif image_type == "firmware":
        file_image_list = [f for f in image_lst if re.search(edge_firmware_regex, f)]
    elif image_type == "appmap":
        file_image_list = [f for f in image_lst if re.search("applications.json$", f)]
    if len(file_image_list) == 0:
        print("No files found that match expected type")
        return False
    try:
        existing_veco_images = veco.get_software_updates_list_summary(profile)
    except VcoResponseEmpty:
        pass
    except VcoResponseError:
        print(f"Failed to request image list on {veco.name}")
        return False
    if image_type == "appmap":
        for file in file_image_list:
            upload_attempts = 0
            while upload_attempts < IMAGE_UPLOAD_RETRIES:
                try:
                    veco.upload(file, "appmap")
                except OSError as error:
                    return False
                except VcoResponseError as error:
                    if any(error_string in error.args[0] for error_string in upload_retry_errors):
                        print(f"Retrying upload on {os.path.basename(file)} - {error}")
                    upload_attempts += 1
                else:
                    print(f"Uploaded {os.path.basename(file)}")
                    return True
            else:
                # Failure path for maximum upload attempts reached
                print(f"Failed on uploading {os.path.basename(file)} to {veco.name}")
                return False
    # Detect if any files are missing and only attempt those
    missing_list = list(
        set(EDGE_TYPE_LIST).difference(
            list(f['deviceFamily'] for f in existing_veco_images)
        )
    )
    if missing_list:
        for missing_image in missing_list:
            # Convert the image files as they expand on the VECO to the zip file that contains them
            device_type_missing_list.append(next(edge_type for edge_type in EDGE_DEVICE_MAP if
                                                 missing_image in EDGE_DEVICE_MAP[edge_type]))
        device_type_missing_dict = {i: device_type_missing_list.count(i) for i in
                                    device_type_missing_list}
        for device_type in device_type_missing_dict:
            if device_type_missing_dict[device_type] == len(EDGE_DEVICE_MAP[device_type]):
                # Search through file_image_list and create a new list based on device_types that
                # are missing. We're doing a length comparison because all device types must be
                # missing for the VECO to accept the file
                try:
                    image_files_to_upload.append(
                        next(f for f in file_image_list if re.match(f".*{device_type}.*", f)))
                except StopIteration:
                    # If the image does not exist, ignore it and continue
                    continue
    else:
        print("Skipping image file upload, all files exist")
        return True
    if len(image_files_to_upload) == 0:
        print(f"All image already uploaded to {veco.name}, skipping to next VECO")
        return True
    print(f"Uploading edge image files to {veco.name}")
    for file in image_files_to_upload:
        upload_attempts = 0
        while upload_attempts < IMAGE_UPLOAD_RETRIES:
            try:
                veco.upload(file, "image")
            except OSError as error:
                print(error)
                return False
            except VcoResponseError as error:
                if any(error_string in error.args[0] for error_string in upload_skip_errors):
                    # This file already exists on VECO
                    # Note that this path should almost never happen with the missing_image search
                    break
                print(f"Retrying upload on {os.path.basename(file)} - {error}")
                upload_attempts += 1
            else:
                print(f"Uploaded {os.path.basename(file)}")
                break
        else:
            # Failure path for maximum upload attempts reached
            print(f"Failed on uploading {os.path.basename(file)} to {veco.name}")
            return False
    return True


def assign_images_to_op_profile(veco, profile, operator_profile_id: int, image_type: str = ""):
    """Assigns edge, firmware or platform images to the existing operator_profile_id

    Args:
        veco: Vco object from the EdgeOps VCO SDK
            Vco object should be authenticated and tested before handling to this function
        operator_profile_id: Int that is the Operator Profile Configuration Module id on the VECO
        image_type: (Optional) One of the following types of profiles to assign:
            edge: Edge images - Default
            firmware: Edge Firmware images
            appmap: Application map
    Returns:
        True if the profile is assigned
        False for any Exceptions or errors
    """
    try:
        operator_profile_dict = veco.get_image_update_configuration(operator_profile_id)
    except VcoResponseEmpty:
        print(
            f"Unable to find Operator Profile configuration module. "
            f"Got empty response for profile id {operator_profile_id}"
        )
        return False
    except VcoResponseError as error:
        print(f"{veco.name} request failed on configuration id {operator_profile_id}: {error}")
        return False
    version_str = '.'.join(
        profile.split("-")[0].strip("R")[i:i + 1]
        for i in range(0, len(profile.split("-")[0].strip("R")), 1)
    )
    data_dict = operator_profile_dict["data"]
    if data_dict["buildNumber"] != profile:
        update_profile_dict = {
            "buildNumber": profile,
            "profileVersion": version_str,
            "version": version_str
        }
    else:
        update_profile_dict = {
            "buildNumber": data_dict["buildNumber"],
            "profileVersion": data_dict["profileVersion"],
            "version": data_dict["profileVersion"]
        }
    update_profile_dict["deviceFamily"] = EDGE_TYPE_LIST
    if data_dict["buildNumber"] != profile:
        update_profile_dict["softwarePackageName"] = f"{version_str}(build {profile})"
    else:
        update_profile_dict[
            "softwarePackageName"] = f"{data_dict['profileVersion']}(build {data_dict['buildNumber']})"
    if (not image_type or image_type == "edge"):
        update_profile_dict['factoryFirmware'] = data_dict['factoryFirmware']
        update_profile_dict['modemFirmware'] = data_dict['modemFirmware']
        update_profile_dict['platformFirmware'] = data_dict['platformFirmware']
        # perform call and exit
    if image_type == "firmware":
        update_profile_dict['factoryFirmware'] = {
            "buildNumber": profile,
            "deviceFamily": EDGE_TYPE_LIST,
            "windowDurationMins": data_dict['factoryFirmware']['windowDurationMins'],
            "windowed": data_dict['factoryFirmware']['windowed']
        }
        if data_dict["buildNumber"] != profile:
            update_profile_dict['factoryFirmware']['version'] = f"{version_str}(build {profile})"
        else:
            update_profile_dict['factoryFirmware'][
                'version'] = f"{data_dict['profileVersion']}(build {data_dict['buildNumber']})"
        update_profile_dict['modemFirmware'] = data_dict['modemFirmware']
        update_profile_dict['platformFirmware'] = data_dict['platformFirmware']
    try:
        veco.update_image_profile(operator_profile_dict["id"], update_profile_dict)
    except VcoResponseEmpty:
        print("Unable to update configuration. Got empty response from VECO - Check parameters")
        return False
    except VcoConfigUpdateError as error:
        print(f"Error in Update Configuration request: {error}")
        return False
    else:
        return True


def assign_op_to_enterprises(veco, operator_profile_id: int):
    """Assigns an operator_profile_id to Enterprises on a VECO that meet the following criteria:
    VECO is Enterprise/Shared
    Enterprise has Edge Image Management property enabled

    Args:
        veco: Vco object from the EdgeOps VCO SDK
            Vco object should be authenticated and tested before handling to this function
        operator_profile_id: Int that is the Operator Profile Configuration Module id on the VECO

    Returns:
        True if the Enterprises are assigned or no valid enterprises to work on
        False for any Exceptions or errors
    """
    try:
        full_enterprise_lst = veco.get_full_enterprise_list()
        managed_enterprise_lst = veco.get_managed_enterprise_list()
    except VcoResponseError as error:
        print(f"Unable to retrieve Enterprise list on {veco.name}: {error}")
        return False
    # Since empty sets are valid responses, we should do a check in case either list is empty and
    # return True
    if len(full_enterprise_lst) == 0 or len(managed_enterprise_lst) == 0:
        return True
    # Intersect the two lists and return a list of valid ids
    target_enterprises_lst = list(set(full_enterprise_lst) & set(managed_enterprise_lst))
    if len(target_enterprises_lst) == 0:
        return True
    try:
        veco.add_enterprises_to_op(operator_profile_id, target_enterprises_lst)
    except VcoResponseError as error:
        print(f"{veco.name}: {error}")
        return False
    return True


def assign_op_to_partners(veco, operator_profile_id: int):
    """Assigns an operator_profile_id to Managed/Partner Enterprises on a VECO
    This function should only be called for Enterprise/Shared VECOs or with the --partner flag

    Args:
        veco: Vco object from the EdgeOps VCO SDK
            Vco object should be authenticated and tested before handling to this function
        operator_profile_id: Int that is the Operator Profile Configuration Module id on the VECO

    Returns:
        True if the Enterprises are assigned or no valid enterprises to work on
        False for any Exceptions or errors
    """
    try:
        enterprise_lst = veco.get_enterprise_proxy_list()
    except VcoResponseError as error:
        print(f"Unable to retrieve Enterprise Proxy list on {veco.name}: {error}")
        return False
    # Since empty sets are valid responses, we should check if the list is empty and return True
    if len(enterprise_lst) == 0:
        return True
    enterprise_assigned_op_dict = {}
    print(f"Enterprise count: {len(enterprise_lst)}")
    for enterprise_id in enterprise_lst:
        try:
            enterprise_assigned_op_dict[
                enterprise_id] = veco.get_enterprise_proxy_operator_profiles(enterprise_id)
        except VcoResponseError as error:
            print(
                f"Unable to retrieve Enterprise Proxy Operator Profile list on {veco.name}: {error}"
            )
            return False
    # Dictionary Comprehension that returns only the Proxy Enterprises that do not have the profile
    # assigned
    enterprise_update_dict = {
        enterprise_id: op_list
        for (enterprise_id, op_list) in enterprise_assigned_op_dict.items()
        if operator_profile_id not in enterprise_assigned_op_dict[enterprise_id]
    }
    if len(enterprise_update_dict) == 0:
        return True
    # Loop through the update dict and add the missing operator profile
    for enterprise_proxy_id in enterprise_update_dict:
        enterprise_update_dict[enterprise_proxy_id].append(operator_profile_id)
        try:
            veco.update_op_list_for_proxy_enterprises(
                enterprise_proxy_id,
                enterprise_update_dict[enterprise_proxy_id]
            )
        except VcoResponseError as error:
            print(f"{veco.name}: {error}")
            return False
    return True


def upload_edge_images(
    veco_list, domain, username_str, password_str, min_veco_version, directory,
    netbox, profile=None, factory_image=None, add=False, partner_assign=False
):
    """Upload Edge Images and/or Factory Image onto VECO

    Args:
        vecoLst: List of VECOs to perform work upon
        domain: Domain name for the list of VECOs
        username_str: Super Operator Admin username for authentication
        password_str: Password for the Super Operator Admin
        min_veco_version: Minimal VECO Version to upload the image to
        directory: File location that contains zip files of images to upload onto the VECO
        netbox: Authenticated and checked netbox object
        profile: (Optional) String containing a profile name to match against
        factory_image: (Optional) Apply a Factory Image to the profile
        add: (Optional) Upload any missing images to a profile if it already exists on the VECO
        partner_assign: (Optional) Perform all Operator Profile actions even if this is a non-Shared
            VECO

    Returns:
        True: Edge Images and/or Factory Image are uploaded successfully and applied to a profile
        False: Failed to upload image, images are invalid, or at least one of the VECO uploads
            failed.
    """
    image_files = os.listdir(directory)
    file_lst = [directory + f for f in image_files if
                (os.path.isfile(directory + f) and f.endswith(".zip"))]
    if len(list(file_lst)) == 0:
        print(f"No zip files in {directory}. Exiting")
        return False
    if not zip_files_test(file_lst):
        exit(1)
    if profile is None:
        profile = extract_profile_name(file_lst, directory)
        if profile is None:
            print(
                f"No Manifest file in archive: {EDGE_MANIFEST_FILE} - "
                f"Please specify a Profile name or provide a valid Image file"
            )
            return False
    # Check if a tag was provided instead of a list of VECOs
    if (len(veco_list) == 1 and veco_list[0].startswith("tags")):
        print("Targetting VECOs by tag")
        veco_list = get_veco_list_by_tag(netbox,veco_list[0].split("_")[1])
    if not veco_list:
        print("No VECOs to work on, exiting")
        exit(1)
    image_upload_status = dict.fromkeys(veco_list, None)
    for veco_name in veco_list:
        operator_profile_id = None
        appmap_hash = None
        appmap_id_dict = None
        veco_type = None
        veco = newVco(f"{veco_name}.{domain}")
        try:
            veco.operator_login_password(username_str, password_str)
        except VcoRequestError as error:
            # If we fail a login, break out of the loop and mark it as failed
            print(f"Unable to login with {username_str} account to {veco.name}")
            image_upload_status[veco_name] = False
            continue
        try:
            version = veco.get_veco_version()
        except VcoRequestError as error:
            print(f"Unable to get version on {veco.name}")
            image_upload_status[veco_name] = False
            continue
        if Version(version) < Version(min_veco_version):
            print(f"Refusing to run: {veco.name} is on {version}. Min VECO Version is {min_veco_version}")
            image_upload_status[veco_name] = False
            continue
        print(f"Processing {veco.name}")
        # Detect VECO Type
        if veco_tenant_id := get_vm_tenant_id(netbox, veco.name):
            if get_tenant_type(netbox, veco_tenant_id) in VECO_ENTERPRISE_TENANTS:
                # At the moment, we only care for Non-Shared VECOs, but setting this in case of
                # future need
                veco_type = "Shared"
            else:
                veco_type = "Other"
        else:
            print(f"Unable to get Netbox information for {veco.name}, skipping")
            image_upload_status[veco_name] = False
            continue

        # Application Map operations - Note we will always upload AppMaps for any VECO type except
        # if we're doing factory images
        if not factory_image:
            appmap_file_lst = [directory + f for f in image_files if
                               (os.path.isfile(directory + f) and f == "applications.json")]
            if len(list(appmap_file_lst)) == 0:
                print(f"Unable to locate applications.json in {directory}. Exiting")
                return False
            # Get the SHA1 hash of the applications.json file
            if not (appmap_hash := get_file_sha1_hash(appmap_file_lst[0])):
                print(f"Unable to calculate md5 hash on {appmap_file_lst[0]}")
                image_upload_status[veco_name] = False
                break
            if not (appmap_id_dict := veco.get_application_map_id(appmap_hash)):
                # Upload the appmap if it does not exist on the VECO
                if not perform_file_upload(veco, appmap_file_lst, profile, "appmap"):
                    image_upload_status[veco_name] = False
                    continue
                appmap_id_dict = veco.get_application_map_id(appmap_hash)
                try:
                    veco.rename_application_map(appmap_id_dict['id'], profile)
                except VcoApplicationMapUpdateError as error:
                    print(
                        f"Failed to rename AppMap {appmap_id_dict['id']} on {veco.name}: {error}\n")
                    print("Proceeding on next task (non-fatal error)")
        # Veco type check - If this is a non-Shared VECO AND partner_assign flag is not passed,
        # only upload images
        if (veco_type == "Other" and not partner_assign):
            if not factory_image:
                if not perform_file_upload(veco, file_lst, profile, "edge"):
                    image_upload_status[veco_name] = False
                    continue
                else:
                    # Exit the loop for Private VECOs - All other steps will perform Operator
                    # Profile operations
                    image_upload_status[veco_name] = True
                    continue
            else:
                if not perform_file_upload(veco, file_lst, profile, "firmware"):
                    image_upload_status[veco_name] = False
                    continue
                else:
                    # Exit the loop for Private VECOs - All other steps will perform Operator
                    # Profile operations
                    image_upload_status[veco_name] = True
                    continue
        edge_profile_dict = get_edge_profiles(veco, profile)
        if edge_profile_dict is not None:
            # Perform uploads only if add is specified in case we have an existing profile
            if not edge_profile_dict["imageInfo"]["softwareDeprecated"]:
                operator_profile_id = edge_profile_dict["id"]
            else:
                print(f"Software Image is Depricated on {veco.name}. Skipping to next VECO")
                image_upload_status[veco_name] = False
                continue
            if not add:
                print("Profile exists, refusing to make changes without --add option")
                image_upload_status[veco_name] = False
                continue
            else:
                if not factory_image:
                    if not perform_file_upload(veco, file_lst, profile, "edge"):
                        image_upload_status[veco_name] = False
                        continue
                else:
                    if not perform_file_upload(veco, file_lst, profile, "firmware"):
                        image_upload_status[veco_name] = False
                        continue
        else:
            # If no profile exists, create one and upload files
            try:
                operator_profile_id = veco.create_operator_profile(profile)
            except VcoRequestError as error:
                print(f"Failed to create Operator Profile on {veco.name}: {error}")
                image_upload_status[veco_name] = False
                continue
            if not perform_file_upload(veco, file_lst, profile, "edge"):
                image_upload_status[veco_name] = False
                continue
        if not assign_images_to_op_profile(veco, profile, operator_profile_id):
            print("Unable to update Operator Profile. Moving to next VECO")
            image_upload_status[veco_name] = False
            continue
        # AppMap Profile operations
        if factory_image:
            image_upload_status[veco_name] = True
            continue
        try:
            application_map_dict = veco.get_application_map_configuration(operator_profile_id)
        except VcoResponseEmpty:
            print(f"No configurations found on {veco} for operator profile {operator_profile_id}")
            image_upload_status[veco_name] = False
            continue
        except VcoResponseError as error:
            print(f"Failed to locate the Application Map Configuration Object on the VECO: {error}")
            image_upload_status[veco_name] = False
            continue
        try:
            veco.assign_application_map(application_map_dict['id'], appmap_id_dict['logicalId'])
        except VcoConfigUpdateError as error:
            print(f"Failed to assign AppMap {application_map_dict['id']} to {veco.name}: {error}")
            image_upload_status[veco_name] = False
            continue
        if not assign_op_to_enterprises(veco, operator_profile_id):
            print(
                f"Failed to assign Operator Profiles to Enterprises, please manually check {veco.name}")
            image_upload_status[veco_name] = False
            continue
        if not assign_op_to_partners(veco, operator_profile_id):
            print(
                f"Failed to assign Operator Profiles to Partner Enterprises, please manually check {veco.name}")
            image_upload_status[veco_name] = False
            continue
        image_upload_status[veco_name] = True
    return image_upload_status


def main():
    """Main function: Authenticate to VECO(s) and manage Edge Images"""
    parser = argparse.ArgumentParser(description='Manage Edge Images on VECO')
    parser.add_argument('-o', '--orchestrator', required=True, dest="vecos",
                        type=str, help='VECOs to perform work on')
    parser.add_argument('-d', '--domain', required=True, action='store',
                        type=str.lower, help='Domain for the VECO')
    parser.add_argument('-u', '--username', required=True, action='store',
                        type=str, help='Service account username to authenticate on VECO')
    parser.add_argument('-p', '--password', required=True, action='store',
                        type=str, help='Service account password to authenticate on VECO')
    parser.add_argument('-a', '--action', required=True,
                        choices=['upload'],
                        dest='action', type=str.lower,
                        help='Action for script to perform. Valid option: upload')
    parser.add_argument('-n', dest='netbox_url',
                        type=str, help='Netbox URL')
    parser.add_argument('-t', dest='netbox_token',
                        type=str, help='Netbox Service Account Token')
    parser.add_argument('--profile', action='store',
                        type=str,
                        help='Profile name to match/use for Edge Images (overrides default profile naming)')
    parser.add_argument('--directory', required=True, action='store',
                        type=str,
                        help='Location for Edge images and other needed files i.e. Application Map JSON file')
    parser.add_argument('--minimum_veco_version', dest='min_veco_version',
                        type=str, required=True,
                        help='Minimum VECO version to perform action against')
    parser.add_argument('--factory_image', dest='factory_image', action='store_true',
                        help='Include and assign Factory Image to the Software Profile')
    parser.add_argument('--partner_assign', dest='partner_assign', action='store_true',
                        help='Perform Operator Profile creation and assignment steps for a Partner/Dedicated VECO')
    parser.add_argument('--add', dest='add',
                        action='store_true',
                        help='Add any missing Edge Images into an existing profile')
    args = parser.parse_args()
    directory = add_slash_to_path(args.directory)
    if not validate_path(directory):
        print(f"Invalid Directory: {directory}. Exiting")
        exit(2)
    args.vecos = [s.strip() for s in args.vecos.split(",") if s != ""]
    if args.action == 'upload':
        netbox = netbox_object(args.netbox_url, args.netbox_token)
        if not check_netbox(netbox):
            print(f"Unable to login to Netbox Instance: {args.netbox_url}")
            exit(1)
        image_upload_dict = upload_edge_images(args.vecos, args.domain,
                                               args.username, args.password,
                                               args.min_veco_version,
                                               directory,
                                               netbox,
                                               args.profile, args.factory_image, args.add,
                                               args.partner_assign)
        if not all(image_upload_dict.values()):
            failure_dict = {k: v for k, v in image_upload_dict.items() if not v}
            print("The following VECOs failed to complete image uploading:")
            for failed_veco in failure_dict:
                print(failed_veco)
            exit(3)
        else:
            print("Finished Upload tasks without error.")
            exit(0)


if __name__ == "__main__":
    main()
