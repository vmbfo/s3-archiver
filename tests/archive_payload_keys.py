"""Assertions for archive payloads separately from their membership sidecars."""

from s3_archiver_core.s3 import S3Client
from s3_archiver_localstack_support.objects import listed_keys


def listed_payload_keys(client: S3Client, bucket: str) -> set[str]:
    return {key for key in listed_keys(client, bucket) if ".tar.gz.members." not in key}
