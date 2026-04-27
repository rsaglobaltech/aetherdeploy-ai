"""Credential validation helpers for the AetherDeploy CLI.

All cloud-provider credential checks live here so they can be imported by
both the classic CLI (cli.py) and any future API server without pulling in
the full CLI surface.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from .agent.nodes.execution import check_feature_credentials, check_prod_credentials

# Keys accepted from external sources (frontend, env). Anything else is dropped.
ALLOWED_CREDENTIAL_KEYS: frozenset[str] = frozenset({
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_TENANT_ID",
    "LOCALSTACK_AUTH_TOKEN",
})


async def validate_provider_credentials(provider: str) -> tuple[bool, str]:
    """Validates credentials by making a real verification call.

    Returns (ok, message) where ok=True means credentials are valid.
    """
    provider = provider.lower()

    if provider == "aws":
        try:
            import boto3  # type: ignore[import]
            from botocore.config import Config  # type: ignore[import]

            def _check_aws() -> tuple[bool, str]:
                try:
                    sts = boto3.Session().client(
                        "sts",
                        config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 1}),
                    )
                    identity = sts.get_caller_identity()
                    account = identity.get("Account", "?")
                    arn = identity.get("Arn", "?")
                    return True, f"AWS credentials valid — Account: {account} ({arn})"
                except Exception as exc:
                    return False, f"Invalid AWS credentials: {exc}"

            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _check_aws)
        except ImportError:
            return False, "Cannot verify AWS credentials: boto3/botocore is not installed."

    if provider == "gcp":
        cred_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if cred_file and not Path(cred_file).exists():
            return False, f"Invalid GCP credentials: file not found at '{cred_file}'"
        try:
            import google.auth  # type: ignore[import]
            credentials, project_id = google.auth.default()
            if not credentials.valid and credentials.expired and credentials.refresh_token:
                from google.auth.transport.requests import Request  # type: ignore[import]
                credentials.refresh(Request())
            return True, f"GCP credentials valid — project: {project_id or '?'}"
        except ImportError:
            if not cred_file:
                return False, (
                    "Cannot verify GCP credentials: install google-auth or set GOOGLE_APPLICATION_CREDENTIALS."
                )
            try:
                import json as _json
                data = _json.loads(Path(cred_file).read_text())
                required = ["type", "project_id", "private_key", "client_email"]
                missing = [k for k in required if k not in data]
                if missing:
                    return False, f"Incomplete GCP credentials file. Missing fields: {', '.join(missing)}"
                return True, f"GCP credentials valid — service account: {data.get('client_email', '?')}"
            except Exception as exc:
                return False, f"Invalid GCP credentials: {exc}"
        except Exception as exc:
            return False, f"Invalid GCP credentials: {exc}"

    if provider == "azure":
        try:
            from azure.identity import DefaultAzureCredential  # type: ignore[import]

            def _check_azure() -> tuple[bool, str]:
                try:
                    token = DefaultAzureCredential().get_token(
                        "https://management.azure.com/.default"
                    )
                    return True, f"Azure credentials valid — token expires at {token.expires_on}"
                except Exception as exc:
                    return False, f"Invalid Azure credentials: {exc}"

            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _check_azure)
        except ImportError:
            return False, "Cannot verify Azure credentials: azure-identity is not installed."

    if provider == "localstack":
        token = os.environ.get("LOCALSTACK_AUTH_TOKEN", "")
        if not token or len(token) < 8:
            return False, "LOCALSTACK_AUTH_TOKEN is invalid or too short."
        return True, "LocalStack token received."

    return True, f"Credentials for '{provider}' received (no verification available)."


async def validate_required_credentials(
    envs: list[str],
    provider: str,
    requested_action: str = "deploy",
) -> tuple[bool, str]:
    """Validates all credentials needed for the given environments and action."""
    for env in envs:
        if env == "feature":
            if requested_action == "plan":
                continue
            ok, msg = await validate_provider_credentials("localstack")
            if not ok:
                return ok, msg
        elif env in ("prod", "staging"):
            ok, msg = await validate_provider_credentials(provider)
            if not ok:
                return ok, msg
    return True, "No cloud credentials required for this flow."


def check_credentials_for_envs(
    envs: list[str],
    provider: str,
    requested_action: str = "deploy",
) -> dict | None:
    """Synchronous presence-check (no network call). Returns missing fields dict or None."""
    for env in envs:
        if env == "feature":
            if requested_action == "plan":
                continue
            missing = check_feature_credentials()
            if missing:
                return missing
        elif env in ("prod", "staging"):
            missing = check_prod_credentials(provider)
            if missing:
                return missing
    return None


def credential_fields_for(envs: list[str], provider: str) -> dict:
    """Returns the expected credential field names for the given envs/provider."""
    for env in envs:
        if env == "feature":
            return {"provider": "localstack", "fields": ["LOCALSTACK_AUTH_TOKEN"]}
        if env in ("prod", "staging"):
            fields = {
                "aws": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
                "gcp": ["GOOGLE_APPLICATION_CREDENTIALS"],
                "azure": ["AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID"],
            }.get(provider.lower(), [])
            return {"provider": provider.lower(), "fields": fields}
    return {"provider": provider.lower(), "fields": []}
