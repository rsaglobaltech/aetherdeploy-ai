"""Keyring-backed credential store (MEJORAS.md §7.1).

All cloud credentials persist exclusively through the OS keychain:

* macOS — Keychain via ``keyring.backends.macOS``.
* Linux — Secret Service via ``keyring.backends.SecretService``.
* Windows — Credential Manager via ``keyring.backends.Windows``.

The module never writes to ``.env`` files and never persists values to disk
itself. The only state lives in the OS keychain, scoped by ``SERVICE_PREFIX``.

A missing or unusable backend (``keyring.errors.NoKeyringError``) raises
:class:`KeyringUnavailableError` from save/delete and degrades load to ``None``
so callers can fall back to env vars without crashing in headless CI.
"""
from __future__ import annotations

import os
from typing import Iterable

# Service name scope inside the keychain. One entry per (service, key).
SERVICE_PREFIX = "aetherdeploy"

# The same allowlist as ``cli_credentials.ALLOWED_CREDENTIAL_KEYS`` — duplicated
# here to keep this module standalone (no circular import on startup).
ALLOWED_CREDENTIAL_KEYS: frozenset[str] = frozenset(
    {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "AZURE_TENANT_ID",
        "LOCALSTACK_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "AETHER_LLM_API_KEY",
    }
)


class KeyringUnavailableError(RuntimeError):
    """Raised when the platform keyring backend is not usable."""


def _service_name(provider: str | None = None) -> str:
    return f"{SERVICE_PREFIX}:{provider}" if provider else SERVICE_PREFIX


def _keyring():
    try:
        import keyring  # type: ignore[import]
    except ImportError as exc:  # pragma: no cover — keyring is a hard dep, but be defensive
        raise KeyringUnavailableError("`keyring` package is not installed.") from exc
    return keyring


def is_keyring_available() -> bool:
    """True when ``keyring`` is importable AND a real backend is wired up."""
    try:
        keyring = _keyring()
    except KeyringUnavailableError:
        return False
    try:
        backend = keyring.get_keyring()
    except Exception:  # noqa: BLE001
        return False
    name = type(backend).__name__.lower()
    # The "fail" / "null" backends are placeholders that always raise on use;
    # treat them as unavailable so callers don't try to write.
    return "fail" not in name and "null" not in name


def save_credential(key: str, value: str, provider: str | None = None) -> None:
    """Stores ``value`` under ``(service, key)`` in the OS keychain.

    Caller responsibility: ``key`` should appear in :data:`ALLOWED_CREDENTIAL_KEYS`.
    The store does not enforce the allowlist so it can also hold service-specific
    extras (e.g. ``AWS_REGION``) when the operator opts in.
    """
    if not value:
        raise ValueError("refusing to store an empty credential value")
    keyring = _keyring()
    try:
        keyring.set_password(_service_name(provider), key, value)
    except Exception as exc:  # noqa: BLE001 — keyring.errors hierarchy isn't stable
        raise KeyringUnavailableError(f"keyring set_password failed: {exc}") from exc


def load_credential(key: str, provider: str | None = None) -> str | None:
    """Returns the stored value, or ``None`` when missing or backend unavailable."""
    try:
        keyring = _keyring()
    except KeyringUnavailableError:
        return None
    try:
        return keyring.get_password(_service_name(provider), key)
    except Exception:  # noqa: BLE001
        return None


def delete_credential(key: str, provider: str | None = None) -> bool:
    """Removes the entry. Returns ``True`` when something was deleted."""
    try:
        keyring = _keyring()
    except KeyringUnavailableError:
        return False
    try:
        existing = keyring.get_password(_service_name(provider), key)
        if existing is None:
            return False
        keyring.delete_password(_service_name(provider), key)
        return True
    except Exception:  # noqa: BLE001
        return False


def list_credentials(
    provider: str | None = None,
    keys: Iterable[str] = ALLOWED_CREDENTIAL_KEYS,
) -> dict[str, bool]:
    """Returns ``{key: stored?}`` for each allow-listed key.

    Does not return the values themselves — listing is a presence check used to
    render "AWS ✓ / GCP ✗" badges without surfacing secrets to logs.
    """
    out: dict[str, bool] = {}
    for key in keys:
        out[key] = load_credential(key, provider) is not None
    return out


def hydrate_environment(
    provider: str | None = None,
    keys: Iterable[str] = ALLOWED_CREDENTIAL_KEYS,
    override: bool = False,
) -> list[str]:
    """Pulls stored credentials into ``os.environ`` for the current process.

    Pattern: on CLI start, read every allow-listed key from the keychain and
    inject any missing value into ``os.environ``. Already-set env vars take
    precedence unless ``override=True``. Returns the list of keys that were
    actually injected.

    Caller-side opt-out: respect ``AETHER_CREDS_NO_HYDRATE=1``.
    """
    if os.environ.get("AETHER_CREDS_NO_HYDRATE") == "1":
        return []

    injected: list[str] = []
    for key in keys:
        if not override and os.environ.get(key):
            continue
        value = load_credential(key, provider)
        if value:
            os.environ[key] = value
            injected.append(key)
    return injected
