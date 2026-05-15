"""Pre-flight environment check for AetherDeploy.

Verifies the local toolchain (Terraform, Docker), LLM backend reachability, and
optional cloud provider credentials. Returns a structured report so callers can
render it or gate further commands on it.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Literal

import httpx

from .config import get_config
from .terraform.runner import TerraformRunner

Status = Literal["ok", "warn", "fail", "skip"]


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str = ""
    hint: str = ""


@dataclass
class DoctorReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return 1 if any(c.status == "fail" for c in self.checks) else 0

    @property
    def has_warnings(self) -> bool:
        return any(c.status == "warn" for c in self.checks)


def _check_terraform() -> CheckResult:
    runner = TerraformRunner()
    if not runner.is_available():
        return CheckResult(
            name="Terraform CLI",
            status="fail",
            detail=f"binary '{runner._bin}' not found or not executable",
            hint="install Terraform >= 1.5 or set AETHER_TERRAFORM_BINARY",
        )
    try:
        out = subprocess.run(
            [runner._bin, "version", "-json"],
            capture_output=True,
            timeout=5,
            text=True,
        )
        version_line = out.stdout.splitlines()[0] if out.stdout else "unknown"
    except (subprocess.TimeoutExpired, OSError):
        version_line = "unknown"
    return CheckResult(name="Terraform CLI", status="ok", detail=version_line)


def _check_docker() -> CheckResult:
    if not shutil.which("docker"):
        return CheckResult(
            name="Docker daemon",
            status="fail",
            detail="docker binary not on PATH",
            hint="install Docker Desktop / Engine, then start the daemon",
        )
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=5,
            text=True,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return CheckResult(
            name="Docker daemon",
            status="fail",
            detail=f"could not contact daemon: {exc}",
            hint="start Docker (Docker Desktop / `systemctl start docker`)",
        )
    if proc.returncode != 0:
        return CheckResult(
            name="Docker daemon",
            status="fail",
            detail=(proc.stderr or "docker info failed").strip().splitlines()[0],
            hint="start Docker (Docker Desktop / `systemctl start docker`)",
        )
    return CheckResult(name="Docker daemon", status="ok", detail=f"server v{proc.stdout.strip()}")


async def _check_llm_async() -> CheckResult:
    config = get_config()
    backend = config.llm_backend
    if backend == "anthropic":
        if not config.llm_api_key:
            return CheckResult(
                name="LLM backend (anthropic)",
                status="fail",
                detail="AETHER_LLM_API_KEY not set",
                hint="export AETHER_LLM_API_KEY=sk-ant-...",
            )
        return CheckResult(
            name="LLM backend (anthropic)",
            status="ok",
            detail=f"model={config.llm_model} (API key present, reachability not probed)",
        )
    # ollama
    url = config.llm_base_url.rstrip("/") + "/api/tags"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return CheckResult(
                name="LLM backend (ollama)",
                status="fail",
                detail=f"{url} returned HTTP {resp.status_code}",
                hint="start ollama (`ollama serve`) or set AETHER_LLM_BASE_URL",
            )
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        if config.llm_model not in models:
            return CheckResult(
                name="LLM backend (ollama)",
                status="warn",
                detail=f"reachable, but model '{config.llm_model}' not pulled",
                hint=f"`ollama pull {config.llm_model}`",
            )
        return CheckResult(
            name="LLM backend (ollama)",
            status="ok",
            detail=f"{config.llm_base_url} — model '{config.llm_model}' available",
        )
    except httpx.HTTPError as exc:
        return CheckResult(
            name="LLM backend (ollama)",
            status="fail",
            detail=f"could not reach {url}: {exc}",
            hint="start ollama (`ollama serve`) or set AETHER_LLM_BASE_URL",
        )


def _check_cloud_credentials() -> CheckResult:
    """Best-effort sniff for cloud creds. Soft warning if none found."""
    aws_ok = bool(
        os.environ.get("AWS_ACCESS_KEY_ID")
        or os.environ.get("AWS_PROFILE")
        or (os.path.expanduser("~/.aws/credentials") and os.path.exists(os.path.expanduser("~/.aws/credentials")))
    )
    gcp_ok = bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))
    azure_ok = bool(os.environ.get("AZURE_CLIENT_ID") or os.environ.get("AZURE_SUBSCRIPTION_ID"))

    found = [name for name, ok in (("aws", aws_ok), ("gcp", gcp_ok), ("azure", azure_ok)) if ok]
    if not found:
        return CheckResult(
            name="Cloud credentials",
            status="warn",
            detail="no AWS / GCP / Azure credentials detected in environment",
            hint="set AWS_PROFILE, GOOGLE_APPLICATION_CREDENTIALS or AZURE_* before deploying to prod",
        )
    return CheckResult(name="Cloud credentials", status="ok", detail=f"detected: {', '.join(found)}")


async def run_checks_async() -> DoctorReport:
    report = DoctorReport()
    report.checks.append(_check_terraform())
    report.checks.append(_check_docker())
    report.checks.append(await _check_llm_async())
    report.checks.append(_check_cloud_credentials())
    return report


def run_checks() -> DoctorReport:
    return asyncio.run(run_checks_async())
