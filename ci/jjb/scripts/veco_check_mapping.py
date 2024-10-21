"""Checks csv for correct GCP -> AWS VECO name mapping"""
import os
import sys
import csv

dir_path = os.path.dirname(os.path.realpath(__file__))


def check_veco_mapping(
    aws_vco,
    active_gcp_vco,
    standby_gcp_vco,
    csv_file=f"{dir_path}/templates/veco-aws-gcp-mapping.csv",
):
    """Mapping logic"""
    with open(csv_file, mode="r", encoding="UTF-8") as file:
        reader = csv.DictReader(file)

        for row in reader:
            row_aws_vco = row["AWS VCO Name"].strip()
            row_active_gcp_vco = row["GCP Active VECO"].strip()
            row_standby_gcp_vco = row["GCP Standby VECO"].strip()

            if (
                row_aws_vco == aws_vco
                and row_active_gcp_vco == active_gcp_vco
                and (not standby_gcp_vco or row_standby_gcp_vco == standby_gcp_vco)
            ):
                return True

    return False


if __name__ == "__main__":
    if len(sys.argv) < 3 or len(sys.argv) > 5:
        print("Usage: python script.py <AWS_VCO> <ACTIVE_GCP_VCO> [<STANDBY_GCP_VCO>]")
        sys.exit(1)

    aws_vco = sys.argv[1]
    active_gcp_vco = sys.argv[2]

    standby_gcp_vco = ""

    # Assign optional parameters based on their format or content
    if len(sys.argv) > 3:
        standby_gcp_vco = sys.argv[3]

    if check_veco_mapping(aws_vco, active_gcp_vco, standby_gcp_vco):
        print("Match found in the CSV file.")
    else:
        print("No match found in the CSV file.")
