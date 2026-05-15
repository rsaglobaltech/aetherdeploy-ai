"""Remote Terraform state bootstrap (MEJORAS.md §1.1).

Creates the S3 bucket + DynamoDB lock table that the AWS S3 backend needs so
``terraform init`` does not fail on the first run. Idempotent: a bucket or
table that already exists is reused without modification.

GCS and AzureRM backends will plug in here under the same interface — for now
the only implementation is AWS S3 + DynamoDB because that is the only provider
catalogue that ships tier templates today.

boto3 is an optional extra, imported lazily so the rest of the toolchain works
without it. A missing SDK or missing credentials degrade to a soft warning;
the caller decides whether that is fatal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Status = Literal["created", "exists", "skipped", "error"]


@dataclass
class BackendBootstrap:
    backend: str           # "s3" / "gcs" / "azurerm"
    bucket: str
    lock_table: str | None
    region: str
    bucket_status: Status
    lock_status: Status
    skipped_reason: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        if self.bucket_status == "error" or self.lock_status == "error":
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "bucket": self.bucket,
            "lock_table": self.lock_table,
            "region": self.region,
            "bucket_status": self.bucket_status,
            "lock_status": self.lock_status,
            "skipped_reason": self.skipped_reason,
            "errors": list(self.errors),
        }


class BackendBootstrapError(RuntimeError):
    """Raised by callers that decide to treat a bootstrap failure as fatal."""


# ---------------------------------------------------------------------------
# AWS S3 + DynamoDB
# ---------------------------------------------------------------------------

def _aws_clients(region: str) -> tuple[Any, Any]:
    """Returns ``(s3, dynamodb)`` boto3 clients. Raises ImportError if boto3 is missing."""
    import boto3  # type: ignore[import]
    return boto3.client("s3", region_name=region), boto3.client("dynamodb", region_name=region)


def _bucket_exists(s3, bucket: str) -> bool:
    try:
        s3.head_bucket(Bucket=bucket)
        return True
    except Exception:  # noqa: BLE001 — boto3 raises ClientError but type isn't imported
        return False


def _create_bucket(s3, bucket: str, region: str) -> None:
    """``us-east-1`` does not accept ``LocationConstraint`` — handle separately."""
    if region == "us-east-1":
        s3.create_bucket(Bucket=bucket)
    else:
        s3.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": region},
        )


def _harden_bucket(s3, bucket: str) -> None:
    """Versioning + AES-256 encryption + block all public access.

    Terraform state can contain secrets even when the resources themselves do
    not — backups + encryption + no-public-access is non-negotiable.
    """
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    s3.put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        },
    )
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )


def _dynamodb_table_exists(dynamodb, table: str) -> bool:
    try:
        dynamodb.describe_table(TableName=table)
        return True
    except Exception:  # noqa: BLE001
        return False


def _create_lock_table(dynamodb, table: str) -> None:
    dynamodb.create_table(
        TableName=table,
        AttributeDefinitions=[{"AttributeName": "LockID", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "LockID", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )


def ensure_s3_state_backend(
    bucket: str,
    region: str,
    lock_table: str | None = None,
) -> BackendBootstrap:
    """Idempotently provisions the S3 bucket and (optionally) the DynamoDB lock table."""
    result = BackendBootstrap(
        backend="s3",
        bucket=bucket,
        lock_table=lock_table,
        region=region,
        bucket_status="skipped",
        lock_status="skipped" if lock_table else "skipped",
    )

    try:
        s3, dynamodb = _aws_clients(region)
    except ImportError:
        result.skipped_reason = "boto3 not installed (`pip install aetherdeploy[aws]`)"
        return result

    # --- Bucket ---
    try:
        if _bucket_exists(s3, bucket):
            result.bucket_status = "exists"
        else:
            _create_bucket(s3, bucket, region)
            _harden_bucket(s3, bucket)
            result.bucket_status = "created"
    except Exception as exc:  # noqa: BLE001
        result.bucket_status = "error"
        result.errors.append(f"S3 bucket bootstrap failed: {exc}")
        return result

    # If we created the bucket pre-existing without hardening, ensure it now.
    # Harden is idempotent so it is safe to call on a pre-existing bucket too.
    if result.bucket_status == "exists":
        try:
            _harden_bucket(s3, bucket)
        except Exception as exc:  # noqa: BLE001 — surface as warning, not fatal
            result.errors.append(f"could not enforce hardening on existing bucket: {exc}")

    # --- DynamoDB lock table ---
    if not lock_table:
        return result
    try:
        if _dynamodb_table_exists(dynamodb, lock_table):
            result.lock_status = "exists"
        else:
            _create_lock_table(dynamodb, lock_table)
            result.lock_status = "created"
    except Exception as exc:  # noqa: BLE001
        result.lock_status = "error"
        result.errors.append(f"DynamoDB lock table bootstrap failed: {exc}")

    return result


def ensure_state_backend(backend_config: dict) -> BackendBootstrap:
    """Dispatches to the right backend implementation based on ``backend_config['backend']``."""
    backend = backend_config.get("backend", "")
    if backend == "s3":
        return ensure_s3_state_backend(
            bucket=backend_config["bucket"],
            region=backend_config["region"],
            lock_table=backend_config.get("dynamodb_table"),
        )

    return BackendBootstrap(
        backend=backend or "unknown",
        bucket=backend_config.get("bucket", ""),
        lock_table=backend_config.get("dynamodb_table"),
        region=backend_config.get("region", ""),
        bucket_status="skipped",
        lock_status="skipped",
        skipped_reason=f"backend '{backend}' bootstrap not implemented yet",
    )
