"""Cloudflare-related classes &c."""

import requests
from requests.exceptions import HTTPError, RequestException


class CloudflareHandler:
    """Handle VCO Cloudflare related activities"""

    def __init__(self, email, auth_key, zone_id):
        self.email = email
        self.auth_key = auth_key
        self.zone_id = zone_id
        self.base_url = (
            f"https://api.cloudflare.com/client/v4/zones/{self.zone_id}/dns_records"
        )

    def get_headers(self):
        """Build up headers for all API calls."""
        return {
            "X-Auth-Email": self.email,
            "X-Auth-Key": self.auth_key,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _request(method, url, **kwargs):
        """Custom HTTP request class"""
        try:
            response = requests.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except HTTPError as http_err:
            try:
                error_details = response.json()
                errors = error_details.get(
                    "errors", [{"message": "No error message returned"}]
                )[0]
                error_message = errors.get("message")
                error_code = errors.get("code", "No error code returned")
                if error_code == 81053:
                    print("Record already exists. Continuing.")
                    return
                print(
                    f"HTTP error occurred: {http_err}\n"
                    f"Error Code: {error_code}\nError Message: {error_message}"
                )
            except ValueError:  # response.json() may raise ValueError if not JSON
                print(f"HTTP error occurred: {http_err}\nError: Non-JSON response")
            raise
        except RequestException as err:
            print(f"Error occurred: {err}")
            raise
        except Exception as err:
            print(f"An error occurred: {err}")
            raise

    def get_dns_records(
        self, record_name=None, record_type=None, content=None, per_page=100
    ):
        """Get all DNS records based on record name, type, or content"""
        params = {"per_page": per_page}
        if record_name:
            params["name"] = record_name
        if record_type:
            params["type"] = record_type
        if content:
            params["content"] = content

        for page in range(1, 1000000):
            params["page"] = page
            data = self._request(
                "GET", self.base_url, headers=self.get_headers(), params=params
            )
            if not data["result"]:
                return

            for item in data["result"]:
                yield item

            if page >= data["result_info"]["total_pages"]:
                return

    def update_dns_record(self, record_id, data):
        """Overwrites existing DNS record"""
        url = f"{self.base_url}/{record_id}"
        return self._request("PUT", url, headers=self.get_headers(), json=data)

    def create_dns_record(self, data):
        """Creates new DNS record"""
        return self._request(
            "POST", self.base_url, headers=self.get_headers(), json=data
        )

    def delete_dns_record(self, record_id):
        """Deletes a DNS record"""
        url = f"{self.base_url}/{record_id}"
        return self._request("DELETE", url, headers=self.get_headers())

    def create_cname_record(self, name, target):
        """Leverages create DNS record to create a CNAME record"""
        return self.create_dns_record(
            data={"content": target, "name": name, "type": "CNAME", "proxied": False}
        )

    def get_dns_records_for_fqdn(self, fqdn):
        """Get A, AAAA, and CNAME records for a given FQDN."""
        return {
            record_type: list(
                self.get_dns_records(record_name=fqdn, record_type=record_type.upper())
            )
            for record_type in ["a", "aaaa"]
        } | {
            "cname": list(self.get_dns_records(content=fqdn, record_type="CNAME")),
        }

    def retire_aws_records(self, url_to_retire: str):
        """Prepend 'old-' to any A or AAAA records for specified FQDNs."""
        print(f"Retrieving DNS records for {url_to_retire}")
        records = self.get_dns_records_for_fqdn(url_to_retire)

        for record_type in ["a", "aaaa"]:
            for record in records.get(record_type, []):
                print(
                    f"Updating record {record['id']} - Changing name to 'old-{record['name']}'"
                )
                self.update_dns_record(
                    record_id=record["id"],
                    data={
                        "name": f"old-{record['name']}",
                        "content": record["content"],
                        "type": record["type"],
                    },
                )

    def migrate_records(
        self,
        url_to_repoint: str = "",
        new_base_url: str = "",
        new_cname: str = "",
    ):
        """
        Migrates the DNS records for a given URL.

        1. Creates CNAME to new URL if CNAME is provided. Uses FQDN otherwise.
        2. Retires old URL A/AAAA records
        3. CNAMEs old URL to new CNAME/FQDN
        """

        if not new_cname:
            print(f"New CNAME not specified. Using base URL '{new_base_url}' instead.")
            new_cname = new_base_url
        else:
            print(f"Creating CNAME record: {new_cname} -> {new_base_url}")
            self.create_cname_record(new_cname, new_base_url)

        print(f"Retiring AWS records for {url_to_repoint}")
        self.retire_aws_records(url_to_repoint)

        print(f"Creating CNAME record: {url_to_repoint} -> {new_cname}")
        self.create_cname_record(url_to_repoint, new_cname)

        print(
            f"Migrating CNAME records pointing to {url_to_repoint} to point to {new_cname}"
        )
        for record in self.get_dns_records(record_type="CNAME", content=url_to_repoint):
            record["content"] = new_cname
            print(
                f"Updating CNAME record {record['id']} - Changing content to {new_cname}"
            )
            self.update_dns_record(
                record_id=record["id"],
                data={"content": new_cname, "name": record["name"], "type": "CNAME"},
            )
