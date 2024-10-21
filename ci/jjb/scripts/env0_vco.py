""" create environment in env0 """
import argparse
import json
import time
import ast
import sys
from .env0 import Env0

parser = argparse.ArgumentParser()

##### Arguments Parser ######
parser.add_argument(
    "-a",
    "--action",
    "--actionType",
    dest="actionType",
    required=True,
    choices=("create", "destroy"),
    help="action type",
)
parser.add_argument(
    "-aN",
    "--account",
    "--accountName",
    dest="accountName",
    required=True,
    choices=("sase-engr-dev", "sase-test", "sase-preprod", "sase-prod"),
    help="account name where we provision vco",
)
parser.add_argument(
    "-v", "--vco", "--vcoName", dest="vcoName", required=True, help="vco name"
)
parser.add_argument(
    "-r",
    "--region",
    "--regionName",
    dest="regionName",
    required=True,
    help="region name where we provision vco",
)
parser.add_argument(
    "-auth",
    "--basicAuth",
    dest="auth",
    required=True,
    help="Basic auth to connect env0 via API key",
)
parser.add_argument(
    "-u",
    "--env0Url",
    dest="commonUrl",
    required=True,
    choices=("https://api.env0.com",),
    help="env0 url where we deploy VCO",
)
parser.add_argument(
    "-e",
    "--envType",
    dest="envType",
    default="VMware SASE Orchestrators",
    required=False,
    help="sub project name which present under env0",
)
parser.add_argument(
    "-t",
    "--template",
    "--templateName",
    dest="templateName",
    required=False,
    default="orchestrator_nonprod",
    help="template name which present in env0",
)
parser.add_argument(
    "-b",
    "--branch",
    "--branchName",
    dest="branchName",
    required=False,
    default="main",
    help="Branch name which gonna use to clone repo",
)
parser.add_argument("-ami", "--amiId", dest="ami", required=True, help="ami id of vco")
parser.add_argument(
    "-bN",
    "--bucket_name",
    dest="bucket_name",
    required=True,
    help="bucket name to store terraform state file",
)
parser.add_argument(
    "-bR",
    "--bucket_region",
    dest="bucket_region",
    required=True,
    help="S3 bucket region",
)
parser.add_argument(
    "-dT",
    "--dynamodb_table",
    dest="dynamodb_table",
    required=True,
    help="name of dynamodb table name",
)
parser.add_argument(
    "-o", "--optional-vars", nargs="*", help="Additional key-value pairs"
)

args = parser.parse_args()

accountName = args.accountName
actionType = args.actionType
ami = args.ami
auth = args.auth
branchName = args.branchName
bucket_name = args.bucket_name
bucket_region = args.bucket_region
commonUrl = args.commonUrl
dynamodb_table = args.dynamodb_table
envType = args.envType
region = args.regionName
templateName = args.templateName
vcoName = args.vcoName
optional_vars = args.optional_vars


def process_addn_vars(**kwargs):
    """
    Convert key value json pairs to env0 supported json format
    """
    json_dict = []
    if kwargs:
        for key, value in kwargs.items():
            try:
                # Check if value is of type json
                result = json.loads(value)
                # If value is boolean raise value error
                if isinstance(result, bool):
                    raise json.JSONDecodeError("Not a Valid JSON, Its a Boolean", "", 0)
                json_dict.append(
                    {
                        "name": key,
                        "value": f"{value}",
                        "type": 1,
                        "schema": {"format": "JSON"},
                    }
                )
            except json.JSONDecodeError:
                # If value is not json, evaluate if string is list or string
                try:
                    result = ast.literal_eval(value)
                    if isinstance(result, list):
                        json_dict.append(
                            {
                                "name": key,
                                "value": f"{value}",
                                "type": 1,
                                "schema": {"format": "JSON"},
                            }
                        )
                except (SyntaxError, ValueError):
                    json_dict.append({"name": key, "value": value, "type": 1})
    return json_dict


def main():
    """
    check project then check environment
    If not will create a environment and deploy the env
    otherwise will deploy the existing env
    """

    vco_obj = Env0(commonUrl, auth, accountName, region, envType)
    if actionType == "create":
        create_vco = vco_obj.deploy_env(
            vcoName,
            templateName,
            ami,
            bucket_name,
            bucket_region,
            dynamodb_table,
            process_addn_vars(**dict(arg.split("=") for arg in optional_vars or [])),
            branchName,
        )
        print(json.dumps(create_vco, indent=4))
        print("Validating the status of deployment")
        count = 60  # Keeping loop for 30mins
        while count > 0:
            check_deploy = vco_obj.check_env(vcoName)
            print(
                f"Deployment {vcoName} environment still in progress. Current status {check_deploy['status']}"
            )
            if check_deploy["status"] == "FAILED":
                print(
                    f"Environment {vcoName} deployment is failed. Check manually & reinitate the deployment"
                )
                print(json.dumps(check_deploy["latestDeploymentLog"], indent=4))
                sys.exit(1)
            elif check_deploy["status"] != "ACTIVE" and count == 1:
                print(
                    f"Environment {vcoName} creation is not completed and running longer than 30mins. Check Manually"
                )
                print(json.dumps(check_deploy, indent=4))
                sys.exit(1)
            else:
                count = count - 1
                time.sleep(30)

            if check_deploy["status"] == "ACTIVE":
                print("Environment deployment is active")
                break

    elif actionType == "destroy":
        destroy_vco = vco_obj.destroy_env(vcoName)
        # return if no vco found to destroy
        if destroy_vco is None:
            return
        print(json.dumps(destroy_vco, indent=4))
        print("Validating the status of destroy")
        count = 60  # Keeping loop for 30mins
        while count > 0:
            check_deploy = vco_obj.check_env(vcoName)
            print(
                f"Destroy {vcoName} environment is still in progress. Current status {check_deploy['status']}"
            )

            if check_deploy["status"] == "FAILED":
                print(f"Environment {vcoName} destroy failed")
                print(json.dumps(check_deploy["latestDeploymentLog"], indent=4))
                sys.exit(1)
            elif check_deploy["status"] != "INACTIVE" and count == 1:
                print(
                    f"Environment {vcoName} destroy did not complete and running longer than 30mins. Check Manually"
                )
                print(json.dumps(check_deploy, indent=4))
                sys.exit(1)
            else:
                count = count - 1
                time.sleep(30)

            if check_deploy["status"] == "INACTIVE":
                print(f"Environment {vcoName} is destroyed")
                break

    else:
        print("No action taken")


if __name__ == "__main__":
    main()
