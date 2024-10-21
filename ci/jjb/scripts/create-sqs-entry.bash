#!/usr/bin/env bash
builtin export AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-us-west-2}
builtin export QEUEUE_NAME_PREFIX=${QEUEUE_NAME_PREFIX:-vcg-netbox-queue}
#builtin export QUEUE_URL=$(aws sqs list-queues --queue-name-prefix ${QEUEUE_NAME_PREFIX} | jq -r ".QueueUrls[]" | head -1)
builtin export QUEUE_URL=${QUEUE_URL:-https://sqs.us-west-2.amazonaws.com/238882787599/jenkins-sqs}
builtin export vcg_name=${1:?Need vcg name as first argument/param}
echo "queue url: ${QUEUE_URl}"
echo "vcg name: ${1}"
echo "site name: ${2}"
aws sqs send-message --queue-url ${QUEUE_URL} --message-body "{ \"vcg_name\": \"${1}\", \"site_name\": \"${2}\" }"
