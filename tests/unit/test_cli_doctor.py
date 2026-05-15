"""Tests for cli_doctor — the pre-flight environment check."""
from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from aetherdeploy import cli_doctor
from aetherdeploy.cli_doctor import (
    CheckResult,
    DoctorReport,
    _check_cloud_credentials,
    _check_docker,
    _check_terraform,
)


def test_check_terraform_missing_binary():
    with patch.object(cli_doctor, "TerraformRunner") as runner_cls:
        runner_cls.return_value.is_available.return_value = False
        runner_cls.return_value._bin = "terraform"
        result = _check_terraform()
    assert result.status == "fail"
    assert "not found" in result.detail


def test_check_terraform_present_returns_ok():
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="Terraform v1.7.0\n{}", stderr="")
    with patch.object(cli_doctor, "TerraformRunner") as runner_cls, \
         patch.object(cli_doctor.subprocess, "run", return_value=completed):
        runner_cls.return_value.is_available.return_value = True
        runner_cls.return_value._bin = "terraform"
        result = _check_terraform()
    assert result.status == "ok"
    assert "Terraform" in result.detail


def test_check_docker_no_binary():
    with patch.object(cli_doctor.shutil, "which", return_value=None):
        result = _check_docker()
    assert result.status == "fail"
    assert "docker binary" in result.detail


def test_check_docker_daemon_unreachable():
    completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="Cannot connect to the Docker daemon\n")
    with patch.object(cli_doctor.shutil, "which", return_value="/usr/bin/docker"), \
         patch.object(cli_doctor.subprocess, "run", return_value=completed):
        result = _check_docker()
    assert result.status == "fail"
    assert "daemon" in result.detail.lower() or "docker" in result.detail.lower()


def test_check_docker_ok():
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="24.0.7\n", stderr="")
    with patch.object(cli_doctor.shutil, "which", return_value="/usr/bin/docker"), \
         patch.object(cli_doctor.subprocess, "run", return_value=completed):
        result = _check_docker()
    assert result.status == "ok"
    assert "24.0.7" in result.detail


def test_check_cloud_credentials_none(monkeypatch):
    for var in ("AWS_ACCESS_KEY_ID", "AWS_PROFILE", "GOOGLE_APPLICATION_CREDENTIALS",
                "AZURE_CLIENT_ID", "AZURE_SUBSCRIPTION_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cli_doctor.os.path, "exists", lambda _p: False)
    result = _check_cloud_credentials()
    assert result.status == "warn"


def test_check_cloud_credentials_aws_present(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA-XXX")
    for var in ("GOOGLE_APPLICATION_CREDENTIALS", "AZURE_CLIENT_ID", "AZURE_SUBSCRIPTION_ID"):
        monkeypatch.delenv(var, raising=False)
    result = _check_cloud_credentials()
    assert result.status == "ok"
    assert "aws" in result.detail


def test_doctor_report_exit_code_fail():
    report = DoctorReport(checks=[
        CheckResult("a", "ok"),
        CheckResult("b", "fail", "down"),
    ])
    assert report.exit_code == 1
    assert report.has_warnings is False


def test_doctor_report_exit_code_ok_with_warnings():
    report = DoctorReport(checks=[
        CheckResult("a", "ok"),
        CheckResult("b", "warn", "missing optional"),
    ])
    assert report.exit_code == 0
    assert report.has_warnings is True


@pytest.mark.asyncio
async def test_run_checks_async_returns_all_four():
    """Smoke: full pipeline produces 4 results without raising."""
    with patch.object(cli_doctor, "_check_terraform", return_value=CheckResult("Terraform CLI", "ok")), \
         patch.object(cli_doctor, "_check_docker", return_value=CheckResult("Docker daemon", "ok")), \
         patch.object(cli_doctor, "_check_cloud_credentials", return_value=CheckResult("Cloud credentials", "warn")):
        async def _stub_llm():
            return CheckResult("LLM backend (ollama)", "ok")
        with patch.object(cli_doctor, "_check_llm_async", _stub_llm):
            report = await cli_doctor.run_checks_async()
    assert len(report.checks) == 4
    assert {c.name for c in report.checks} == {
        "Terraform CLI", "Docker daemon", "LLM backend (ollama)", "Cloud credentials"
    }
