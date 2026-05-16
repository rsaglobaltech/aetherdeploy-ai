"""Tests for the Infracost wrapper and cost gate (MEJORAS.md §3.1)."""
from __future__ import annotations

import asyncio
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from aetherdeploy.agent.context import _emitter_var
from aetherdeploy.agent.nodes import cost as cost_node_mod
from aetherdeploy.cost import infracost as infracost_mod


INFRACOST_SAMPLE = {
    "currency": "USD",
    "totalMonthlyCost": "175.50",
    "totalHourlyCost": "0.24",
    "projects": [
        {
            "name": "main",
            "breakdown": {
                "resources": [
                    {
                        "name": "aws_lambda_function.app",
                        "monthlyCost": "12.00",
                        "hourlyCost": "0.016",
                    },
                    {
                        "name": "aws_rds_cluster.db",
                        "monthlyCost": "150.00",
                        "hourlyCost": "0.21",
                    },
                ],
                "modules": [
                    {
                        "resources": [
                            {"name": "aws_s3_bucket.assets", "monthlyCost": "13.50"}
                        ]
                    }
                ],
            },
        }
    ],
}


@contextmanager
def _emitter_ctx():
    events: list[dict] = []
    token = _emitter_var.set(events.append)
    try:
        yield events
    finally:
        _emitter_var.reset(token)


def _completed(rc: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# infracost runner
# ---------------------------------------------------------------------------

def test_run_infracost_skips_when_binary_missing(tmp_path: Path):
    with patch.object(infracost_mod.shutil, "which", return_value=None):
        report = infracost_mod.run_infracost(tmp_path)
    assert report.ok
    assert "not found" in (report.skipped_reason or "")
    assert report.total_monthly_cost == 0.0


def test_run_infracost_parses_breakdown(tmp_path: Path):
    with patch.object(infracost_mod.shutil, "which", return_value="/bin/infracost"), \
         patch.object(infracost_mod.subprocess, "run", return_value=_completed(0, json.dumps(INFRACOST_SAMPLE))):
        report = infracost_mod.run_infracost(tmp_path)

    assert report.ok
    assert report.total_monthly_cost == 175.50
    assert len(report.breakdown.resources) == 3
    top = report.breakdown.top_resources(2)
    assert top[0].name == "aws_rds_cluster.db"
    assert top[0].monthly_cost == 150.0
    assert top[1].name == "aws_s3_bucket.assets"


def test_run_infracost_handles_non_zero_exit(tmp_path: Path):
    with patch.object(infracost_mod.shutil, "which", return_value="/bin/infracost"), \
         patch.object(infracost_mod.subprocess, "run", return_value=_completed(1, "", "auth error")):
        report = infracost_mod.run_infracost(tmp_path)
    assert not report.ok
    assert any("auth error" in e for e in report.raw_errors)


def test_run_infracost_handles_malformed_json(tmp_path: Path):
    with patch.object(infracost_mod.shutil, "which", return_value="/bin/infracost"), \
         patch.object(infracost_mod.subprocess, "run", return_value=_completed(0, "not json")):
        report = infracost_mod.run_infracost(tmp_path)
    assert not report.ok
    assert any("malformed JSON" in e for e in report.raw_errors)


def test_run_infracost_handles_timeout(tmp_path: Path):
    with patch.object(infracost_mod.shutil, "which", return_value="/bin/infracost"), \
         patch.object(infracost_mod.subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd=["infracost"], timeout=1)):
        report = infracost_mod.run_infracost(tmp_path, timeout_s=1)
    assert not report.ok
    assert any("timed out" in e for e in report.raw_errors)


def test_falls_back_to_summing_resources_when_total_missing(tmp_path: Path):
    payload = {
        "currency": "USD",
        "projects": [
            {
                "breakdown": {
                    "resources": [
                        {"name": "a", "monthlyCost": "5"},
                        {"name": "b", "monthlyCost": "7.5"},
                    ]
                }
            }
        ],
    }
    with patch.object(infracost_mod.shutil, "which", return_value="/bin/infracost"), \
         patch.object(infracost_mod.subprocess, "run", return_value=_completed(0, json.dumps(payload))):
        report = infracost_mod.run_infracost(tmp_path)
    assert report.total_monthly_cost == 12.5


# ---------------------------------------------------------------------------
# cost_node
# ---------------------------------------------------------------------------

def test_cost_node_skips_dry_run():
    with _emitter_ctx():
        out = asyncio.run(cost_node_mod.cost_node({"dry_run": True, "target_environments": ["prod"]}))
    assert out["current_step"] == "cost_skipped"


def test_cost_node_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("AETHER_COST_DISABLED", "1")
    with _emitter_ctx():
        out = asyncio.run(cost_node_mod.cost_node({"target_environments": ["prod"]}))
    assert out["current_step"] == "cost_skipped"


def test_cost_node_skips_local_targets():
    with _emitter_ctx():
        out = asyncio.run(cost_node_mod.cost_node({"target_environments": ["local"]}))
    assert out["current_step"] == "cost_skipped"


def test_cost_node_reports_without_budget(tmp_path: Path):
    tf_dir = tmp_path / ".aetherdeploy" / "terraform" / "prod"
    tf_dir.mkdir(parents=True)

    report = infracost_mod.CostReport()
    report.breakdown.total_monthly_cost = 50.0
    report.breakdown.resources = [
        infracost_mod.ResourceCost(name="aws_x.y", resource_type="aws_x", monthly_cost=50.0)
    ]
    with patch.object(cost_node_mod, "run_infracost", return_value=report):
        with _emitter_ctx():
            out = asyncio.run(
                cost_node_mod.cost_node(
                    {"project_path": str(tmp_path), "target_environments": ["prod"]}
                )
            )
    assert out["current_step"] == "cost_done"
    assert out["cost_results"]["prod"]["total_monthly_cost"] == 50.0
    assert out["cost_results"]["prod"]["budget_usd"] is None


def test_cost_node_blocks_when_budget_exceeded(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AETHER_COST_BUDGET_USD", "100")
    tf_dir = tmp_path / ".aetherdeploy" / "terraform" / "prod"
    tf_dir.mkdir(parents=True)

    report = infracost_mod.CostReport()
    report.breakdown.total_monthly_cost = 175.50
    with patch.object(cost_node_mod, "run_infracost", return_value=report):
        with _emitter_ctx():
            out = asyncio.run(
                cost_node_mod.cost_node(
                    {"project_path": str(tmp_path), "target_environments": ["prod"]}
                )
            )
    assert out["current_step"] == "cost_error"
    assert out["cost_results"]["prod"]["over_budget"]
    assert any("exceeds budget" in e for e in out["errors"])


def test_cost_node_env_specific_budget_overrides_global(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AETHER_COST_BUDGET_USD", "10")  # global tight
    monkeypatch.setenv("AETHER_COST_BUDGET_USD_PROD", "500")  # prod relaxed
    tf_dir = tmp_path / ".aetherdeploy" / "terraform" / "prod"
    tf_dir.mkdir(parents=True)

    report = infracost_mod.CostReport()
    report.breakdown.total_monthly_cost = 175.50
    with patch.object(cost_node_mod, "run_infracost", return_value=report):
        with _emitter_ctx():
            out = asyncio.run(
                cost_node_mod.cost_node(
                    {"project_path": str(tmp_path), "target_environments": ["prod"]}
                )
            )
    assert out["current_step"] == "cost_done"
    assert out["cost_results"]["prod"]["budget_usd"] == 500.0


def test_cost_node_skips_envs_without_tf_dir(tmp_path: Path):
    with patch.object(cost_node_mod, "run_infracost") as runner:
        with _emitter_ctx():
            out = asyncio.run(
                cost_node_mod.cost_node(
                    {"project_path": str(tmp_path), "target_environments": ["prod"]}
                )
            )
    runner.assert_not_called()
    assert out["cost_results"]["prod"]["skipped"]


def test_cost_node_passes_through_infracost_skip(tmp_path: Path):
    tf_dir = tmp_path / ".aetherdeploy" / "terraform" / "prod"
    tf_dir.mkdir(parents=True)
    skipped = infracost_mod.CostReport()
    skipped.skipped_reason = "infracost binary not found on PATH"
    with patch.object(cost_node_mod, "run_infracost", return_value=skipped):
        with _emitter_ctx():
            out = asyncio.run(
                cost_node_mod.cost_node(
                    {"project_path": str(tmp_path), "target_environments": ["prod"]}
                )
            )
    assert out["current_step"] == "cost_done"
    assert out["cost_results"]["prod"]["skipped_reason"]
