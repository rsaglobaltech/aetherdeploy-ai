"""Application secret detection (MEJORAS.md §3.3).

Scans the project for declared (but value-less) secrets so the deployment
pipeline can provision matching ``aws_secretsmanager_secret`` /
``google_secret_manager_secret`` / ``azurerm_key_vault_secret`` resources
without dragging plaintext values through Terraform state.

The detector only reads *example* files (``.env.example`` and friends). Real
``.env`` files are ignored on purpose — those usually carry committed-by-accident
secrets that should not become source-of-truth for the deployment.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


_EXAMPLE_FILE_PATTERNS: tuple[str, ...] = (
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.dist",
    "env.example",
    "config/secrets.example.yml",
    "config/secrets.example.yaml",
    "src/main/resources/application.properties.example",
    "src/main/resources/application.example.properties",
)


_SECRET_KEY_PATTERN = re.compile(
    r"(?:^|_)("
    r"SECRET|PASSWORD|PASSWD|TOKEN|API_KEY|APIKEY|PRIVATE_KEY|PRIVKEY"
    r"|ACCESS_KEY|CLIENT_SECRET|SIGNING_KEY|ENCRYPTION_KEY"
    r"|DATABASE_URL|DB_URL|REDIS_URL|MONGODB_URI|MONGO_URI"
    r"|JWT|STRIPE|SENDGRID|TWILIO|SLACK"
    r")(?:_|$)",
    re.IGNORECASE,
)

# Keys that look like secrets per the pattern but are routinely benign so we
# do not pester the operator with a provisioning prompt for them.
_FALSE_POSITIVES: frozenset[str] = frozenset(
    {
        "API_KEY_HEADER_NAME",
        "TOKEN_HEADER",
        "JWT_HEADER",
        "JWT_ALGORITHM",
        "JWT_AUDIENCE",
        "JWT_ISSUER",
    }
)

# Env-var name patterns that are configuration knobs, not secrets, even when
# they happen to contain a keyword from the secret pattern.
_NON_SECRET_NAMES: frozenset[str] = frozenset(
    {
        "PUBLIC_KEY_URL",
        "PUBLIC_KEY",  # public keys aren't secrets
    }
)


@dataclass
class SecretSpec:
    name: str                           # original env var name, e.g. DATABASE_URL
    source: Path                        # the example file the secret was declared in
    example_value: str | None = None    # optional placeholder we saw in the example
    classification: str = "app-secret"

    def aws_resource_name(self) -> str:
        """ECR-style safe identifier (Terraform local name + Secrets Manager name)."""
        slug = re.sub(r"[^A-Za-z0-9]+", "_", self.name.lower()).strip("_") or "secret"
        return f"app_secret_{slug}"


# ---------------------------------------------------------------------------
# Single-line classifier
# ---------------------------------------------------------------------------

def looks_like_secret(key: str) -> bool:
    if not key:
        return False
    up = key.strip().upper()
    if up in _FALSE_POSITIVES or up in _NON_SECRET_NAMES:
        return False
    return bool(_SECRET_KEY_PATTERN.search(up))


def _parse_env_lines(text: str) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # support `export KEY=value` form too
        if line.lower().startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            continue
        value = value.strip().strip('"').strip("'") or None
        out.append((key, value))
    return out


def _parse_properties_lines(text: str) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        env_key = re.sub(r"[^A-Za-z0-9]+", "_", key.strip()).upper().strip("_")
        if not env_key:
            continue
        value = value.strip() or None
        out.append((env_key, value))
    return out


def _parse_secret_lines(path: Path, text: str) -> list[tuple[str, str | None]]:
    name = path.name.lower()
    if name.endswith(".properties") or "properties.example" in name:
        return _parse_properties_lines(text)
    return _parse_env_lines(text)


# ---------------------------------------------------------------------------
# Project-level detection
# ---------------------------------------------------------------------------

def _candidate_files(project_path: Path) -> list[Path]:
    found: list[Path] = []
    for pattern in _EXAMPLE_FILE_PATTERNS:
        candidate = project_path / pattern
        if candidate.is_file():
            found.append(candidate)
    # Spring layout often ships multiple .example files; also pick up any
    # ``*.example`` at the project root for resilience.
    for match in project_path.glob("*.example"):
        if match.is_file() and match not in found:
            found.append(match)
    return found


def detect_secrets(project_path: Path) -> list[SecretSpec]:
    """Returns one :class:`SecretSpec` per declared secret across all example files.

    Duplicates (same key in multiple files) are collapsed to a single spec,
    keeping the first source for traceability.
    """
    seen: dict[str, SecretSpec] = {}
    for path in _candidate_files(project_path):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for key, value in _parse_secret_lines(path, text):
            if not looks_like_secret(key):
                continue
            up = key.upper()
            if up in seen:
                continue
            seen[up] = SecretSpec(name=up, source=path, example_value=value)
    return list(seen.values())


def filter_already_provisioned(
    specs: Iterable[SecretSpec],
    already: Iterable[str],
) -> list[SecretSpec]:
    """Drops specs whose name is already covered by an outside provisioning flow."""
    skip = {a.upper() for a in already}
    return [s for s in specs if s.name.upper() not in skip]
