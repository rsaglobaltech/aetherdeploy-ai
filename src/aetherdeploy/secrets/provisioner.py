"""AWS Secrets Manager value provisioning (MEJORAS.md §3.3).

The Terraform stanza from :mod:`secrets.generator` provisions the secret
*containers*. This module fills the *values* via boto3 ``PutSecretValue``
after ``terraform apply`` has created the secrets — keeping plaintext out of
Terraform state and out of generated HCL.

Values are read from operator-supplied environment variables of the form
``AETHER_SECRET_VALUE_<KEY>``. Anything not set is reported as ``"missing"``
so the operator can see at-a-glance which secrets still need real values.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable

from .detector import SecretSpec


@dataclass
class SecretProvisionResult:
    name: str
    status: str                          # "written" | "missing" | "error"
    error: str | None = None
    secret_arn: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "error": self.error,
            "secret_arn": self.secret_arn,
        }


def _aws_secrets_client(region: str) -> Any:
    try:
        import boto3  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is required to provision secret values. Install `aetherdeploy[aws]`."
        ) from exc
    return boto3.client("secretsmanager", region_name=region)


def _value_for(spec: SecretSpec) -> str | None:
    """Read value from env var; never from disk and never from the example file."""
    return os.environ.get(f"AETHER_SECRET_VALUE_{spec.name.upper()}")


def provision_aws_secret_values(
    specs: Iterable[SecretSpec],
    project_name: str,
    environment: str,
    region: str = "us-east-1",
    boto3_client: Any | None = None,
) -> list[SecretProvisionResult]:
    """Writes operator-supplied values into Secrets Manager.

    The function is idempotent: each call writes the *current* value as a new
    version of the secret, which is the behaviour expected for rotation.
    Secrets without a corresponding env var are flagged as ``"missing"`` —
    callers decide whether to abort the deploy or proceed (the resource still
    exists, just empty).
    """
    specs = list(specs)
    results: list[SecretProvisionResult] = []
    if not specs:
        return results

    try:
        client = boto3_client or _aws_secrets_client(region)
    except RuntimeError as exc:
        for spec in specs:
            results.append(SecretProvisionResult(name=spec.name, status="error", error=str(exc)))
        return results

    for spec in specs:
        value = _value_for(spec)
        if value is None:
            results.append(SecretProvisionResult(name=spec.name, status="missing"))
            continue

        secret_id = f"{project_name}/{environment}/{spec.name.lower()}"
        try:
            response = client.put_secret_value(SecretId=secret_id, SecretString=value)
        except Exception as exc:  # noqa: BLE001
            results.append(SecretProvisionResult(name=spec.name, status="error", error=str(exc)))
            continue
        results.append(
            SecretProvisionResult(
                name=spec.name,
                status="written",
                secret_arn=response.get("ARN"),
            )
        )

    return results
