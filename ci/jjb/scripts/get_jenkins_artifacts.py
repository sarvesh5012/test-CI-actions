#!/usr/bin/env python3
"""
CLI to list and download specific artifacts from Jenkins server
"""
import argparse
import logging
import os
import pathlib
import posixpath
import re
import shutil
from typing import Iterable, Dict, Any

import jenkins
import requests


# typedefs
# Artifact is a dict of artifact data from build info.
Artifact = Dict[str, Any]

# Setup logging
log = logging.getLogger(__name__)
stream_handler = logging.StreamHandler()
stream_formatter = logging.Formatter("%(levelname)s: %(message)s")
stream_handler.setFormatter(stream_formatter)
log.addHandler(stream_handler)
log.setLevel(logging.INFO)


class JenkinsBuild:
    """Class for non-specific Jenkins build job handling."""

    def __init__(self, jenkins_url: str, job: str, build_number: int):
        """
        Represents and handles a Jenkins build.

        Parameters:
            jenkins_url  (str): scheme and host of Jenkins e.g. https://jenkins.example.com
            job          (str): release name, e.g. Release-4.5.0
            build_number (int): Jenkins' job build number, e.g. 546
        """
        self._jenkins_url = jenkins_url
        self._job = job
        self._job_prefix = None
        self._build_number = build_number
        self._build_info = None

    def sync_build_info(self, jenkins_user: str, token: str):
        """
        Synchronize build_info data from Jenkins.

        !! This has to be called before other methods that rely on build data from Jenkins.

        Parameters:
            jenkins_user (str): Jenkins username
            token        (str): Jenkins password token
        """
        server = jenkins.Jenkins(
            self._jenkins_url, username=jenkins_user, password=token
        )
        job = f"{self._job_prefix}{self._job}" if self._job_prefix else self._job
        self._build_info = server.get_build_info(job, self._build_number)

    def get_artifacts(self) -> Iterable[Artifact]:
        """
        Return info on the artifacts that were built by the job.

        Returns:
            Iterable[str]: info on the artifacts that were built by the job

        Raises:
            AssertionError: if `sync_build_info` wasn't called first
        """
        assert self._build_info  # ensure we are in sync
        return self._build_info["artifacts"]


def download_file(
    user: str, token: str, url: str, dest_filename: str, create_dir: bool = True
):
    """
    Download from the Jenkins server.

    Parameters:
        user  (str): Jenkins username
        token (str): Jenkins password token

    Raises:
        requests.HTTPError: if there was any problem on the server side
        IOError: if file could not be written, for whatever reason
    """
    log.info("Streaming URL %s to %s", url, dest_filename)
    if create_dir:
        os.makedirs(os.path.dirname(dest_filename), exist_ok=True)
    with requests.get(url, auth=(user, token), stream=True) as request:
        request.raise_for_status()
        with open(dest_filename, "wb") as out:
            shutil.copyfileobj(request.raw, out)


class SdwanJenkinsBuild(JenkinsBuild):
    """Specialization of JenkinsBuild for SD-WAN purposes."""

    # The structure of this is as follows:
    # key: artifact type
    # values:
    #   'prefix' - first part of path to artifact on Jenkins
    #   'stages'
    #     key: stage, aka deploy type of the build artifact
    #     values:
    #       'relativePaths' collection of path parts to help identify
    #           the stage.
    #       'fileNames' regex to match filenames to help identify the
    #           stage.
    ARTIFACT_TYPES = {
        "edge": {
            "prefix": "",
            "stages": {
                "deploy": {
                    "relativePaths": [
                        "build/x86_64/image/edge",
                        "common/meta/enums/appids",
                    ],
                    "fileNames": r".*\.zip$|.*\.img.gz$|applications\.json",
                },
                "upgrade": {
                    "relativePaths": [
                        "build/x86_64/image/edge",
                        "common/meta/enums/appids",
                    ],
                    "fileNames": r".*\.zip$|.*\.img.gz$|applications\.json",
                },
                "upload": {
                    "relativePaths": [
                        "build/x86_64/image/edge",
                        "common/meta/enums/appids",
                    ],
                    "fileNames": r".*\.zip$|applications\.json",
                },
            },
        },
        "vcg": {
            "prefix": "",
            "stages": {
                "deploy": {
                    "relativePaths": ["build/x86_64/image/gateway"],
                    "fileNames": r".*kvm\.qcow2$",
                },
                "upgrade": {
                    "relativePaths": ["build/x86_64/package/gateway/deb"],
                    "fileNames": r".*\.deb$|pubkey\.pem$|vcg\-update.*\.tar$",
                },
            },
        },
        "vco": {
            "prefix": "vco-release-jobs/",
            "stages": {
                "deploy": {"relativePaths": [], "fileNames": r"TODO"},
                "upgrade": {
                    "relativePaths": [
                        "vco/build",
                        "vco/build/images"
                    ],
                    "fileNames": r"vco\-debs\-(signed)?.*(\.tar)+(\.bz2)?$|"
                                 r"vco\-signed\-image\-upgrade\-aws\-.*\.tar$|"
                                 r"pubkey\.pem$",
                },
            },
        },
        "gcp-veco": {
            "prefix": "vco-release-jobs/",
            "stages": {
                "deploy": {"relativePaths": [], "fileNames": r"TODO"},
                "upgrade": {
                    "relativePaths": [
                        "vco/build",
                        "vco/build/images"
                    ],
                    "fileNames": r"vco\-debs\-(signed)?.*(\.tar)+(\.bz2)?$|"
                                 r"vco\-signed\-image\-upgrade\-gcp\-.*\.tar$|"
                                 r"pubkey\.pem$",
                },
            },
        },
    }

    def __init__(self, *args, stage: str = "", artifact_type: str = ""):
        """
        Create an `SdwanJenkinsBuild`.

        Raises:
            ValueError: if the artifact type is not known.
            ValueError: if the stage is not defined for the given artifact type.
        """
        if artifact_type not in self.ARTIFACT_TYPES:
            raise ValueError(f'Bad artifact_type "{artifact_type}"')
        if stage not in self.ARTIFACT_TYPES[artifact_type]["stages"]:
            raise ValueError(
                f'Stage "{stage}" is not defined for artifact type "{artifact_type}"'
            )
        self._artifact_type = artifact_type
        self._stage = stage
        super().__init__(*args)
        self._job_prefix = self.ARTIFACT_TYPES[artifact_type]["prefix"]

    @staticmethod
    def _filter_artifacts_by_relative_paths(
        artifacts: Iterable[Artifact], filters: Dict
    ) -> Iterable[Artifact]:
        """
        Filter artifacts from the given collection by relative path.

        Keep any artifact where its relativePath is in any of the filter's relativePaths.

        Parameters:
            artifacts (Iterable[Artifact]): collection of the artifacts from the Jenkins build
            filters (Dict): the dict from the appropriate stage under ARTIFACT_TYPES,
                e.g. `vco/stages/deploy`'s dict.

        Returns:
            Iterable[Artifact]: collection of the matching artifacts
        """
        artifacts = [
            artifact
            for artifact in artifacts
            if any(
                relative_path in artifact["relativePath"]
                for relative_path in filters["relativePaths"]
            )
        ]
        return artifacts

    @staticmethod
    def _filter_artifacts_by_filenames(
        artifacts: Iterable[Artifact], filters: Dict
    ) -> Iterable[Artifact]:
        """
        Filter artifacts from the given collection by filename.

        Keep any artifact where its relativePath is in any of the filter's relativePaths.

        Parameters:
            artifacts (Iterable[Artifact]): collection of the artifacts from the Jenkins build
            filters (Dict): the dict from the appropriate stage under ARTIFACT_TYPES,
                e.g. `vco/stages/deploy`'s dict.

        Returns:
            Iterable[Artifact]: collection of the matching artifacts
        """
        artifacts = [
            artifact
            for artifact in artifacts
            if re.match(filters["fileNames"], artifact["fileName"])
        ]
        return artifacts

    def get_artifacts_info(self):
        """Get the artifacts relevant for the current stage."""
        artifacts = super().get_artifacts()
        filters = self.ARTIFACT_TYPES[self._artifact_type]["stages"][self._stage]
        artifacts = self._filter_artifacts_by_relative_paths(artifacts, filters)
        artifacts = self._filter_artifacts_by_filenames(artifacts, filters)
        return artifacts

    def _build_artifact_url(self, artifact: Artifact) -> str:
        """Create a download URL to fetch an artifact."""
        prefix = self.ARTIFACT_TYPES[self._artifact_type]["prefix"]
        prefix = f"{prefix}job" if prefix else ""
        artifact_url = posixpath.join(
            self._jenkins_url,
            "job",
            prefix,
            self._job,
            str(self._build_number),
            "artifact",
            artifact["relativePath"],
        )
        return artifact_url

    def _build_artifact_filename(self, artifact: Artifact, output_dir: str) -> str:
        """Create a filename to save an artifact."""
        filename = posixpath.join(output_dir, self._job, artifact["fileName"])
        return filename

    def download_artifact(
        self, jenkins_user: str, token: str, artifact: Artifact, output_dir: str
    ):
        """Download an artifact from Jenkins."""
        artifact_url = self._build_artifact_url(artifact)
        filename = self._build_artifact_filename(artifact, output_dir)
        download_file(jenkins_user, token, artifact_url, filename)


def parse_arguments():
    """Setup CLI argument parser, returns argparse.ArgumentParser"""
    parse = argparse.ArgumentParser(
        prog="get_jenkins_artifacts.py",
        description="Parse and download jenkins SDWAN artifacts",
    )
    parse.add_argument("--version", action="version", version="0.0.2")
    parse.add_argument(
        "--jenkins-url",
        "--url",
        dest="url",
        default="https://jenkins2.eng.velocloud.net",
        help="Jenkins URL",
    )
    parse.add_argument(
        "--username", default="svc.sebuopsartifact", help="Jenkins username"
    )
    parse.add_argument("--token", type=str, help="Jenkins password token")
    parse.add_argument(
        "--token-file",
        type=pathlib.Path,
        help="Jenkins password token file. Token must be only item on first line of file.",
    )
    parse.add_argument(
        "--output-dir",
        "--output",
        "-o",
        dest="output",
        default="output",
        metavar="output",
        help="Output base directory",
    )
    parse.add_argument(
        "--release",
        "--release-job",
        dest="release_job",
        metavar="release-job",
        help="Jenkins Release name. Ex: Release-4.5.0",
    )
    parse.add_argument(
        "--stage",
        dest="stage",
        default="upgrade",
        choices=["upload", "upgrade", "deploy"],
    )
    parse.add_argument(
        "--artifact",
        "-a",
        dest="artifact_type",
        choices=SdwanJenkinsBuild.ARTIFACT_TYPES.keys(),
    )
    parse.add_argument(
        "--build",
        "-b",
        metavar="build_number",
        dest="build_number",
        type=int,
        required=True,
    )
    parse.add_argument("--download", action=argparse.BooleanOptionalAction)

    return parse


if __name__ == "__main__":
    parser = parse_arguments()
    options = parser.parse_args()
    if not any([options.token, options.token_file]):
        parser.error("Token or Token File required.")
    if options.token_file:
        with open(options.token_file, encoding="utf-8") as f:
            options.token = f.readline().rstrip()

    # Do the artifact downloads
    sdwan_build = SdwanJenkinsBuild(
        options.url,
        options.release_job,
        options.build_number,
        stage=options.stage,
        artifact_type=options.artifact_type,
    )
    sdwan_build.sync_build_info(options.username, options.token)
    items = sdwan_build.get_artifacts_info()
    if options.download:
        log.info("Commencing downloads")
        for item in items:
            sdwan_build.download_artifact(
                options.username, options.token, item, options.output
            )
    else:
        log.info("Downloads not requested by user")
        for item in items:
            log.info("Would download: %s", item["fileName"])
