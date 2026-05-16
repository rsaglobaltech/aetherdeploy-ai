"""Tests for the secrets pipeline (MEJORAS.md §3.3)."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aetherdeploy.agent.context import _emitter_var
from aetherdeploy.agent.nodes import secrets as secrets_node_mod
from aetherdeploy.secrets import detector, generator, provisioner


@contextmanager
def _emitter_ctx():
    events: list[dict] = []
    token = _emitter_var.set(events.append)
    try:
        yield events
    finally:
        _emitter_var.reset(token)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", [
    "DATABASE_URL", "JWT_SECRET", "STRIPE_API_KEY", "AWS_ACCESS_KEY",
    "DB_PASSWORD", "REDIS_URL", "MONGODB_URI", "TWILIO_AUTH_TOKEN",
])
def test_looks_like_secret_positives(key):
    assert detector.looks_like_secret(key)


@pytest.mark.parametrize("key", [
    "API_KEY_HEADER_NAME", "JWT_HEADER", "PUBLIC_KEY_URL", "DEBUG", "PORT", "",
])
def test_looks_like_secret_negatives(key):
    assert not detector.looks_like_secret(key)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def test_detect_secrets_reads_env_example(tmp_path: Path):
    (tmp_path / ".env.example").write_text(
        "PORT=8080\n"
        "# comment\n"
        "DATABASE_URL=postgres://localhost/db\n"
        "JWT_SECRET=changeme\n"
        "export STRIPE_API_KEY=sk_test_xxx\n"
    )
    specs = detector.detect_secrets(tmp_path)
    names = {s.name for s in specs}
    assert names == {"DATABASE_URL", "JWT_SECRET", "STRIPE_API_KEY"}


def test_detect_secrets_dedupes_across_files(tmp_path: Path):
    (tmp_path / ".env.example").write_text("JWT_SECRET=\n")
    (tmp_path / ".env.template").write_text("JWT_SECRET=\n")
    specs = detector.detect_secrets(tmp_path)
    assert len([s for s in specs if s.name == "JWT_SECRET"]) == 1


def test_detect_secrets_handles_spring_properties(tmp_path: Path):
    res = tmp_path / "src" / "main" / "resources"
    res.mkdir(parents=True)
    (res / "application.properties.example").write_text(
        "spring.datasource.password=changeme\n"
        "server.port=8080\n"
        "stripe.api.key=changeme\n"
    )
    specs = detector.detect_secrets(tmp_path)
    names = {s.name for s in specs}
    assert "SPRING_DATASOURCE_PASSWORD" in names
    assert "STRIPE_API_KEY" in names


def test_detect_secrets_empty_when_no_example_files(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}")
    assert detector.detect_secrets(tmp_path) == []


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

def test_generate_aws_secrets_tf_includes_resources_and_output(tmp_path: Path):
    spec = detector.SecretSpec(
        name="JWT_SECRET", source=tmp_path / ".env.example"
    )
    hcl = generator.generate_aws_secrets_tf([spec], project_name="my-api", environment="prod")
    assert 'resource "aws_secretsmanager_secret"' in hcl
    assert 'name        = "my-api/prod/jwt_secret"' in hcl
    assert 'output "aetherdeploy_secret_arns"' in hcl
    assert "aws_secretsmanager_secret.app_secret_jwt_secret.arn" in hcl


def test_generate_aws_secrets_tf_empty_when_no_specs():
    assert generator.generate_aws_secrets_tf([], "p", "prod") == ""


def test_generate_secrets_tf_dispatches_on_provider(tmp_path: Path):
    spec = detector.SecretSpec(name="JWT_SECRET", source=tmp_path / ".env.example")
    assert "aws_secretsmanager_secret" in generator.generate_secrets_tf("aws", [spec], "p", "prod")
    # Non-AWS providers return empty string for now.
    assert generator.generate_secrets_tf("gcp", [spec], "p", "prod") == ""


def test_write_secrets_tf_creates_file(tmp_path: Path):
    spec = detector.SecretSpec(name="JWT_SECRET", source=tmp_path / ".env.example")
    hcl = generator.generate_aws_secrets_tf([spec], "p", "prod")
    out = generator.write_secrets_tf(hcl, tmp_path / "tf")
    assert out is not None and out.exists()
    assert "aws_secretsmanager_secret" in out.read_text()


def test_write_secrets_tf_is_noop_on_empty(tmp_path: Path):
    out = generator.write_secrets_tf("", tmp_path / "tf")
    assert out is None


# ---------------------------------------------------------------------------
# Provisioner
# ---------------------------------------------------------------------------

def test_provision_writes_when_env_var_set(monkeypatch, tmp_path: Path):
    spec = detector.SecretSpec(name="JWT_SECRET", source=tmp_path / ".env.example")
    monkeypatch.setenv("AETHER_SECRET_VALUE_JWT_SECRET", "actual-value")

    client = MagicMock()
    client.put_secret_value.return_value = {"ARN": "arn:aws:secretsmanager:us-east-1:1:secret/p/prod/jwt_secret"}

    results = provisioner.provision_aws_secret_values(
        [spec], project_name="p", environment="prod", boto3_client=client
    )
    assert len(results) == 1
    assert results[0].status == "written"
    assert results[0].secret_arn and "jwt_secret" in results[0].secret_arn
    client.put_secret_value.assert_called_once_with(
        SecretId="p/prod/jwt_secret", SecretString="actual-value"
    )


def test_provision_reports_missing(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("AETHER_SECRET_VALUE_JWT_SECRET", raising=False)
    spec = detector.SecretSpec(name="JWT_SECRET", source=tmp_path / ".env.example")
    client = MagicMock()
    results = provisioner.provision_aws_secret_values(
        [spec], project_name="p", environment="prod", boto3_client=client
    )
    assert results[0].status == "missing"
    client.put_secret_value.assert_not_called()


def test_provision_records_per_secret_error(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AETHER_SECRET_VALUE_JWT_SECRET", "v")
    spec = detector.SecretSpec(name="JWT_SECRET", source=tmp_path / ".env.example")
    client = MagicMock()
    client.put_secret_value.side_effect = Exception("AccessDeniedException")

    results = provisioner.provision_aws_secret_values(
        [spec], project_name="p", environment="prod", boto3_client=client
    )
    assert results[0].status == "error"
    assert "AccessDeniedException" in (results[0].error or "")


# ---------------------------------------------------------------------------
# secrets_node
# ---------------------------------------------------------------------------

class _StubProposal:
    def __init__(self, provider="aws", region="us-east-1"):
        self.provider = provider
        self.region = region


def test_secrets_node_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("AETHER_SECRETS_DISABLED", "1")
    with _emitter_ctx():
        out = asyncio.run(secrets_node_mod.secrets_node({"target_environments": ["prod"]}))
    assert out["current_step"] == "secrets_skipped"


def test_secrets_node_skips_when_no_examples(tmp_path: Path):
    with _emitter_ctx():
        out = asyncio.run(
            secrets_node_mod.secrets_node(
                {
                    "project_path": str(tmp_path),
                    "target_environments": ["prod"],
                    "architecture_proposal": _StubProposal(),
                }
            )
        )
    assert out["current_step"] == "secrets_skipped"


def test_secrets_node_writes_tf_when_detected(tmp_path: Path, monkeypatch):
    (tmp_path / ".env.example").write_text("JWT_SECRET=\nDATABASE_URL=\n")
    tf_dir = tmp_path / ".aetherdeploy" / "terraform" / "prod"
    tf_dir.mkdir(parents=True)
    monkeypatch.delenv("AETHER_SECRET_VALUE_JWT_SECRET", raising=False)
    monkeypatch.delenv("AETHER_SECRET_VALUE_DATABASE_URL", raising=False)

    with _emitter_ctx():
        out = asyncio.run(
            secrets_node_mod.secrets_node(
                {
                    "project_path": str(tmp_path),
                    "target_environments": ["prod"],
                    "architecture_proposal": _StubProposal(),
                }
            )
        )

    assert out["current_step"] == "secrets_done"
    secrets_tf = tf_dir / "secrets.tf"
    assert secrets_tf.exists()
    content = secrets_tf.read_text()
    assert "JWT_SECRET" in content
    assert "DATABASE_URL" in content
    # No values supplied → provisioned list empty for prod
    assert out["secrets_results"]["prod"]["provisioned"] == []


def test_secrets_node_provisions_when_values_supplied(tmp_path: Path, monkeypatch):
    (tmp_path / ".env.example").write_text("JWT_SECRET=\n")
    tf_dir = tmp_path / ".aetherdeploy" / "terraform" / "prod"
    tf_dir.mkdir(parents=True)
    monkeypatch.setenv("AETHER_SECRET_VALUE_JWT_SECRET", "real-value")

    with patch.object(secrets_node_mod, "provision_aws_secret_values") as prov:
        prov.return_value = [
            provisioner.SecretProvisionResult(name="JWT_SECRET", status="written", secret_arn="arn:x")
        ]
        with _emitter_ctx():
            out = asyncio.run(
                secrets_node_mod.secrets_node(
                    {
                        "project_path": str(tmp_path),
                        "target_environments": ["prod"],
                        "architecture_proposal": _StubProposal(),
                    }
                )
            )

    prov.assert_called_once()
    written = out["secrets_results"]["prod"]["provisioned"]
    assert written and written[0]["status"] == "written"


def test_secrets_node_skips_feature_env_provisioning(tmp_path: Path, monkeypatch):
    (tmp_path / ".env.example").write_text("JWT_SECRET=\n")
    feature_tf = tmp_path / ".aetherdeploy" / "terraform" / "feature"
    feature_tf.mkdir(parents=True)
    monkeypatch.setenv("AETHER_SECRET_VALUE_JWT_SECRET", "real-value")

    with patch.object(secrets_node_mod, "provision_aws_secret_values") as prov:
        with _emitter_ctx():
            out = asyncio.run(
                secrets_node_mod.secrets_node(
                    {
                        "project_path": str(tmp_path),
                        "target_environments": ["feature"],
                        "architecture_proposal": _StubProposal(),
                    }
                )
            )
    # Feature uses LocalStack — never touch real Secrets Manager
    prov.assert_not_called()
    assert (feature_tf / "secrets.tf").exists()
    assert out["current_step"] == "secrets_done"
