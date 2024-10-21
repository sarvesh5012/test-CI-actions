""" Create GCP VECO environment in env0 """
#!/usr/bin/env python3
import argparse
import sys
import time
import os
import json
from typing import Optional

from edgeops_env0.env0 import Env0
from edgeops_env0.edgeops_gcp import EdgeOpsEnv0VecoGCP
from edgeops_env0.cli import base_arg_parser, load_tf_data

# Max time to allow for environment create, update or destroy.
MAX_ENV_ACTION_TIME_SECS = 30 * 60

# The name of the env-var with the Env0 auth token.
ENV_VAR_ENV0_AUTH_TOKEN = "ENV0_TOKEN"


class GcpParams:
    """Class to set GCP paramaters"""

    def __init__(self, org, folder, account, region, project, template, env):
        self.org = org
        self.folder = folder
        self.account = account
        self.region = region
        self.project = project
        self.template = template
        self.env = env

        self._project_id = None

    @classmethod
    def from_args(cls, veco_env0: EdgeOpsEnv0VecoGCP, args):
        """Set veco env0 object based on args"""
        obj = cls(
            args.gcp_org_name,
            args.gcp_foldername,
            args.account_name,
            args.region_name,
            args.project_name,
            args.template_name,
            args.env_name,
        )
        obj._project_id = veco_env0.get_gcp_subproject_id(
            obj.org, obj.folder, obj.account, obj.region, obj.project
        )
        return obj

    @property
    def project_id(self):
        """Return Project ID"""
        if self._project_id is None:
            raise ValueError("Have not initialized the project ID yet")
        return self._project_id

    def get_environment_data(self, env0_obj: Env0) -> Optional[dict]:
        """env0 environment data"""
        try:
            return next(
                env
                for env in env0_obj.iter_environments_by_name(self.env, refresh=True)
                if env.get("projectId") == self.project_id
            )
        except StopIteration:
            return None

    def create_environment(self, veco_obj: EdgeOpsEnv0VecoGCP, terraform_data):
        """Create environment"""
        veco_obj.create_gcp_environment(
            self.org,
            self.folder,
            self.account,
            self.region,
            self.project,
            self.template,
            self.env,
            terraform_data,
        )

    def deploy_environment(self, veco_obj: EdgeOpsEnv0VecoGCP, terraform_data):
        """Deploy existing environment"""
        veco_obj.deploy_gcp_environment(
            self.org,
            self.folder,
            self.account,
            self.region,
            self.project,
            self.template,
            self.env,
            terraform_data,
        )

    def destroy_environment(self, veco_obj: EdgeOpsEnv0VecoGCP):
        """Destroy environment"""
        veco_obj.destroy_gcp_environment(
            self.org, self.folder, self.account, self.region, self.project, self.env
        )


def await_environment_ready(
    env0: Env0,
    veco_obj: EdgeOpsEnv0VecoGCP,
    gcp_params: GcpParams,
    success_status: str,
    action: str = "operation",
):
    """Wait for environment to be ready"""
    if veco_obj.env_settings.get("userRequiresApproval"):
        print("User approval required, skipping environment status check")
        return

    start_time = time.time()
    while time.time() - start_time < MAX_ENV_ACTION_TIME_SECS:
        environment_data = gcp_params.get_environment_data(env0)
        if environment_data is None:
            continue
        status = environment_data.get("status")
        if status == success_status:
            print(f"Environment {gcp_params.env} {action} successful")
            print(json.dumps(environment_data, indent=4))
            return
        elif status == "FAILED":
            print(f"Environment {gcp_params.env} {action} failed")
            print(json.dumps(environment_data, indent=4))
            sys.exit(1)
        else:
            print(f"Environment {gcp_params.env} {action} is still in progress")
        time.sleep(50)

    print(
        f"Environment {gcp_params.env} action is not completed within "
        f"{MAX_ENV_ACTION_TIME_SECS} seconds. Check manually."
    )


def check_create_veco_environment(
    env0: Env0,
    veco_obj: EdgeOpsEnv0VecoGCP,
    gcp_params: GcpParams,
    *_,
):
    """Check creation of VECO env0 environment"""
    environment = gcp_params.get_environment_data(env0)
    if environment is not None:
        await_environment_ready(env0, veco_obj, gcp_params, "ACTIVE", "create")
    else:
        print(f"Environment {gcp_params.env} does not exists")
        sys.exit(1)


def create_veco_environment(
    env0: Env0,
    veco_obj: EdgeOpsEnv0VecoGCP,
    gcp_params: GcpParams,
    terraform_data,
):
    """Create VECO env0 environment"""
    environment = gcp_params.get_environment_data(env0)
    # Fail only if environment exists and active,. Else re-deploy if inactive or failed
    if environment is not None:
        if environment["status"] not in ["INACTIVE", "FAILED"]:
            print(f"Environment {gcp_params.env} already exists")
            print(f"Details: {environment}")
            sys.exit(1)
        else:
            update_veco_environment(env0, veco_obj, gcp_params, terraform_data)
            return

    gcp_params.create_environment(veco_obj, terraform_data)

    print(f"Environment {gcp_params.env} creation initiated")
    time.sleep(30)
    await_environment_ready(env0, veco_obj, gcp_params, "ACTIVE", "create")


def update_veco_environment(
    env0: Env0,
    veco_obj: EdgeOpsEnv0VecoGCP,
    gcp_params: GcpParams,
    terraform_data,
):
    """Update VECO env0 environment"""
    environment = gcp_params.get_environment_data(env0)
    if environment is None:
        print(f"Environment {gcp_params.env} doesn't exist or not found")
        sys.exit(1)

    gcp_params.deploy_environment(veco_obj, terraform_data)
    print(f"Environment {gcp_params.env} deployment initiated")
    time.sleep(30)
    await_environment_ready(env0, veco_obj, gcp_params, "ACTIVE", "deploy")


def check_update_veco_environment(
    env0: Env0,
    veco_obj: EdgeOpsEnv0VecoGCP,
    gcp_params: GcpParams,
    *_,
):
    """Check updation of VECO env0 environment"""
    environment = gcp_params.get_environment_data(env0)
    if environment is not None:
        await_environment_ready(env0, veco_obj, gcp_params, "ACTIVE", "update")
    else:
        print(f"Environment {gcp_params.env} does not exists")
        sys.exit(1)


def destroy_veco_environment(
    env0: Env0,
    veco_obj: EdgeOpsEnv0VecoGCP,
    gcp_params: GcpParams,
    *_,
):
    """Destroy VECO env0 environment"""
    environment = gcp_params.get_environment_data(env0)
    if environment is None:
        print(f"Environment {gcp_params.env} does not exist")
        return

    if environment.get("status") == "INACTIVE":
        print(
            f"Environment {gcp_params.env} already in inactive state, No action required"
        )
        print(json.dumps(environment, indent=4))
        return

    gcp_params.destroy_environment(veco_obj)
    print(f"Environment {gcp_params.env} destruction initiated")
    time.sleep(30)
    await_environment_ready(env0, veco_obj, gcp_params, "INACTIVE", "destroy")


def check_destroy_veco_environment(
    env0: Env0,
    veco_obj: EdgeOpsEnv0VecoGCP,
    gcp_params: GcpParams,
    *_,
):
    """Check destroy of VECO env0 environment"""
    environment = gcp_params.get_environment_data(env0)
    if environment is not None:
        await_environment_ready(env0, veco_obj, gcp_params, "INACTIVE", "destroy")
    else:
        print(f"Environment {gcp_params.env} does not exists, skipping....")


#
# CLI
#


def env0_action(
    action,
    env0,
    veco_obj,
    gcp_params,
    tf_data,
):
    """Handle requested action and dispatch the appropriate method."""
    cases = {
        "create": create_veco_environment,
        "update": update_veco_environment,
        "destroy": destroy_veco_environment,
        "check_create": check_create_veco_environment,
        "check_update": check_update_veco_environment,
        "check_destroy": check_destroy_veco_environment,
    }

    # Retrieve the case function based on the action
    case_function = cases.get(action, default_switch)

    # Call the case function with the provided arguments
    case_function(
        env0,
        veco_obj,
        gcp_params,
        tf_data,
    )


def default_switch(*_):
    """Default switch case when incorrect action is passed"""
    print("Invalid action -r provided")


def get_args():
    """Get arguments"""
    parser = base_arg_parser("VECO env0 environment")
    parser.add_argument(
        "--action",
        required=True,
        choices=(
            "create",
            "update",
            "destroy",
            "check_create",
            "check_update",
            "check_destroy",
        ),
        help="Action to be performed",
    )
    parser.add_argument(
        "--gcp-org-name",
        default="vmw.saasdev.broadcom.com",
        help="GCP Org name for the VECO",
    )
    parser.add_argument(
        "--gcp-foldername",
        default="sdsdeg1-cstack",
        help="GCP folder for the VECO",
    )
    parser.add_argument(
        "--gcp-bucket",
        default="sdsdeg1-sdw-mp01-terraform-state",
        help="Cloud Storage bucket where Terraform states are stored",
    )
    parser.add_argument(
        "--terraform-data-file",
        help="File with required Terraform data. See example file in templates folder.",
        required=True,
    )
    parser.add_argument(
        "--terraform-schema-file",
        required=True,
        help="Terraform schema file in json format used for GCP VECO env0 deployment",
    )
    parser.add_argument(
        "--terraform-requires-approval",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="User approval for TF changes when environment is created/modified/deleted",
    )
    parser.add_argument(
        "--git-branch",
        default="main",
        help="Git Repo branch to be used in env0 module",
    )
    parser.add_argument("--dry-run", help="Want to dry run.", action="store_true")

    args = parser.parse_args()
    return args


def main():
    """Manage VECO env0 environment"""
    args = get_args()
    terraform_data = load_tf_data(args.terraform_data_file)
    env0 = Env0(
        args.url,
        os.environ[ENV_VAR_ENV0_AUTH_TOKEN],
        args.org_name,
        args.terraform_requires_approval,
        args.dry_run,
    )
    veco_obj = EdgeOpsEnv0VecoGCP(env0, args.terraform_schema_file)

    workspace_prefix = f"{args.gcp_org_name}/{args.gcp_foldername}/{args.account_name}/{args.region_name}/VECOs/"
    veco_obj.update_terraform_backend_config(
        {"prefix": workspace_prefix, "bucket": args.gcp_bucket}
    )
    # Set Terraform auto approval in env0
    if args.terraform_requires_approval is not None:
        veco_obj.update_environment_settings(
            {"userRequiresApproval": args.terraform_requires_approval}
        )
    veco_obj.update_environment_settings({"blueprintRevision": args.git_branch})

    gcp_params = GcpParams.from_args(veco_obj, args)

    env0_action(
        args.action,
        env0,
        veco_obj,
        gcp_params,
        terraform_data,
    )


if __name__ == "__main__":
    main()
