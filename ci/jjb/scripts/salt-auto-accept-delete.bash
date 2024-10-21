#!/bin/bash

# Script to Auto accept salt minion or delete the minion from salt master using saltapi service

# Check if all required arguments are passed
if [ "$#" -lt 3 ]; then
  echo "Usage: $0 <arg1> <arg2> <arg3>"
  echo "Argument1: Salt Master FQDN"
  echo "Argument2: Name of the salt minion"
  echo "Argument3: Action to be performed. accept or delete the key"
  exit 1
fi

# Print error message in Red color and exit the script
die() {
    # Print the arguments passed to the function in Red color and exit
    echo -e "${RED}$@ ${NC}" >&2
    exit 1
}

# Retrieve and use the command-line arguments
SALT_MASTER="$1"
SALT_MINION="$2"
ACTION="$3"
RED='\033[0;31m'

SecretName="saltapi-svc-user-cred"

# Test AWS connectivity
AWS_ACCOUNT_ID=$(
    aws sts get-caller-identity \
        --query Account \
        --output text || die \
        "Unable to get the source account ID. Aborting."
)

# List available secrets
secret_list=$(
   aws secretsmanager list-secrets \
      --region us-west-2 \
      --output text \
      --query "SecretList[?Name=='$SecretName']"
)

# Fetch the secret value if exist
if [ "${secret_list}" != "" ]; then
   SALT_API_PASS=$(
      aws secretsmanager get-secret-value \
            --region us-west-2 \
            --secret-id $SecretName \
            --query SecretString \
            --output text
   )
else
   die "SaltAPI secret not found in AWS"
fi

# Check if the environment variable "SALT_API_PASS" is set
#if [ -z "$SALT_API_PASS" ]; then
#  echo "Error: SALT_API_PASS environment variable is not set."
#  exit 1  # Exit with an error code
#fi

# Variables
SALT_MASTER_URL="https://$SALT_MASTER:8443"
LOGIN_URL="$SALT_MASTER_URL/login"
HEADER='Accept: application/json'
SALT_API_USER='saltapi'

#Login to Salt API Master
login_response=$(
    curl -sSk "$LOGIN_URL" \
        -H "$HEADER" \
        -d username="$SALT_API_USER" \
        -d password="$SALT_API_PASS" \
        -d eauth=pam
)

# Check if the "token" key is present in the JSON response
if echo "$login_response" | grep -q '"token":'; then
   token=$(echo "$login_response" | grep -o '"token": "[^"]*' | cut -d'"' -f4)
   TOKEN_HEADER="X-Auth-Token: $token"
elif echo "$login_response" | grep -q "401 Unauthorized"; then
   echo "Could not authenticate using provided credentials"
   exit 1
else
   echo "Login failed: Check for errors: $login_response"
   exit 1
fi

if [ "$ACTION" = "accept" ]; then
   accept_response=$(
        curl -sSk "$SALT_MASTER_URL" \
            -H "$HEADER" \
            -H "$TOKEN_HEADER" \
            -d client='wheel' \
            -d fun='key.accept' \
            -d match=$SALT_MINION
    )
   if echo "$accept_response" | grep -q '"success": true'; then
      echo "Success: Minion $SALT_MINION key is accepted"
   else
      echo "Failed: Minion $SALT_MINION key is not accepted."
      die $accept_response
   fi

elif [ "$ACTION" = "delete" ]; then
   delete_response=$(
        curl -sSk "$SALT_MASTER_URL" \
            -H "$HEADER" \
            -H "$TOKEN_HEADER" \
            -d client='wheel' \
            -d fun='key.delete' \
            -d match=$SALT_MINION
    )
   if echo "$delete_response" | grep -q '"success": true'; then
      echo "Success: Minion $SALT_MINION key is deleted"
   else
      echo "Failed: Minion $SALT_MINION key is not deleted."
      die $delete_response
   fi

else
   die "Incorrect action $ACTION"
fi
