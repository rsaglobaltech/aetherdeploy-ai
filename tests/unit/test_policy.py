"""Tests for the policy gate module and node (MEJORAS.md §3.2)."""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from aetherdeploy.agent.context import _emitter_var
from aetherdeploy.agent.nodes import policy as policy_node_mod
from aetherdeploy.policy import checker as policy_checker


CHECKOV_PUBLIC_S3 = {
    "check_type": "terraform",
    "results": {
        "failed_checks": [
            {
                "check_id": "CKV_AWS_19",
                "check_name": "S3 bucket has versioning disabled",
                "resource": "aws_s3_bucket.app_assets",
                "file_path": "/tf/main.tf",
                "file_line_range": [10, 25],
                "severity": "HIGH",
            },
            {
                "check_id": "CKV_AWS_5000",  # not in hard-fail list
                "check_name": "Tagging convention",
                "resource": "aws_s3_bucket.app_assets",
                "file_path": "/tf/main.tf",
                "file_line_range": [10, 25],
                "severity": "LOW",
            },
        ],
    },
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
    import subprocess
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------

def test_hard_fail_rule_promotes_to_critical():
    sev = policy_checker._classify_checkov_severity("CKV_AWS_19", "HIGH")
    assert sev == "critical"


def test_non_hard_fail_uses_reported_severity():
    sev = policy_checker._classify_checkov_severity("CKV_AWS_9999", "MEDIUM")
    assert sev == "medium"


def test_missing_severity_defaults_to_medium():
    sev = policy_checker._classify_checkov_severity("CKV_AWS_9999", None)
    assert sev == "medium"


# ---------------------------------------------------------------------------
# Checkov runner
# ---------------------------------------------------------------------------

def test_run_checkov_skips_when_binary_missing(tmp_path: Path):
    with patch.object(policy_checker.shutil, "which", return_value=None):
        report = policy_checker.run_checkov(tmp_path)
    assert "checkov" in report.tools_skipped
    assert report.findings == []


def test_run_checkov_parses_failures(tmp_path: Path):
    with patch.object(policy_checker.shutil, "which", return_value="/bin/checkov"), \
         patch.object(policy_checker.subprocess, "run", return_value=_completed(0, json.dumps(CHECKOV_PUBLIC_S3))):
        report = policy_checker.run_checkov(tmp_path)

    assert "checkov" in report.tools_run
    assert len(report.findings) == 2
    critical = [f for f in report.findings if f.severity == "critical"]
    assert critical and critical[0].rule_id == "CKV_AWS_19"


def test_run_checkov_handles_empty_stdout(tmp_path: Path):
    with patch.object(policy_checker.shutil, "which", return_value="/bin/checkov"), \
         patch.object(policy_checker.subprocess, "run", return_value=_completed(0, "")):
        report = policy_checker.run_checkov(tmp_path)
    assert report.findings == []
    assert report.tools_run == ["checkov"]


def test_run_checkov_records_malformed_json(tmp_path: Path):
    with patch.object(policy_checker.shutil, "which", return_value="/bin/checkov"), \
         patch.object(policy_checker.subprocess, "run", return_value=_completed(0, "not json")):
        report = policy_checker.run_checkov(tmp_path)
    assert report.findings == []
    assert any("malformed JSON" in m for m in report.raw_failures)


# ---------------------------------------------------------------------------
# tfsec runner
# ---------------------------------------------------------------------------

TFSEC_OUTPUT = {
    "results": [
        {
            "rule_id": "aws-rds-encrypt-instance-storage-data",
            "severity": "HIGH",
            "description": "RDS instance storage not encrypted",
            "resource": "aws_db_instance.main",
            "location": {"filename": "/tf/db.tf", "start_line": 12},
        }
    ]
}


def test_run_tfsec_parses_findings(tmp_path: Path):
    with patch.object(policy_checker.shutil, "which", return_value="/bin/tfsec"), \
         patch.object(policy_checker.subprocess, "run", return_value=_completed(0, json.dumps(TFSEC_OUTPUT))):
        report = policy_checker.run_tfsec(tmp_path)
    assert "tfsec" in report.tools_run
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.tool == "tfsec"
    assert finding.severity == "high"
    assert finding.file == "/tf/db.tf"
    assert finding.line == 12


# ---------------------------------------------------------------------------
# Aggregate gate
# ---------------------------------------------------------------------------

def test_run_policy_gate_blocks_on_critical(tmp_path: Path):
    def fake_run(cmd, **kwargs):
        if cmd[0] == "checkov":
            return _completed(0, json.dumps(CHECKOV_PUBLIC_S3))
        return _completed(0, json.dumps({"results": []}))

    with patch.object(policy_checker.shutil, "which", side_effect=lambda b: f"/bin/{b}"), \
         patch.object(policy_checker.subprocess, "run", side_effect=fake_run):
        result = policy_checker.run_policy_gate(tmp_path)

    assert result.blocked
    assert any(f.rule_id == "CKV_AWS_19" for f in result.blocked_by)
    assert result.overrides_applied == []


def test_run_policy_gate_respects_allowlist(tmp_path: Path):
    def fake_run(cmd, **kwargs):
        if cmd[0] == "checkov":
            return _completed(0, json.dumps(CHECKOV_PUBLIC_S3))
        return _completed(0, json.dumps({"results": []}))

    with patch.object(policy_checker.shutil, "which", side_effect=lambda b: f"/bin/{b}"), \
         patch.object(policy_checker.subprocess, "run", side_effect=fake_run):
        result = policy_checker.run_policy_gate(tmp_path, allowlist=["CKV_AWS_19"])

    assert not result.blocked
    assert "CKV_AWS_19" in result.overrides_applied


def test_run_policy_gate_skips_when_no_tools(tmp_path: Path):
    with patch.object(policy_checker.shutil, "which", return_value=None):
        result = policy_checker.run_policy_gate(tmp_path)
    assert not result.blocked
    assert result.report.tools_skipped


# ---------------------------------------------------------------------------
# policy_node
# ---------------------------------------------------------------------------

def test_policy_node_skips_when_dry_run():
    with _emitter_ctx():
        result = asyncio.run(
            policy_node_mod.policy_node(
                {"target_environments": ["prod"], "dry_run": True}
            )
        )
    assert result["current_step"] == "policy_skipped"


def test_policy_node_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("AETHER_POLICY_DISABLED", "1")
    with _emitter_ctx():
        result = asyncio.run(
            policy_node_mod.policy_node({"target_environments": ["prod"]})
        )
    assert result["current_step"] == "policy_skipped"


def test_policy_node_skips_local_only_targets():
    with _emitter_ctx():
        result = asyncio.run(
            policy_node_mod.policy_node({"target_environments": ["local"]})
        )
    assert result["current_step"] == "policy_skipped"


def test_policy_node_runs_and_passes(tmp_path: Path):
    tf_dir = tmp_path / ".aetherdeploy" / "terraform" / "prod"
    tf_dir.mkdir(parents=True)
    (tf_dir / "main.tf").write_text("")

    clean = policy_checker.PolicyResult(
        blocked=False,
        report=policy_checker.PolicyReport(tools_run=["checkov"]),
    )
    with patch.object(policy_node_mod, "run_policy_gate", return_value=clean):
        with _emitter_ctx():
            result = asyncio.run(
                policy_node_mod.policy_node(
                    {
                        "project_path": str(tmp_path),
                        "target_environments": ["prod"],
                    }
                )
            )
    assert result["current_step"] == "policy_done"
    assert result["policy_results"]["prod"]["blocked"] is False


def test_policy_node_blocks_on_critical(tmp_path: Path):
    tf_dir = tmp_path / ".aetherdeploy" / "terraform" / "prod"
    tf_dir.mkdir(parents=True)
    (tf_dir / "main.tf").write_text("")

    blocker = policy_checker.Finding(
        rule_id="CKV_AWS_19",
        severity="critical",
        message="bucket public",
        resource="aws_s3_bucket.app",
        file=str(tf_dir / "main.tf"),
        line=4,
    )
    blocked = policy_checker.PolicyResult(
        blocked=True,
        blocked_by=[blocker],
        report=policy_checker.PolicyReport(findings=[blocker], tools_run=["checkov"]),
    )
    with patch.object(policy_node_mod, "run_policy_gate", return_value=blocked):
        with _emitter_ctx():
            result = asyncio.run(
                policy_node_mod.policy_node(
                    {
                        "project_path": str(tmp_path),
                        "target_environments": ["prod"],
                    }
                )
            )

    assert result["current_step"] == "policy_error"
    assert result["policy_results"]["prod"]["blocked"]
    assert any("Policy gate blocked" in e for e in result["errors"])


def test_policy_node_uses_env_allowlist(tmp_path: Path, monkeypatch):
    tf_dir = tmp_path / ".aetherdeploy" / "terraform" / "prod"
    tf_dir.mkdir(parents=True)

    captured = {}

    def fake_gate(tf_path, allowlist=None, **kwargs):
        captured["allowlist"] = list(allowlist or [])
        return policy_checker.PolicyResult(blocked=False, report=policy_checker.PolicyReport(tools_run=["checkov"]))

    monkeypatch.setenv("AETHER_ALLOW_POLICY", "CKV_AWS_19, CKV_AWS_25")
    with patch.object(policy_node_mod, "run_policy_gate", side_effect=fake_gate):
        with _emitter_ctx():
            asyncio.run(
                policy_node_mod.policy_node(
                    {
                        "project_path": str(tmp_path),
                        "target_environments": ["prod"],
                    }
                )
            )
    assert captured["allowlist"] == ["CKV_AWS_19", "CKV_AWS_25"]
