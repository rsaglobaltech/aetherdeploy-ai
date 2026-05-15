"""AWS ECR helpers for the build/push pipeline (MEJORAS.md §2.1).

Creates an Elastic Container Registry repository on demand and returns the
short-lived credentials Docker needs to push to it. boto3 is imported lazily
because it is an optional extra (``aetherdeploy[aws]``).
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any


@dataclass
class EcrRepository:
    name: str
    uri: str           # 123456789012.dkr.ecr.us-east-1.amazonaws.com/<name>
    registry: str      # 123456789012.dkr.ecr.us-east-1.amazonaws.com
    region: str


@dataclass
class EcrCredentials:
    registry: str
    username: str      # always "AWS" for ECR
    password: str      # short-lived token


class EcrError(RuntimeError):
    """Raised when an ECR operation fails or boto3 is not installed."""


def _client(region: str) -> Any:
    try:
        import boto3  # type: ignore[import]
    except ImportError as exc:
        raise EcrError(
            "boto3 is required for ECR support. Install with `pip install aetherdeploy[aws]`."
        ) from exc
    return boto3.client("ecr", region_name=region)


def ensure_ecr_repository(
    repository_name: str,
    region: str,
    immutable_tags: bool = False,
    scan_on_push: bool = True,
) -> EcrRepository:
    """Idempotent ECR repository bootstrap.

    Creates the repository the first time and returns its URI on subsequent
    calls — never raises on ``RepositoryAlreadyExistsException``.
    """
    client = _client(region)

    try:
        response = client.describe_repositories(repositoryNames=[repository_name])
        repo = response["repositories"][0]
    except client.exceptions.RepositoryNotFoundException:
        response = client.create_repository(
            repositoryName=repository_name,
            imageTagMutability="IMMUTABLE" if immutable_tags else "MUTABLE",
            imageScanningConfiguration={"scanOnPush": scan_on_push},
        )
        repo = response["repository"]
    except Exception as exc:  # noqa: BLE001 — surface anything else as EcrError
        raise EcrError(f"ECR describe/create failed: {exc}") from exc

    uri = repo["repositoryUri"]
    registry = uri.split("/", 1)[0]
    return EcrRepository(name=repo["repositoryName"], uri=uri, registry=registry, region=region)


def get_ecr_credentials(region: str) -> EcrCredentials:
    """Fetches a short-lived (~12h) ECR auth token via ``ecr:GetAuthorizationToken``."""
    client = _client(region)
    try:
        response = client.get_authorization_token()
    except Exception as exc:  # noqa: BLE001
        raise EcrError(f"ECR GetAuthorizationToken failed: {exc}") from exc

    data = response["authorizationData"][0]
    decoded = base64.b64decode(data["authorizationToken"]).decode("utf-8")
    username, _, password = decoded.partition(":")
    proxy = data["proxyEndpoint"].replace("https://", "")
    return EcrCredentials(registry=proxy, username=username, password=password)


def derive_repository_name(project_name: str, env: str) -> str:
    """ECR repository names must be lowercase, alnum + ``-_/``. ``my-api/feature`` is valid."""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in project_name.lower())
    safe = safe.strip("-") or "app"
    return f"{safe}/{env}"
