#! /usr/bin/env bash

set -eux

# source "$WORKSPACE"/cws-venv/bin/activate

# Args
ACTION=$1
SITE=$2
NODE=$3
VERSION=$4

# General ansible options
# Inventory paths
BASE_PATH="$WORKSPACE/cws"
INV_PATH="${BASE_PATH}/inventory"
HELPER_INV="$INV_PATH/${SITE}_${VERSION}_helper.yml"
IGW_INV="$INV_PATH/${SITE}_${VERSION}_inboundgateway.yml"
PROXY_INV="$INV_PATH/${SITE}_${VERSION}_proxy.yml"
SURR_INV="$INV_PATH/${SITE}_${VERSION}_surrogate.yml"

# Can also get inventory path based on chosen node type
node_inv() {
    case $NODE in
    "all")
        echo "$HELPER_INV" "$IGW_INV" "$PROXY_INV" "$SURR_INV"
        ;;
    "helper")
        echo "$HELPER_INV"
        ;;
    "inboundgateway")
        echo "$IGW_INV"
        ;;
    "proxy")
        echo "$PROXY_INV"
        ;;
    "surrogate")
        echo "$SURR_INV"
        ;;
    esac
}

pd_duration() {
    case $NODE in
    "all")
        echo "14400"
        ;;
    "helper")
        echo "2700"
        ;;
    "inboundgateway")
        echo "5400"
        ;;
    "proxy")
        echo "5400"
        ;;
    "surrogate")
        echo "2700"
        ;;
    esac
}

EXTRA_OPTIONS=(
    "--vault-pass-file=$ANSIBLE_VAULT_PASS_FILE"
    "-e wait_period=180"
    "-e interval=300"
    "-e health_check=no"
    "-e refresh_content=no"
    "-e vcenter_username=$VC_USERNAME"
    "-e vcenter_password=$VC_PASSWORD"
    "-e pagerduty_token=$PD_TOKEN"
    "-e pd_duration=$(pd_duration)"
)
if [ "$SITE" == "sjc2" ] || [ "$SITE" == "eat1" ]; then
    EXTRA_OPTIONS+=("--skip-tags gdmanage")
fi
FULL_OPTIONS="${EXTRA_OPTIONS[*]}"

deploy_nodes() {
    ansible-playbook "${BASE_PATH}"/menlo_pop_deployment.yml -i "$HELPER_INV" -i "$PROXY_INV" -i "$SURR_INV" $FULL_OPTIONS
    ansible-playbook "${BASE_PATH}"/menlo_pop_deployment.yml -i "$IGW_INV" $FULL_OPTIONS
}

destroy_nodes() {
    ansible-playbook "${BASE_PATH}"/menlo_upgrade.yml -i "$HELPER_INV" -i "$PROXY_INV" -i "$SURR_INV" -e cluster_destroy=yes $FULL_OPTIONS
}

patch_nodes() {
    if [ "$NODE" = "all" ]; then
        ansible-playbook "${BASE_PATH}"/menlo_helper_patch.yml -i "$HELPER_INV" $FULL_OPTIONS
        ansible-playbook "${BASE_PATH}"/menlo_proxy_patch.yml -i "$PROXY_INV" $FULL_OPTIONS
        ansible-playbook "${BASE_PATH}"/menlo_surrogate_patch.yml -i "$SURR_INV" $FULL_OPTIONS
        ansible-playbook "${BASE_PATH}"/menlo_inboundgateway_patch.yml -i "$IGW_INV" $FULL_OPTIONS
    else
        local NODE_INV
        NODE_INV=$(node_inv)
        ansible-playbook "${BASE_PATH}"/menlo_"$NODE"_patch.yml -i "$NODE_INV" $FULL_OPTIONS
    fi
}

reboot_nodes() {
    if [ "$NODE" = "all" ]; then
        ansible-playbook "${BASE_PATH}"/node_actions.yml -i "$HELPER_INV" -i "$PROXY_INV" -i "$SURR_INV" -i "$IGW_INV" -e power=restart $FULL_OPTIONS
    else
        local NODE_INV
        NODE_INV=$(node_inv)
        ansible-playbook "${BASE_PATH}"/node_actions.yml -i "$NODE_INV" -e power=restart $FULL_OPTIONS
    fi
}

redeploy_nodes() {
    if [ "$NODE" = "all" ]; then
        ansible-playbook "${BASE_PATH}"/menlo_helper_patch.yml -i "$HELPER_INV" $FULL_OPTIONS -e replace_anyway=yes
        ansible-playbook "${BASE_PATH}"/menlo_proxy_patch.yml -i "$PROXY_INV" $FULL_OPTIONS -e replace_anyway=yes
        ansible-playbook "${BASE_PATH}"/menlo_surrogate_patch.yml -i "$SURR_INV" $FULL_OPTIONS -e replace_anyway=yes
        ansible-playbook "${BASE_PATH}"/menlo_inboundgateway_patch.yml -i "$IGW_INV" $FULL_OPTIONS -e replace_anyway=yes
    else
        local NODE_INV
        NODE_INV=$(node_inv)
        ansible-playbook "${BASE_PATH}"/menlo_"$NODE"_patch.yml -i "$NODE_INV" $FULL_OPTIONS -e replace_anyway=yes
    fi
}

upgrade_1() {
    ansible-playbook "${BASE_PATH}"/menlo_upgrade.yml -i "$HELPER_INV" -i "$PROXY_INV" -i "$SURR_INV" $FULL_OPTIONS
}

upgrade_2() {
    ansible-playbook "${BASE_PATH}"/menlo_inboundgateway_patch.yml -i "$IGW_INV" $FULL_OPTIONS
}

case "$ACTION" in
"deploy")
    deploy_nodes
    ;;
"destroy")
    destroy_nodes
    ;;
"patch")
    patch_nodes
    ;;
"reboot")
    reboot_nodes
    ;;
"redeploy")
    redeploy_nodes
    ;;
"upgrade-part1")
    upgrade_1
    ;;
"upgrade-part2")
    upgrade_2
    ;;
*)
    echo "Unknown action: $ACTION"
    ;;
esac
