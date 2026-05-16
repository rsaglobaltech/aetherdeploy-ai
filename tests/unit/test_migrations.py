"""Tests for the migration pipeline (MEJORAS.md §2.2)."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aetherdeploy.agent.context import _emitter_var
from aetherdeploy.agent.nodes import migration as migration_node_mod
from aetherdeploy.migrations import detector, runner
from aetherdeploy.migrations.runner import EcsRunTaskTarget, MigrationRunResult


@contextmanager
def _emitter_ctx():
    events: list[dict] = []
    token = _emitter_var.set(events.append)
    try:
        yield events
    finally:
        _emitter_var.reset(token)


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

def test_detect_flyway_in_spring_layout(tmp_path: Path):
    mig = tmp_path / "src" / "main" / "resources" / "db" / "migration"
    mig.mkdir(parents=True)
    (mig / "V1__init.sql").write_text("CREATE TABLE x();")
    (mig / "V2__add_index.sql").write_text("CREATE INDEX i ON x();")

    plans = detector.detect_migrations(tmp_path)
    assert len(plans) == 1
    assert plans[0].tool == "flyway"
    assert plans[0].migrations_dir == mig
    assert plans[0].command[0] == "flyway"
    assert plans[0].extras["script_count"] == "2"


def test_detect_alembic_via_ini(tmp_path: Path):
    (tmp_path / "alembic.ini").write_text("[alembic]\nscript_location = alembic")
    versions = tmp_path / "alembic" / "versions"
    versions.mkdir(parents=True)
    plans = detector.detect_migrations(tmp_path)
    assert any(p.tool == "alembic" for p in plans)


def test_detect_prisma_via_schema(tmp_path: Path):
    schema = tmp_path / "prisma" / "schema.prisma"
    schema.parent.mkdir()
    schema.write_text("datasource db { provider = \"postgresql\" }")
    plans = detector.detect_migrations(tmp_path)
    assert any(p.tool == "prisma" for p in plans)


def test_detect_django(tmp_path: Path):
    (tmp_path / "manage.py").write_text("#!/usr/bin/env python\n")
    mig = tmp_path / "app" / "migrations"
    mig.mkdir(parents=True)
    (mig / "__init__.py").write_text("")
    (mig / "0001_initial.py").write_text("class Migration: pass\n")
    plans = detector.detect_migrations(tmp_path)
    assert any(p.tool == "django" for p in plans)


def test_detect_returns_empty_for_pristine_project(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}")
    plans = detector.detect_migrations(tmp_path)
    assert plans == []


def test_select_primary_plan_prefers_orm():
    flyway = detector.MigrationPlan(
        tool="flyway",
        migrations_dir=Path("/tmp/x"),
        command=["flyway", "migrate"],
        image_hint="flyway/flyway",
        env_var="FLYWAY_URL",
        description="",
        detected_at=Path("/tmp/x"),
    )
    django = detector.MigrationPlan(
        tool="django",
        migrations_dir=Path("/tmp/y"),
        command=["python", "manage.py", "migrate"],
        image_hint="python:3.12",
        env_var="DATABASE_URL",
        description="",
        detected_at=Path("/tmp/y/manage.py"),
    )
    assert detector.select_primary_plan([flyway, django]).tool == "django"


# ---------------------------------------------------------------------------
# Docker runner
# ---------------------------------------------------------------------------

def _fake_subprocess(exit_code: int = 0, stdout: bytes = b"line1\nline2\n", stderr: bytes = b""):
    proc = MagicMock()
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()

    out_lines = stdout.split(b"\n")
    err_lines = stderr.split(b"\n")

    async def out_readline():
        if out_lines:
            return out_lines.pop(0) + (b"\n" if out_lines else b"")
        return b""

    async def err_readline():
        if err_lines:
            return err_lines.pop(0) + (b"\n" if err_lines else b"")
        return b""

    proc.stdout.readline = out_readline
    proc.stderr.readline = err_readline
    proc.wait = AsyncMock(return_value=exit_code)
    return proc


def test_run_migration_docker_success(tmp_path: Path):
    plan = detector.MigrationPlan(
        tool="alembic",
        migrations_dir=tmp_path / "alembic",
        command=["alembic", "upgrade", "head"],
        image_hint="python:3.12-slim",
        env_var="DATABASE_URL",
        description="",
        detected_at=tmp_path,
    )

    async def fake_create(*args, **kwargs):
        return _fake_subprocess(exit_code=0, stdout=b"applied 3 migrations\n")

    with patch("aetherdeploy.migrations.runner.asyncio.create_subprocess_exec", side_effect=fake_create):
        result = asyncio.run(
            runner.run_migration_docker(
                plan=plan,
                database_url="postgres://x",
                project_path=tmp_path,
            )
        )
    assert result.ok
    assert result.exit_code == 0
    assert result.backend == "docker"
    assert result.tool == "alembic"


def test_run_migration_docker_failure(tmp_path: Path):
    plan = detector.MigrationPlan(
        tool="alembic",
        migrations_dir=tmp_path,
        command=["alembic", "upgrade", "head"],
        image_hint="python:3.12-slim",
        env_var="DATABASE_URL",
        description="",
        detected_at=tmp_path,
    )

    async def fake_create(*args, **kwargs):
        return _fake_subprocess(exit_code=2, stdout=b"", stderr=b"sqlalchemy error")

    with patch("aetherdeploy.migrations.runner.asyncio.create_subprocess_exec", side_effect=fake_create):
        result = asyncio.run(
            runner.run_migration_docker(
                plan=plan,
                database_url="postgres://x",
                project_path=tmp_path,
            )
        )
    assert not result.ok
    assert result.exit_code == 2
    assert "sqlalchemy error" in (result.error or "")


# ---------------------------------------------------------------------------
# ECS runner
# ---------------------------------------------------------------------------

def _plan_alembic() -> detector.MigrationPlan:
    return detector.MigrationPlan(
        tool="alembic",
        migrations_dir=Path("/tmp"),
        command=["alembic", "upgrade", "head"],
        image_hint="python:3.12-slim",
        env_var="DATABASE_URL",
        description="",
        detected_at=Path("/tmp"),
    )


def _ecs_target() -> EcsRunTaskTarget:
    return EcsRunTaskTarget(
        cluster="my-cluster",
        task_definition="migrations:1",
        container_name="app",
        subnets=["subnet-aaa"],
        security_groups=["sg-aaa"],
        region="us-east-1",
    )


def test_run_migration_ecs_success(monkeypatch):
    client = MagicMock()
    client.run_task.return_value = {"tasks": [{"taskArn": "arn:task/1"}]}
    client.describe_tasks.side_effect = [
        {"tasks": [{"lastStatus": "RUNNING", "containers": [{}]}]},
        {"tasks": [{"lastStatus": "STOPPED", "containers": [{"exitCode": 0}]}]},
    ]
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)

    result = runner.run_migration_ecs(
        plan=_plan_alembic(),
        database_url="postgres://x",
        target=_ecs_target(),
        wait_timeout_s=5,
        poll_interval_s=1,
        boto3_client=client,
    )
    assert result.ok
    assert result.exit_code == 0
    assert result.task_id == "arn:task/1"


def test_run_migration_ecs_propagates_exit_code(monkeypatch):
    client = MagicMock()
    client.run_task.return_value = {"tasks": [{"taskArn": "arn:task/2"}]}
    client.describe_tasks.return_value = {"tasks": [{"lastStatus": "STOPPED", "containers": [{"exitCode": 1}]}]}
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)

    result = runner.run_migration_ecs(
        plan=_plan_alembic(),
        database_url="postgres://x",
        target=_ecs_target(),
        boto3_client=client,
    )
    assert not result.ok
    assert result.exit_code == 1


def test_run_migration_ecs_reports_run_task_failures(monkeypatch):
    client = MagicMock()
    client.run_task.return_value = {"tasks": [], "failures": [{"reason": "RESOURCE:CPU"}]}
    result = runner.run_migration_ecs(
        plan=_plan_alembic(),
        database_url="x",
        target=_ecs_target(),
        boto3_client=client,
    )
    assert not result.ok
    assert "RESOURCE:CPU" in (result.error or "")


# ---------------------------------------------------------------------------
# migration_node
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, success=True, environments=None):
        self.success = success
        self.environments = environments or {}


def test_migration_node_skips_dry_run():
    with _emitter_ctx():
        out = asyncio.run(migration_node_mod.migration_node({"dry_run": True}))
    assert out["current_step"] == "migrations_skipped"


def test_migration_node_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("AETHER_MIGRATIONS_DISABLED", "1")
    with _emitter_ctx():
        out = asyncio.run(
            migration_node_mod.migration_node(
                {"target_environments": ["prod"], "requested_action": "deploy"}
            )
        )
    assert out["current_step"] == "migrations_skipped"


def test_migration_node_skips_when_no_migrations_detected(tmp_path: Path):
    with _emitter_ctx():
        out = asyncio.run(
            migration_node_mod.migration_node(
                {
                    "project_path": str(tmp_path),
                    "target_environments": ["prod"],
                    "requested_action": "deploy",
                }
            )
        )
    assert out["current_step"] == "migrations_skipped"


def test_migration_node_runs_feature_via_docker(tmp_path: Path, monkeypatch):
    (tmp_path / "alembic.ini").write_text("[alembic]\nscript_location = .")
    (tmp_path / "versions").mkdir()
    monkeypatch.setenv("DATABASE_URL", "postgres://feature")

    ok = MigrationRunResult(ok=True, backend="docker", tool="alembic", exit_code=0)
    with patch.object(migration_node_mod, "run_migration_docker", new=AsyncMock(return_value=ok)) as runner_mock:
        with _emitter_ctx():
            out = asyncio.run(
                migration_node_mod.migration_node(
                    {
                        "project_path": str(tmp_path),
                        "target_environments": ["feature"],
                        "requested_action": "deploy",
                    }
                )
            )
    assert out["current_step"] == "migrations_done"
    assert out["migration_results"]["feature"]["ok"]
    runner_mock.assert_awaited_once()


def test_migration_node_records_failures(tmp_path: Path, monkeypatch):
    (tmp_path / "alembic.ini").write_text("[alembic]\nscript_location = .")
    monkeypatch.setenv("DATABASE_URL", "postgres://feature")

    failed = MigrationRunResult(ok=False, backend="docker", tool="alembic", exit_code=2, error="syntax error")
    with patch.object(migration_node_mod, "run_migration_docker", new=AsyncMock(return_value=failed)):
        with _emitter_ctx():
            out = asyncio.run(
                migration_node_mod.migration_node(
                    {
                        "project_path": str(tmp_path),
                        "target_environments": ["feature"],
                        "requested_action": "deploy",
                    }
                )
            )
    assert out["current_step"] == "migrations_error"
    assert any("syntax error" in e for e in out["errors"])


def test_migration_node_prod_without_target_is_soft_skip(tmp_path: Path, monkeypatch):
    (tmp_path / "alembic.ini").write_text("[alembic]\n")
    monkeypatch.setenv("DATABASE_URL", "postgres://prod")
    with _emitter_ctx():
        out = asyncio.run(
            migration_node_mod.migration_node(
                {
                    "project_path": str(tmp_path),
                    "target_environments": ["prod"],
                    "requested_action": "deploy",
                }
            )
        )
    assert out["current_step"] == "migrations_done"
    assert out["migration_results"]["prod"]["ok"]
    assert "no ECS migration target" in (out["migration_results"]["prod"]["error"] or "")


def test_migration_node_prod_with_target_runs_ecs(tmp_path: Path, monkeypatch):
    (tmp_path / "alembic.ini").write_text("[alembic]\n")
    monkeypatch.setenv("AETHER_DB_URL_PROD", "postgres://prod")

    ok = MigrationRunResult(ok=True, backend="ecs", tool="alembic", exit_code=0, task_id="arn:task/1")
    with patch.object(migration_node_mod, "run_migration_ecs", return_value=ok) as ecs_mock:
        with _emitter_ctx():
            out = asyncio.run(
                migration_node_mod.migration_node(
                    {
                        "project_path": str(tmp_path),
                        "target_environments": ["prod"],
                        "requested_action": "deploy",
                        "migration_targets": {
                            "prod": {
                                "cluster": "c",
                                "task_definition": "td",
                                "container_name": "app",
                                "subnets": ["subnet-1"],
                                "region": "us-east-1",
                            }
                        },
                    }
                )
            )
    assert out["current_step"] == "migrations_done"
    assert out["migration_results"]["prod"]["task_id"] == "arn:task/1"
    ecs_mock.assert_called_once()
