#!/usr/bin/env bash
# nowhere near done

# custom fields info
#   iteration -- date last run
#   created   -- date created
#   result    -- last tf run condition
#   enabled   -- only running when enabled
#   update    -- set to manually run (if already exists)

builtin export AUTH="Authorization: TOKEN ${NETBOX_TOKEN:?netbox token required}";
builtin export BASE="https://netbox.vmware-nonprod.net/api";
builtin export VPATH="/virtualization/virtual-machines/";
builtin export CPATH="/virtualization/clusters/";
builtin export ROLEPATH="/dcim/device-roles/";

ACTION="${1:-get}"
SITEARG="${2:-sjc2}"


function set_site() {
	id=$(curl -Ls -H "${AUTH}"  ${BASE}${CPATH} | jq -r ".results[]|select(.name==\"${1}\")|.id");
	echo -n ${id}
}
function create_netbox() {
        curl -X POST -Ls -H  "accept: application/json" -H  "Content-Type: application/json" -H "${AUTH}" ${BASE}${VPATH}/ -d "{ \"name\": \"${1}\", \"cluster\": ${2}, \"role\": ${3} }" 1>/dev/null
}
function update_tf() {
	# patch vm expects 5 args, id for api path, name, clusterid, and whatever we are customizing, key and value
        curl -X PATCH -Ls -H  "accept: application/json" -H  "Content-Type: application/json" -H "${AUTH}" ${BASE}${VPATH}${1}/ -d "{ \"name\": \"${2}\", \"cluster\": ${3}, \"custom_fields\": { \"terraform_${4}\": \"${5}\" } }" 1>/dev/null

}
function get_role_id() {
	curl -Ls -H "${AUTH}" ${BASE}${ROLEPATH} | jq ".results[] |select(.name==\"${1:-vcg}\").id"
}

if [[ "${ACTION}" == "get" ]]; then
	site_id=$(set_site ${SITEARG:?need site location for all operations (cluster id map)})
	echo;
fi
if [[ "${ACTION}" == "create" ]]; then
	create_netbox "${2}" $(set_site ${3:-sjc2}) $(get_role_id)
	exit 0
fi

for vcg in $(curl -Ls -H "${AUTH}"  ${BASE}${VPATH} | jq ".results[]|select((.role.name==\"vcg\") and (.cluster.id==${site_id}))|.id"); do
	name="$(curl -Ls -H "${AUTH}" ${BASE}${VPATH}${vcg}/ | jq -r '.|.name')";
	builtin echo "found ${name}"
	update_tf ${vcg} ${name} ${site_id} result 0
	update_tf ${vcg} ${name} ${site_id} update true
	update_tf ${vcg} ${name} ${site_id} iteration $(date +"%Y%h%d_%H:%M:%S")
	curl -Ls -H "${AUTH}" ${BASE}${VPATH}${vcg}/ | jq -r '.|.name, .custom_fields'
done

#curl -X PATCH -Ls -H  "accept: application/json" -H  "Content-Type: application/json" -H "${AUTH}" ${BASE}${VPATH}${vcg}/ -d "{ \"name\": \"${name}\", \"cluster\": ${cluster}, \"custom_fields\": { \"terraform\": \"true\" } }" | jq
