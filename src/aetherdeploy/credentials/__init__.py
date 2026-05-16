"""Secure credential storage (MEJORAS.md §7.1)."""
from .store import (
    ALLOWED_CREDENTIAL_KEYS,
    SERVICE_PREFIX,
    KeyringUnavailableError,
    delete_credential,
    hydrate_environment,
    is_keyring_available,
    list_credentials,
    load_credential,
    save_credential,
)

__all__ = [
    "ALLOWED_CREDENTIAL_KEYS",
    "SERVICE_PREFIX",
    "KeyringUnavailableError",
    "delete_credential",
    "hydrate_environment",
    "is_keyring_available",
    "list_credentials",
    "load_credential",
    "save_credential",
]
