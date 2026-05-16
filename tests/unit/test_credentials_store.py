"""Tests for the keyring-backed credential store (MEJORAS.md §7.1)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from aetherdeploy.credentials import store


class _FakeKeyring:
    """In-memory keyring backend used to test save/load/delete round-trips."""

    def __init__(self):
        self.data: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, key: str, value: str) -> None:
        self.data[(service, key)] = value

    def get_password(self, service: str, key: str) -> str | None:
        return self.data.get((service, key))

    def delete_password(self, service: str, key: str) -> None:
        if (service, key) not in self.data:
            raise KeyError(key)
        del self.data[(service, key)]

    # The shim the store uses to detect a usable backend
    def get_keyring(self):
        return self  # treat the fake as the active backend


class _NullKeyring:
    """Backend whose name contains 'fail' — should be treated as unavailable."""

    name = "fail.Keyring"

    def get_keyring(self):
        return self


@pytest.fixture
def fake_keyring():
    fake = _FakeKeyring()
    with patch.object(store, "_keyring", return_value=fake):
        yield fake


@pytest.fixture
def null_keyring():
    null = _NullKeyring()
    with patch.object(store, "_keyring", return_value=null):
        yield null


# ---------------------------------------------------------------------------
# Backend availability detection
# ---------------------------------------------------------------------------

def test_is_keyring_available_true_with_real_backend(fake_keyring):
    assert store.is_keyring_available()


def test_is_keyring_available_false_with_fail_backend(null_keyring):
    assert not store.is_keyring_available()


def test_is_keyring_available_false_when_import_fails():
    def raise_import():
        raise store.KeyringUnavailableError("missing")

    with patch.object(store, "_keyring", side_effect=raise_import):
        assert not store.is_keyring_available()


# ---------------------------------------------------------------------------
# save / load / delete
# ---------------------------------------------------------------------------

def test_save_and_load_round_trip(fake_keyring):
    store.save_credential("AWS_ACCESS_KEY_ID", "AKIA123")
    assert store.load_credential("AWS_ACCESS_KEY_ID") == "AKIA123"


def test_save_scoped_by_provider_does_not_collide(fake_keyring):
    store.save_credential("AWS_ACCESS_KEY_ID", "key-prod", provider="aws-prod")
    store.save_credential("AWS_ACCESS_KEY_ID", "key-dev", provider="aws-dev")
    assert store.load_credential("AWS_ACCESS_KEY_ID", "aws-prod") == "key-prod"
    assert store.load_credential("AWS_ACCESS_KEY_ID", "aws-dev") == "key-dev"


def test_save_rejects_empty_value(fake_keyring):
    with pytest.raises(ValueError):
        store.save_credential("AWS_ACCESS_KEY_ID", "")


def test_load_returns_none_when_missing(fake_keyring):
    assert store.load_credential("AWS_ACCESS_KEY_ID") is None


def test_load_returns_none_when_keyring_unavailable():
    def raise_import():
        raise store.KeyringUnavailableError("missing")

    with patch.object(store, "_keyring", side_effect=raise_import):
        assert store.load_credential("AWS_ACCESS_KEY_ID") is None


def test_delete_returns_true_when_existed(fake_keyring):
    store.save_credential("AWS_ACCESS_KEY_ID", "AKIA123")
    assert store.delete_credential("AWS_ACCESS_KEY_ID") is True
    assert store.load_credential("AWS_ACCESS_KEY_ID") is None


def test_delete_returns_false_when_absent(fake_keyring):
    assert store.delete_credential("AWS_ACCESS_KEY_ID") is False


def test_list_credentials_reports_presence_only(fake_keyring):
    store.save_credential("AWS_ACCESS_KEY_ID", "AKIA123")
    presence = store.list_credentials()
    assert presence["AWS_ACCESS_KEY_ID"] is True
    assert presence["AWS_SECRET_ACCESS_KEY"] is False


# ---------------------------------------------------------------------------
# hydrate_environment
# ---------------------------------------------------------------------------

def test_hydrate_injects_missing_keys(fake_keyring, monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    store.save_credential("AWS_ACCESS_KEY_ID", "AKIA123")
    injected = store.hydrate_environment()
    assert "AWS_ACCESS_KEY_ID" in injected
    import os
    assert os.environ.get("AWS_ACCESS_KEY_ID") == "AKIA123"
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)


def test_hydrate_does_not_overwrite_existing_env(fake_keyring, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "from-env")
    store.save_credential("AWS_ACCESS_KEY_ID", "from-keyring")
    injected = store.hydrate_environment()
    assert "AWS_ACCESS_KEY_ID" not in injected
    import os
    assert os.environ["AWS_ACCESS_KEY_ID"] == "from-env"


def test_hydrate_overrides_when_requested(fake_keyring, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "from-env")
    store.save_credential("AWS_ACCESS_KEY_ID", "from-keyring")
    injected = store.hydrate_environment(override=True)
    assert "AWS_ACCESS_KEY_ID" in injected
    import os
    assert os.environ["AWS_ACCESS_KEY_ID"] == "from-keyring"


def test_hydrate_respects_no_hydrate_env_flag(fake_keyring, monkeypatch):
    monkeypatch.setenv("AETHER_CREDS_NO_HYDRATE", "1")
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    store.save_credential("AWS_ACCESS_KEY_ID", "AKIA123")
    assert store.hydrate_environment() == []
    import os
    assert os.environ.get("AWS_ACCESS_KEY_ID") is None
