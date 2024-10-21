#!/usr/bin/env python3

""" Convert Terraform variable file to json structure with var type detail"""
import json
import argparse
import hcl2

parser = argparse.ArgumentParser()

##### Arguments Parser ######
parser.add_argument(
    "--input-file",
    required=True,
    help="Terraform variable file passed as input",
)
parser.add_argument(
    "--output-file",
    required=True,
    help="Output file to be saved as json",
)

args = parser.parse_args()

type_mapping = {
    "${string}": "string",
    "${bool}": "boolstring",
    "${list": "list",
    "${map": "dict",
    "${object": "dict",
}

# Load the Terraform variable file
with open(args.input_file, "r", encoding='utf-8') as file:
    tf_vars = hcl2.load(file)

req_json = {}

for variable in tf_vars["variable"]:
    for key, value in variable.items():
        for prefix, type_ in type_mapping.items():
            if value["type"].startswith(prefix):
                req_json[key] = {"value_type": type_}
                break

# Write the json content to file
with open(args.output_file, "w+", encoding='utf-8') as file:
    file.write(json.dumps(req_json, indent=4))
