"""Module to manage env0 environments specifically for VMware Cloud Orchestrators
"""
import atexit
import sys
import requests


class Env0:
    """Class to help using env0 API's"""

    def __init__(self, url, auth_token, acc_name, region, project_name="VCO"):
        """Create the object.

        Args:
            url: env0 api url
            auth_token: Base64 encoded (username & password) auth token to access env0 api's.
            acc_name: Parent project name denoting AWS Account Name to connect to. (Eg: sase-prod, sase-test)
            region: Env0 subproject name denoting AWS region's (Eg, us-west-2, us-east-1)
            project_name: Subproject to above region where environments will be managed

        Yields:
            org_id: env0 Organization ID

        Raises:
            Exit when no organization found
        """
        self.org_name = "SEBU Edge Operations"
        self.base_url = url
        self.auth_token = auth_token
        self.account_name = acc_name
        self.region = region
        self.project_name = project_name
        self.org_id = None
        self.account_id = None
        self.region_id = None
        self.project_id = None
        self.template_id = None
        self.env_id = None
        self.url_header = {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Basic {self.auth_token}",
        }
        self.timeout = None
        self.env_name = None
        self.ami = None
        self.teleport_auth_server = None
        self.teleport_ca_pin = None
        self.teleport_auth_token = None
        self.s3_bucket_name = None
        self.s3_bucket_region = None
        self.dynamodb_table = None
        self.git_branch = None
        self.config_body = {}
        self.available_projects = None
        self.available_templates = None
        self.terraform_backend_config = None
        self.config_changes = None
        self.deploy_attributes = None
        self.create_payload = None
        self.deploy_payload = None
        self.available_environs = None
        self.create_environment = None
        self.deploy_environment = None
        self.env_exists = None
        self.env_info = None
        self.optional_vars = None
        self.destroy_environment = None
        self._get_env0_org_id_()
        self._get_env0_project_id_()

    @staticmethod
    def _exit_handler_():
        """Internal Usage: Displays the message and exit the script

        Args: None
        Raises: None
        Returns: None
        """
        print("Script failed and did not proceed further")
        sys.exit(1)

    def _get_request(self, get_url):
        """Internal Usage: Invokes HTTP requests GET method for the given URL

        Args:
            get_url: URL to connect
        Returns:
            JSON formatted output of post method
        Raises:
            Exit if the status code is not success
        """
        try:
            response = requests.get(
                url=get_url, headers=self.url_header, timeout=self.timeout
            )
            response.raise_for_status()  # Raise an exception if response status code is not successful

        except requests.exceptions.RequestException as error:
            print(f"An error occurred: {error}")
            atexit.register(self._exit_handler_())

        return response.json()

    def _post_request(self, url, data=None):
        """Internal Usage: Invokes HTTP requests POST method for the given URL

        Args:
            url: URL to connect
            data: json body to send to post method. Default will be None
        Returns:
            JSON formatted output of post method
        Raises:
            Exit if the status code is not success

        """
        try:
            response = requests.post(
                url, json=data, headers=self.url_header, timeout=self.timeout
            )
            response.raise_for_status()  # Raise an exception if response status code is not successful
        except requests.exceptions.RequestException as error:
            print(f"An error occurred: {error}")
            atexit.register(self._exit_handler_())
        return response.json()

    def _get_env0_org_id_(self):
        """Internal Usage: Check if given organization exists in env0

        Returns:
            org_id: Organization ID
        Raises:
            Exit if no organization found
        """
        url = f"{self.base_url}/organizations"
        available_orgs = self._get_request(url)
        for item in available_orgs:
            if item["name"] == self.org_name:
                self.org_id = item["id"]
                break
        if self.org_id is None:
            print("No Env0 Organization found")
            atexit.register(self._exit_handler_())

    def _get_env0_projects(self):
        """Internal Usage: Get all available env0 projects for the given organization

        Args: None
        Returns:
            available_projects: All available projects
        Raises: None
        """
        url = f"{self.base_url}/projects?organizationId={self.org_id}"
        self.available_projects = self._get_request(url)

    def _get_env0_account_id_(self):
        """Internal Usage: Fetches the ID of the parent project

        Args: None
        Yields:
            account_id: Logical ID of the parent project

        Raises: Exit if no project found
        """
        for projects in self.available_projects:
            if projects["name"] == self.account_name:
                self.account_id = projects["id"]
                break
        if self.account_id is None:
            print(f"Project {self.account_name} does not exist in env0")
            atexit.register(self._exit_handler_())

    def _get_env0_region_id_(self):
        """Internal Usage: Fetches the ID of the region wise sub project

        Args: None
        Yields:
            region_id: Logical ID of the region wise sub-project

        Raises: Exit if no project found
        """
        for projects in self.available_projects:
            if (
                projects["name"] == self.region
                and projects["parentProjectId"] == self.account_id
            ):
                self.region_id = projects["id"]
                break
        if self.region_id is None:
            print(
                f"Project {self.account_name} exist, but sub-project {self.region} doesn't exist in env0"
            )
            atexit.register(self._exit_handler_())

    def _get_env0_project_id_(self):
        """Internal Usage: Fetches the ID of the parent project of the environment,
        which is also subproject of account & region level projects

        Args: None
        Yields:
            project_id: Logical ID of the project where environment is managed

        Raises: Exit if no project found
        """
        self._get_env0_projects()
        self._get_env0_account_id_()
        self._get_env0_region_id_()
        for projects in self.available_projects:
            if (
                projects["name"] == self.project_name
                and projects["parentProjectId"] == self.region_id
            ):
                self.project_id = projects["id"]
                break
        if self.project_id is None:
            print(
                f"Projects '{self.account_name}/{self.region}' exists, \
                    but sub-project '{self.project_name}' doesn't exist in env0"
            )
            atexit.register(self._exit_handler_())

    def _get_env0_templates(self):
        """Internal Usage: Fetches all the available templates

        Args: None
        Yields:
            available_templates: All available templates

        Raises: None
        """
        url = f"{self.base_url}/blueprints?organizationId={self.org_id}"
        self.available_templates = self._get_request(url)

    def _load_templates(self):
        """Internal Usage: Load all required templates required for create/deploy api calls

        Args: None
        Yields:
            terraform_backend_config: Terraform backend configurations
            config_changes: Configurations pushed to create/deploy env
            deploy_attributes: Required deployment attributes
            create_payload: Json data loaded using above variables
            deploy_payload: Json data loaded using above variables

        Raises: None
        """
        self.terraform_backend_config = ",".join(
            [
                f"bucket={self.s3_bucket_name}",
                f"key={self.env_name}",
                f"region={self.s3_bucket_region}",
                "encrypt=true",
                f"dynamodb_table={self.dynamodb_table}",
                f"workspace_key_prefix=vcos/{self.region}",
            ]
        )

        self.config_changes = [
            {"name": "vco_instance_name", "value": f"{self.env_name}", "type": 1},
            {"name": "ami_id", "value": f"{self.ami}", "type": 1},
            {"name": "region", "value": f"{self.region}", "type": 1},
            {"name": "environment", "value": f"{self.account_name}", "type": 1},
            {
                "name": "ENV0_TERRAFORM_BACKEND_CONFIG",
                "value": f"{self.terraform_backend_config}",
                "type": 0,
            },
            *self.optional_vars,
        ]

        self.deploy_attributes = {
            "blueprintId": f"{self.template_id}",
            "deploymentType": "deploy",
            "blueprintRevision": f"{self.git_branch}",
            "userRequiresApproval": False,
        }

        self.create_payload = {
            "name": f"{self.env_name}",
            "projectId": f"{self.project_id}",
            "requiresApproval": False,
            "configurationChanges": self.config_changes,
            "deployRequest": self.deploy_attributes,
            "workspaceName": self.env_name,
        }

        self.deploy_payload = {
            **self.deploy_attributes,
            "configurationChanges": self.config_changes,
        }

    def _list_env0_envs(self):
        """Internal Usage: Fetch all available environments for the project

        Args: None

        Yields:
            available_environs: Available environment details

        Raise: None

        """
        list_envs_url = f"{self.base_url}/environments?projectId={self.project_id}"
        self.available_environs = self._get_request(list_envs_url)

    def _create_env(self):
        """Internal Usage: Invokes HTTP Post request to create environment in env0

        Args: None
        Yields:
            create_environment: Details of created environment

        Raise: None
        """
        url = f"{self.base_url}/environments"
        self.create_environment = self._post_request(url, self.create_payload)
        print(
            f"Environment {self.env_name} created under {self.account_name}/{self.region}/{self.project_name}"
        )
        return self.create_environment

    def _deploy_env(self):
        """Internal Usage: Invokes HTTP Post request to deploy environment in env0

        Args: None
        Returns:
            deploy_environment: Details of initiated deployed environment

        Raise: None
        """
        url = f"{self.base_url}/environments/{self.env_id}/deployments"
        self.deploy_environment = self._post_request(url, self.deploy_payload)
        print(
            f"Environment {self.env_name} deployment initiated {self.account_name}/{self.region}/{self.project_name}"
        )
        return self.deploy_environment

    def _destroy_env(self):
        """Internal Usage: Invokes HTTP Post request to destroy environment in env0

        Args: None
        Returns:
            destroy_environment: Details of environment initiated for destroy.

        Raise: None
        """
        url = f"{self.base_url}/environments/{self.env_id}/destroy"
        self.destroy_environment = self._post_request(url)
        print(
            f"Environment {self.env_name} destroy initiated {self.account_name}/{self.region}/{self.project_name}"
        )
        return self.destroy_environment

    def get_env0_template_id_(self, template_name):
        """Internal Usage: Finds the logical ID for the given template name

        Args:
            template_name: Name of the template

        Returns:
            template_id: Logical ID of the template

        Raises:
            Exit if no templates found
        """
        self._get_env0_templates()
        for templates in self.available_templates:
            if templates["name"] == template_name:
                self.template_id = templates["id"]
                break
        if self.template_id is None:
            print(f"{template_name} doesn't exist in following env0 {self.base_url}")
            atexit.register(self._exit_handler_())
        return self.template_id

    def check_env(self, env_name):
        """Check if given environment if available in env0

        Args:
            env_name: Name of the env0 environment

        Yields:
            env_exists: Boolean value to show if environment is available
            env_id: Logical ID of the env0 environment if available
            env_info: Complete info about the env0 environment

        Raises:
            env_info: Details of existing environment
        """
        self.env_exists = False
        self.env_id = None
        self.env_info = None
        self._list_env0_envs()
        for envs in self.available_environs:
            if envs["name"] == env_name:
                self.env_exists = True
                self.env_id = envs["id"]
                self.env_info = envs
                return self.env_info
        return None

    def deploy_env(
        self,
        envname,
        templatename,
        ami,
        s3bucketname,
        s3bucketregion,
        dynamodb_table,
        optional_vars,
        gitbranch="main",
    ):
        """Create or deploy env0 environment

        Args:
            envname: Name of the env0 environment
            templatename: Name of the template used to create environment
            ami: AMI ID for the instance launch
            s3bucketname: S3 Bucket where terraform state will be stored
            s3bucketregion: S3 Bucket region
            dynamodb_table: Dynamodb table used for terraform state locking
            gitbranch: Git repository branch to use

        Returns:
            create_environment: Details of the environment if created
            deploy_environment:  Details of the environment if deployed

        Raises: None
        """
        self.env_name = envname
        self.ami = ami
        self.s3_bucket_name = s3bucketname
        self.s3_bucket_region = s3bucketregion
        self.dynamodb_table = dynamodb_table
        self.optional_vars = optional_vars
        self.git_branch = gitbranch
        self.config_body = {}
        self.check_env(self.env_name)
        self.get_env0_template_id_(templatename)
        self._load_templates()
        environment = None
        if self.env_exists:
            print(
                f"Environment {self.env_name} exists. Re-initiating deployment with configuration changes"
            )
            environment = self._deploy_env()
        else:
            print(
                f"Environment {self.env_name} doesn't exists. Starting environment creation & deploying"
            )
            environment = self._create_env()
        return environment

    def destroy_env(self, envname):
        """Destroy an env0 environment

        Args:
            envname: Name of the env0 environment to be destroyed

        Returns:
            destroy_environment: Details of the environment initiated for destroy

        Raises: None
        """
        self.env_name = envname
        self.check_env(self.env_name)
        environment = None
        # Destroy only if Environment exists and is active
        if self.env_exists:
            if self.env_info["status"] == "ACTIVE":
                environment = self._destroy_env()
            else:
                print(
                    f"Environment {self.env_name} exists but in INACTIVE state, no action required"
                )
        else:
            print(
                f"Environment {self.env_name} doesn't exist \
                    under {self.account_name}/{self.region}/{self.project_name} and no action required"
            )
        return environment
