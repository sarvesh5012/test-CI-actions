#!/usr/bin/env bash

set -eux

TASK=$1
CURRENT_VCO=$2
CANDIDATE_VCO=${3:-""}
VOLTYPES=("clickhouse" "data" "root" "binlog")

ssh_cmd() {
    ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -F "$SSH_CONFIG" -q "$@"
}

call_snapshot() {
    local action=$1
    local dbinstance=$2
    local voltype=$3

    ssh_cmd backup1-us1 sudo su - vcbackup -c "bash -c '/opt/patron-vcdb/bin/patron_snapshot --config /opt/patron-vcdb/conf/backup_prod_patron.yml $action $dbinstance $voltype'" || {
        echo "Error occurred with $action for $dbinstance $voltype."
    }
}

check_final_snapshot() {
    local dbinstance=$1
    local voltype=$2
    local snapshots=$(call_snapshot "list" "$dbinstance" "$voltype")
    if echo "$snapshots" | grep -Eq "KeyError|Error"; then
        echo "error"
        return
    fi
    local recent_final_snapshot=$(echo "$snapshots" | grep -E "^final-" | tail -n 1)
    if [[ -z "$recent_final_snapshot" ]]; then
        echo "backup_needed"
        return
    fi
    local snapshot_date=$(echo "$recent_final_snapshot" | grep -oP "\d{4}-\d{2}-\d{2}")
    local snapshot_epoch=$(date -d "$snapshot_date" +%s)
    local two_days_ago=$(date -d '2 days ago' +%s)
    if [[ $snapshot_epoch -lt $two_days_ago ]]; then
        echo "backup_needed"
        return
    fi
    echo "no_backup_needed"
}

take_backups() {
    local VCO=$1
    for voltype in "${VOLTYPES[@]}"; do
        local should_backup=$(check_final_snapshot "$VCO" "$voltype")
        case $should_backup in
            "backup_needed")
                echo "Initiating backup for $voltype on $VCO."
                call_snapshot "createfinal" "$VCO" "$voltype"
                ;;
            "error")
                echo "Error listing snapshots for $voltype on $VCO. Skipping backup."
                ;;
            "no_backup_needed")
                echo "Skipping backup for $voltype on $VCO."
                ;;
        esac
    done
}

get_volume_details() {
    host=$1
    volume=$2

    read used_space available_space < <(ssh_cmd "$host" "df $volume | grep -v Filesystem | awk '{print \$3, \$4}'")
    echo "$used_space $available_space"
}

compare_volumes() {
    CURRENT_VCO=$1
    CANDIDATE_VCO=$2
    volume=$3

    read active_used active_avail < <(get_volume_details "$CURRENT_VCO" "$volume")
    read candidate_used candidate_avail < <(get_volume_details "$CANDIDATE_VCO" "$volume")

    min_required=$((active_used * 130 / 100))

    if [ "$candidate_avail" -lt "$min_required" ]; then
        echo "Volume $volume on $CANDIDATE_VCO does not have sufficient space."
        echo "Required: $min_required KB, Available: $candidate_avail KB"
        exit 1
    else
        echo "Volume $volume on $CANDIDATE_VCO has sufficient space."
        echo "Required: $min_required KB, Available: $candidate_avail KB"
    fi
}

compare_cpu_mem_amount() {
    VCO1=$1
    VCO2=$2
    MEM_THRESHOLD=1000000

    get_cpu_mem() {
        host=$1
        cpu=$(ssh_cmd "$host" "nproc --all")
        mem=$(ssh_cmd "$host" "free | grep Mem | awk '{print \$2}'")

        echo "$cpu $mem"
    }

    read cpu1 mem1 < <(get_cpu_mem "$VCO1")
    read cpu2 mem2 < <(get_cpu_mem "$VCO2")

    echo "$VCO1" has "$cpu1" CPUs and "$mem1" KB of mem
    echo "$VCO2" has "$cpu2" CPUs and "$mem2" KB of mem

    mem_delta=$((mem2-mem1))

    if ((mem_delta < 0)); then
        abs_diff_delta=$((mem_delta * -1))
    else
        abs_diff_delta=$mem_delta
    fi

    if (( cpu2 < cpu1 || abs_diff_delta > MEM_THRESHOLD)); then
        echo "Failure: $VCO2 has less resources than $VCO1"
        return 1
    else
        echo "Success: $VCO2 has equal or more resources than $VCO1"
        return 0
    fi
}

dbrepl() {
    VCO1=$1
    VCO2=$2

    AUTH_KEYS_PATH="/home/dbrepl/.ssh/authorized_keys"

    check_and_modify_immutable_flag() {
        host=$1
        echo "Checking the immutable flag on $host for the file $AUTH_KEYS_PATH"
        if ssh_cmd "$host" "sudo lsattr $AUTH_KEYS_PATH | grep -q '\-i\-'"; then
            echo "Immutable flag is set on $AUTH_KEYS_PATH. Removing..."
            ssh_cmd "$host" "sudo chattr -i $AUTH_KEYS_PATH"
            echo "Immutable flag removed."
        else
            echo "No immutable flag set on $AUTH_KEYS_PATH."
        fi
    }

    check_and_modify_immutable_flag "$VCO1"
    check_and_modify_immutable_flag "$VCO2"
}

replication_tables() {
    VCO=$1
    ssh_cmd -T "$VCO" <<'EOF'
commands=$(cat <<'END_HEREDOC'
if ! grep -qxF 'replicate-wild-do-table = velocloud\_search.%%' /etc/mysql/conf.d/standby.cnf.disabled; then
    echo 'replicate-wild-do-table = velocloud\_search.%%' | sudo tee -a /etc/mysql/conf.d/standby.cnf.disabled
fi
if ! grep -qxF 'replicate-wild-do-table = velocloud\_search_velocloud.%%' /etc/mysql/conf.d/standby.cnf.disabled; then
    echo 'replicate-wild-do-table = velocloud\_search_velocloud.%%' | sudo tee -a /etc/mysql/conf.d/standby.cnf.disabled
fi
if ! grep -qxF 'replicate-wild-do-table = velocloud\_sse.%%' /etc/mysql/conf.d/standby.cnf.disabled; then
    echo 'replicate-wild-do-table = velocloud\_sse.%%' | sudo tee -a /etc/mysql/conf.d/standby.cnf.disabled
fi
if ! grep -qxF 'replicate-wild-do-table = velocloud\_sse_velocloud.%%' /etc/mysql/conf.d/standby.cnf.disabled; then
    echo 'replicate-wild-do-table = velocloud\_sse_velocloud.%%' | sudo tee -a /etc/mysql/conf.d/standby.cnf.disabled
fi
END_HEREDOC
)
bash -c "$commands"
exit 0
EOF
}

clickhouse_permissions() {
    VCO=$1
    ssh_cmd "$VCO" "sudo chown -R clickhouse:clickhouse /store3/clickhouse/"
}

clickhouse_sync() {
    VCO=$1
    retries=10
    while (( retries > 0)); do
        if ssh_cmd "$VCO" "sudo test -e /store3/clickhouse/data/velocloud_stats/VELOCLOUD_LINK_STATS/";then
            if [[ $(ssh_cmd "$VCO" "expr $(date +%s) - $(sudo stat -c %Y /store3/clickhouse/data/velocloud_stats/VELOCLOUD_LINK_STATS/)") -le 600 ]]; then
                echo "Clickhouse /store3 folder is updating on the DR VCO"
                exit 0
            else
                echo "Clickhouse /store3 folder exists but does not seem to be updating."
                exit 1
            fi
        else
            echo "Clickhouse /store3 folder doesn't exist"
            echo "Retrying"
            sleep 10
            (( retries-- ))
        fi
    done
    echo "Clickhouse /store3 folder not created after 100 seconds. Exiting"
    exit 1
}

case "$TASK" in
  "volume-size")
    if [[ -z $CURRENT_VCO ]]; then
        echo "CURRENT_VCO (active) is not provided. Exiting."
        exit 1
    fi

    if [[ -z $CANDIDATE_VCO ]]; then
        echo "CANDIDATE_VCO (standby) is not provided. Exiting."
        exit 1
    fi
    compare_volumes "$CURRENT_VCO" "$CANDIDATE_VCO" "/store"
    compare_volumes "$CURRENT_VCO" "$CANDIDATE_VCO" "/store3"
    ;;

  "compare-resources")
    compare_cpu_mem_amount "$CURRENT_VCO" "$CANDIDATE_VCO"
    ;;

  "take-backups")
    take_backups "$CURRENT_VCO"
    ;;

  "dbrepl")
    if [[ -z $CURRENT_VCO ]]; then
        echo "CURRENT_VCO (active) is not provided. Exiting."
        exit 1
    fi

    if [[ -z $CANDIDATE_VCO ]]; then
        echo "CANDIDATE_VCO (standby) is not provided. Exiting."
        exit 1
    fi
    dbrepl "$CURRENT_VCO" "$CANDIDATE_VCO"
    ;;

  "replication-tables")
    replication_tables "$CURRENT_VCO"
    ;;

  "clickhouse-permissions")
    clickhouse_permissions "$CURRENT_VCO"
    ;;

  "clickhouse-sync")
    clickhouse_sync "$CURRENT_VCO"
    ;;
esac
