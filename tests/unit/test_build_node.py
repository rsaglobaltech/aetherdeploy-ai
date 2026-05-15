"""Tests for the image build & push node (MEJORAS.md §2.1)."""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from aetherdeploy.agent.context import _emitter_var
from aetherdeploy.agent.nodes import build as build_module
from aetherdeploy.docker import builder as builder_module


@contextmanager
def _emitter_ctx():
    """Sets a noop emitter so the node can call ``emit({...})`` freely in tests."""
    events: list[dict] = []
    token = _emitter_var.set(events.append)
    try:
        yield events
    finally:
        _emitter_var.reset(token)


class _StubProposal:
    def __init__(self, provider: str = "aws", region: str = "us-east-1"):
        self.provider = provider
        self.region = region


def test_detect_dockerfile_in_root(tmp_path: Path):
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    found = builder_module.detect_dockerfile(tmp_path)
    assert found == tmp_path / "Dockerfile"


def test_detect_dockerfile_in_docker_subdir(tmp_path: Path):
    (tmp_path / "docker").mkdir()
    df = tmp_path / "docker" / "Dockerfile"
    df.write_text("FROM scratch\n")
    assert builder_module.detect_dockerfile(tmp_path) == df


def test_detect_dockerfile_missing(tmp_path: Path):
    assert builder_module.detect_dockerfile(tmp_path) is None


def test_compute_image_tag_falls_back_when_no_git(tmp_path: Path):
    tag = builder_module.compute_image_tag(tmp_path)
    # Without a git repo, the helper returns a timestamp-based tag.
    assert tag.startswith("build-") or tag.isalnum()


def test_envs_needing_image_filters_local_and_feature():
    state = {
        "requested_action": "deploy",
        "target_environments": ["local", "feature", "prod"],
    }
    assert build_module._envs_needing_image(state) == ["prod"]


def test_envs_needing_image_skips_non_deploy_actions():
    state = {
        "requested_action": "plan",
        "target_environments": ["prod"],
    }
    assert build_module._envs_needing_image(state) == []


def test_build_node_skips_when_no_targets():
    with _emitter_ctx():
        result = asyncio.run(
            build_module.build_node(
                {
                    "project_path": ".",
                    "requested_action": "deploy",
                    "target_environments": ["local"],
                }
            )
        )
    assert result["current_step"] == "build_skipped"


def test_build_node_skips_when_dry_run():
    with _emitter_ctx():
        result = asyncio.run(
            build_module.build_node(
                {
                    "project_path": ".",
                    "requested_action": "deploy",
                    "target_environments": ["prod"],
                    "dry_run": True,
                }
            )
        )
    assert result["current_step"] == "build_skipped"


def test_build_node_skips_when_disabled_via_env(monkeypatch):
    monkeypatch.setenv("AETHER_BUILD_DISABLED", "1")
    with _emitter_ctx():
        result = asyncio.run(
            build_module.build_node(
                {
                    "project_path": ".",
                    "requested_action": "deploy",
                    "target_environments": ["prod"],
                }
            )
        )
    assert result["current_step"] == "build_skipped"


def test_build_node_skips_when_no_dockerfile(tmp_path: Path):
    with _emitter_ctx():
        result = asyncio.run(
            build_module.build_node(
                {
                    "project_path": str(tmp_path),
                    "requested_action": "deploy",
                    "target_environments": ["prod"],
                    "architecture_proposal": _StubProposal(),
                }
            )
        )
    assert result["current_step"] == "build_skipped"


def test_build_node_skips_unsupported_provider(tmp_path: Path):
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    with patch.object(build_module, "is_docker_available", return_value=True):
        with _emitter_ctx():
            result = asyncio.run(
                build_module.build_node(
                    {
                        "project_path": str(tmp_path),
                        "requested_action": "deploy",
                        "target_environments": ["prod"],
                        "architecture_proposal": _StubProposal(provider="gcp"),
                    }
                )
            )
    assert result["current_step"] == "build_skipped"


def test_build_node_errors_when_docker_missing(tmp_path: Path):
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    with patch.object(build_module, "is_docker_available", return_value=False):
        with _emitter_ctx():
            result = asyncio.run(
                build_module.build_node(
                    {
                        "project_path": str(tmp_path),
                        "requested_action": "deploy",
                        "target_environments": ["prod"],
                        "architecture_proposal": _StubProposal(),
                    }
                )
            )
    assert result["current_step"] == "build_error"
    assert any("Docker" in e for e in result["errors"])


def _fake_repo():
    from aetherdeploy.providers.aws.registry import EcrRepository
    return EcrRepository(
        name="myapp/prod",
        uri="123456789012.dkr.ecr.us-east-1.amazonaws.com/myapp/prod",
        registry="123456789012.dkr.ecr.us-east-1.amazonaws.com",
        region="us-east-1",
    )


def _fake_creds():
    from aetherdeploy.providers.aws.registry import EcrCredentials
    return EcrCredentials(
        registry="123456789012.dkr.ecr.us-east-1.amazonaws.com",
        username="AWS",
        password="secret-token",
    )


def _ok_build():
    return builder_module.BuildResult(
        ok=True,
        image_uri="123456789012.dkr.ecr.us-east-1.amazonaws.com/myapp/prod:abc1234",
        tag="abc1234",
    )


def test_build_node_full_aws_happy_path(tmp_path: Path, monkeypatch):
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")

    with patch.object(build_module, "is_docker_available", return_value=True), \
         patch("aetherdeploy.providers.aws.registry.ensure_ecr_repository", return_value=_fake_repo()), \
         patch("aetherdeploy.providers.aws.registry.get_ecr_credentials", return_value=_fake_creds()), \
         patch.object(build_module, "docker_login", new=AsyncMock(return_value=builder_module.BuildResult(ok=True))), \
         patch.object(build_module, "build_image", new=AsyncMock(return_value=_ok_build())), \
         patch.object(build_module, "push_image", new=AsyncMock(return_value=builder_module.BuildResult(ok=True))), \
         patch.object(build_module, "compute_image_tag", return_value="abc1234"):
        with _emitter_ctx():
            result = asyncio.run(
                build_module.build_node(
                    {
                        "project_path": str(tmp_path),
                        "requested_action": "deploy",
                        "target_environments": ["prod"],
                        "architecture_proposal": _StubProposal(),
                    }
                )
            )

    assert result["current_step"] == "build_done"
    assert "prod" in result["image_builds"]
    prod = result["image_builds"]["prod"]
    assert prod["ok"]
    assert prod["tag"] == "abc1234"
    assert prod["image_uri"].endswith(":abc1234")

    tfvars_path = tmp_path / ".aetherdeploy" / "terraform" / "prod" / "terraform.tfvars.json"
    assert tfvars_path.exists()
    payload = json.loads(tfvars_path.read_text())
    assert payload["container_image"] == prod["image_uri"]
    assert payload["image_uri"] == prod["image_uri"]


def test_build_node_surfaces_build_failure(tmp_path: Path):
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    failed_build = builder_module.BuildResult(ok=False, error="docker build exited with 1")

    with patch.object(build_module, "is_docker_available", return_value=True), \
         patch("aetherdeploy.providers.aws.registry.ensure_ecr_repository", return_value=_fake_repo()), \
         patch("aetherdeploy.providers.aws.registry.get_ecr_credentials", return_value=_fake_creds()), \
         patch.object(build_module, "docker_login", new=AsyncMock(return_value=builder_module.BuildResult(ok=True))), \
         patch.object(build_module, "build_image", new=AsyncMock(return_value=failed_build)), \
         patch.object(build_module, "push_image", new=AsyncMock(return_value=builder_module.BuildResult(ok=True))):
        with _emitter_ctx():
            result = asyncio.run(
                build_module.build_node(
                    {
                        "project_path": str(tmp_path),
                        "requested_action": "deploy",
                        "target_environments": ["prod"],
                        "architecture_proposal": _StubProposal(),
                    }
                )
            )

    assert result["current_step"] == "build_error"
    assert any("docker build exited with 1" in e for e in result["errors"])


def test_write_tfvars_merges_existing(tmp_path: Path):
    path = tmp_path / ".aetherdeploy" / "terraform" / "prod"
    path.mkdir(parents=True)
    (path / "terraform.tfvars.json").write_text(json.dumps({"existing_key": "value"}))
    written = build_module._write_tfvars(tmp_path, "prod", {"container_image": "u:tag"})
    payload = json.loads(written.read_text())
    assert payload["existing_key"] == "value"
    assert payload["container_image"] == "u:tag"


def test_derive_repository_name_sanitizes():
    from aetherdeploy.providers.aws.registry import derive_repository_name
    assert derive_repository_name("My Cool App", "prod") == "my-cool-app/prod"
    # Empty/invalid base name collapses to a safe default.
    assert derive_repository_name("@@@", "feature") == "app/feature"
    # ECR allows underscores, so they are preserved.
    assert derive_repository_name("data_pipeline", "prod") == "data_pipeline/prod"
