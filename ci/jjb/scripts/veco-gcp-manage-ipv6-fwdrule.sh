#!/bin/bash
# Create and delete ipv6 forwarding rule for GCP LB

set -x
# Function to display usage
usage() {
    echo "Usage:"
    echo "$0 create <PROJECT_ID> <FORWARDING_RULE_NAME> <REGION> <BACKEND_SERVICE> <IPV6_ADDRESS> <IPV6_COLLECTION> <PORTS>"
    echo "$0 delete <PROJECT_ID> <FORWARDING_RULE_NAME> <REGION>"
    exit 1
}

# Check if the first parameter is provided
if [[ -z $1 ]]; then
    echo "Error: ACTION parameter is missing."
    usage
fi

# Parse command-line arguments
ACTION=$1
PROJECT_ID=$2
FORWARDING_RULE_NAME=$3
REGION=$4
BACKEND_SERVICE=$5
IPV6_ADDRESS=$6
IPV6_COLLECTION=$7
PORTS=$8

check_forwarding_rule_exists() {
   gcloud compute forwarding-rules describe $FORWARDING_RULE_NAME \
         --region $REGION \
         &> /dev/null \
         && echo true \
         || echo false
}

create_or_update_forwarding_rule() {
   rule_exists=$(check_forwarding_rule_exists)
   if $rule_exists; then
      # If forwarding rule exists, update it
      echo "Forwarding rule $FORWARDING_RULE_NAME exists, skipping..."
   else
      # If forwarding rule does not exist, create it
      echo "Forwarding rule $FORWARDING_RULE_NAME does not exist, creating..."
      gcloud compute forwarding-rules create $FORWARDING_RULE_NAME \
               --address $IPV6_ADDRESS \
               --backend-service $BACKEND_SERVICE \
               --ip-collection $IPV6_COLLECTION \
               --ip-collection-region $REGION \
               --ip-protocol 'TCP' \
               --ip-version 'IPV6' \
               --load-balancing-scheme 'EXTERNAL' \
               --network-tier 'PREMIUM' \
               --ports $PORTS \
               --project $PROJECT_ID
   fi
}

delete_forwarding_rule() {
   rule_exists=$(check_forwarding_rule_exists)
   if $rule_exists; then
      echo "Deleting forwarding rule $FORWARDING_RULE_NAME..."
      gcloud compute forwarding-rules delete $FORWARDING_RULE_NAME \
            --project $PROJECT_ID \
            --region $REGION \
            --quiet
   else
      echo "Forwarding rule $FORWARDING_RULE_NAME does not exist, skipping deletion..."
   fi
}

# Check parameters based on the ACTION value
case $ACTION in
    create)
        if [[ $# -ne 8 ]]; then
            echo "Error: Insufficient parameters for action 'create'."
            usage
        else
            create_or_update_forwarding_rule
        fi
        ;;
    delete)
        if [[ $# -lt 4 ]]; then
            echo "Error: Insufficient parameters for action 'delete'."
            usage
        else
            delete_forwarding_rule
        fi
        ;;
    *)
        echo "Error: Invalid ACTION. Allowed values are 'create' or 'delete'."
        usage
        ;;
esac
