"""Shared settings test fixtures."""

from __future__ import annotations

from pathlib import Path


def dual_env(tmp_path: Path) -> dict[str, str]:
    """Return a complete dual S3 environment for settings tests."""

    return {
        "S3_SOURCE_PROVIDER": "oci",
        "S3_SOURCE_ACCESS_KEY_ID": "source-access",
        "S3_SOURCE_SECRET_ACCESS_KEY": "source-secret",
        "S3_SOURCE_REGION": "eu-frankfurt-1",
        "S3_SOURCE_NAMESPACE": "tenant",
        "S3_SOURCE_BUCKET": "source-bucket",
        "S3_SOURCE_IAM_USER_OCID": "ocid1.user.oc1..source",
        "S3_SOURCE_ADDRESSING_STYLE": "path",
        "S3_DESTINATION_PROVIDER": "localstack",
        "S3_DESTINATION_ACCESS_KEY_ID": "destination-access",
        "S3_DESTINATION_SECRET_ACCESS_KEY": "destination-secret",
        "S3_DESTINATION_REGION": "us-east-1",
        "S3_DESTINATION_BUCKET": "destination-bucket",
        "S3_DESTINATION_ADDRESSING_STYLE": "path",
        "LOG_LEVEL": "INFO",
        "LOG_DIR": str(tmp_path / "logs"),
    }
