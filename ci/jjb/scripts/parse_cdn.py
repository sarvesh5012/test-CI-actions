#!/usr/bin/env python3
"""
CLI to get download URLs of specific artifacts from CDN
"""
import argparse
import logging
import os
from posixpath import (
    join as path_join,
    dirname,
)
import time
import hmac
from hashlib import sha256
import base64
from urllib.parse import urljoin, quote_plus

CDN_HOSTNAME = "repo.broadcom.com"
ROOT_DIR = "/sase"

# Setup logging
LOG = logging.getLogger(__name__)
stream_handler = logging.StreamHandler()
stream_formatter = logging.Formatter("%(levelname)s: %(message)s")
stream_handler.setFormatter(stream_formatter)
LOG.addHandler(stream_handler)
LOG.setLevel(logging.INFO)


def get_files(opts):
    """
    List artifact(s) URLs with token from CDN

    Variables:
        opts.artifacts (str): Artifact(s) to list URLs (req full path unless dir specified)
        opts.dir       (str): (optional) dir of the artifacts
        opts.output    (str): Output text file
        opts.prefix    (str): Prefix for each output line written to text file
        opts.reverse  (bool): Reverse output ordering
    """
    LOG.debug("Requested artifacts: %s", opts.artifacts)
    for artifact in opts.artifacts:
        if artifact.endswith("/"):
            parser.error(f"{artifact} ends with /; must provide a file not directory")
        artifact = artifact.lstrip("/")
        if opts.dir and opts.dir not in artifact:
            LOG.debug("Dir: %s", opts.dir)
            artifact = path_join(opts.dir, artifact)
        LOG.debug("Artifact: %s", artifact)
        artifact_url = generate_url(artifact)
        LOG.debug("Artifact URL: %s", artifact_url)
        if opts.output:
            if dirname(opts.output):
                LOG.debug("Output file:", opts.output)
                os.makedirs(dirname(opts.output), exist_ok=True)
                with open(opts.output, "a", encoding="utf-8") as output_file:
                    output_file.write(f"{opts.prefix}{artifact_url}\n")
        else:
            print(artifact_url)


def generate_url(artifact):
    artifact_path = path_join(ROOT_DIR, artifact)
    LOG.debug("Artifact path: %s", artifact_path)
    url = urljoin(BASE_URL, artifact_path)
    LOG.debug("URL: %s", url)
    token = generate_token(artifact_path)
    LOG.debug("Token: %s", token)
    return urljoin(url, token)


def generate_token(artifact_path):
    """
    Generate a HMAC token for CDN download
    """
    timestamp = str(int(time.time()))
    digest = hmac.new(
        options.key.encode("utf8"), f"{artifact_path}{timestamp}".encode("utf8"), sha256
    )
    token = quote_plus(base64.b64encode(digest.digest()))
    token_content = f"?verify={timestamp}-{token}"
    return token_content


def parse_arguments():
    """Setup CLI argument parser, returns argparse.ArgumentParser"""
    parse = argparse.ArgumentParser(prog="parse_cdn.py", description="Parse CDN repo")
    parse.add_argument("--version", action="version", version="0.0.1")
    parse.add_argument("--key", type=str, help="Encryption key")
    parse.add_argument(
        "--key-file",
        help="Encryption key file. Key must be only item first line of file.",
    )
    parse.add_argument(
        "--hostname",
        "-d",
        dest="cdn",
        default=os.environ.get("CLOUDFLARE_HOSTNAME", CDN_HOSTNAME),
        metavar="cdn",
        help="CDN hostname",
    )
    parse.add_argument(
        "--output-file",
        "--output",
        "-o",
        dest="output",
        default=None,
        metavar="output",
        help="Output file",
    )
    parse.add_argument(
        "--prefix",
        default="- ",
        type=str,
        help='Prefix string for output usability. default: "- "',
    )
    parse.add_argument(
        "--reverse", action=argparse.BooleanOptionalAction, help="Reverse listed output"
    )
    parse.add_argument(
        "--debug", action=argparse.BooleanOptionalAction, help="Print debug logs"
    )

    subparsers = parse.add_subparsers(
        dest="cmd", metavar="{get, download}", required=True
    )

    # Get URL parser
    url_parser = subparsers.add_parser("get", aliases=["get_url", "url"])
    url_parser.set_defaults(func=get_files)
    url_parser.add_argument(
        "--dir",
        help="Artifact directory. Ex: 'vcg/GA/upgrade/Release-4.4.0'",
    )
    url_parser.add_argument(
        "artifacts",
        help="Artifact(s) to get url. Ex: 'vcg/GA/upgrade/Release-4.4.0/"
        "vcg-update-v2-4.4.0-57-R440-20210610-GA-40b4b0fbf2.tar'",
        nargs="*",
    )

    return parse


if __name__ == "__main__":
    parser = parse_arguments()
    options = parser.parse_args()
    if not any([options.key, options.key_file]):
        parser.error("Key or Key File required.")
    if options.key_file:
        with open(options.key_file, encoding="utf-8") as key_file:
            options.key = key_file.readline().rstrip()

    BASE_URL = f"https://{options.cdn}"

    if options.debug:
        LOG.setLevel(logging.DEBUG)

    options.func(options)
