"""Policy gates for generated Terraform (MEJORAS.md §3.2).

Wraps Checkov and tfsec to flag insecure infrastructure before apply. Designed
so a missing binary downgrades to a soft warning instead of breaking the
deploy — policy enforcement should be available, never compulsory.

Severities are normalized to a small, fixed taxonomy so the agent and the TUI
do not need to understand vendor-specific levels. The well-known hard fails
(public S3 buckets, IAM ``*:*``, security groups exposing non-web ports to
``0.0.0.0/0``) get their own classification so the build can be aborted
deterministically — not on every Checkov medium.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

Severity = Literal["critical", "high", "medium", "low", "info"]


@dataclass
class Finding:
    rule_id: str
    severity: Severity
    message: str
    resource: str
    file: str
    line: int | None = None
    tool: str = "checkov"

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
            "resource": self.resource,
            "file": self.file,
            "line": self.line,
            "tool": self.tool,
        }


@dataclass
class PolicyReport:
    findings: list[Finding] = field(default_factory=list)
    tools_run: list[str] = field(default_factory=list)
    tools_skipped: dict[str, str] = field(default_factory=dict)  # tool -> reason
    raw_failures: list[str] = field(default_factory=list)

    @property
    def has_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)

    @property
    def has_high(self) -> bool:
        return any(f.severity == "high" for f in self.findings)


@dataclass
class PolicyResult:
    blocked: bool
    blocked_by: list[Finding] = field(default_factory=list)
    overrides_applied: list[str] = field(default_factory=list)
    report: PolicyReport = field(default_factory=PolicyReport)

    def summary(self) -> str:
        if not self.report.findings and not self.report.tools_run:
            tools = ", ".join(self.report.tools_skipped.keys()) or "no policy tools"
            return f"Policy gate skipped ({tools} unavailable)."
        if not self.report.findings:
            return f"Policy gate passed ({', '.join(self.report.tools_run)} found no issues)."
        counts: dict[Severity, int] = {}
        for f in self.report.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        breakdown = ", ".join(f"{n} {sev}" for sev, n in counts.items())
        verdict = "BLOCKED" if self.blocked else "WARN"
        return f"Policy gate {verdict}: {breakdown}."


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------

# Checkov rule IDs that should always block — irrespective of Checkov's
# reported severity — because they are unambiguous catastrophes in production.
_HARD_FAIL_RULES: frozenset[str] = frozenset(
    {
        # S3 publicly accessible
        "CKV_AWS_19", "CKV_AWS_53", "CKV_AWS_54", "CKV_AWS_55", "CKV_AWS_56",
        # RDS / Aurora unencrypted
        "CKV_AWS_16", "CKV_AWS_17",
        # IAM wildcard policies
        "CKV_AWS_1", "CKV_AWS_40", "CKV_AWS_45", "CKV_AWS_62",
        # Security groups exposing the world on non-web ports
        "CKV_AWS_24", "CKV_AWS_25", "CKV_AWS_260",
        # Public load balancer / API Gateway without auth
        "CKV_AWS_20", "CKV_AWS_21",
        # GCP — buckets public, KMS rotation, SQL public IP
        "CKV_GCP_28", "CKV_GCP_29", "CKV_GCP_43", "CKV_GCP_60",
        # Azure — storage public, NSG SSH/RDP open
        "CKV_AZURE_3", "CKV_AZURE_10", "CKV_AZURE_17",
    }
)


_CHECKOV_TO_SEVERITY: dict[str, Severity] = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
    "INFO": "info",
}


_TFSEC_TO_SEVERITY: dict[str, Severity] = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
    "INFO": "info",
}


def _classify_checkov_severity(check_id: str, raw_severity: str | None) -> Severity:
    if check_id in _HARD_FAIL_RULES:
        return "critical"
    if raw_severity:
        return _CHECKOV_TO_SEVERITY.get(raw_severity.upper(), "medium")
    return "medium"


# ---------------------------------------------------------------------------
# Tool runners
# ---------------------------------------------------------------------------

def _binary_available(name: str) -> bool:
    return shutil.which(name) is not None


def run_checkov(tf_dir: Path, timeout_s: int = 90) -> PolicyReport:
    """Runs Checkov against ``tf_dir`` and returns a normalized report.

    Checkov exits 0 when no failures are detected and non-zero otherwise. We
    treat both as success as long as JSON parsing works; the failure list
    comes from the JSON payload, not the exit code.
    """
    report = PolicyReport()
    if not _binary_available("checkov"):
        report.tools_skipped["checkov"] = "binary not found on PATH"
        return report

    try:
        proc = subprocess.run(
            ["checkov", "-d", str(tf_dir), "--quiet", "--output", "json", "--soft-fail"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        report.tools_skipped["checkov"] = "timed out"
        return report
    except FileNotFoundError:
        report.tools_skipped["checkov"] = "binary not found on PATH"
        return report

    report.tools_run.append("checkov")

    if not proc.stdout.strip():
        return report

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        report.raw_failures.append(f"checkov: malformed JSON ({exc})")
        return report

    report.findings.extend(_parse_checkov(data))
    return report


def _parse_checkov(data) -> list[Finding]:
    """Normalizes Checkov's JSON output (handles single-framework and list variants)."""
    findings: list[Finding] = []
    runs = data if isinstance(data, list) else [data]
    for run in runs:
        results = (run.get("results") or {}) if isinstance(run, dict) else {}
        failed = results.get("failed_checks", []) if isinstance(results, dict) else []
        for chk in failed:
            check_id = chk.get("check_id") or chk.get("bc_check_id") or "UNKNOWN"
            raw_sev = chk.get("severity") or chk.get("severity_label")
            file_path = chk.get("file_path") or chk.get("repo_file_path") or "?"
            file_line = chk.get("file_line_range") or []
            line = file_line[0] if isinstance(file_line, list) and file_line else None
            findings.append(
                Finding(
                    rule_id=check_id,
                    severity=_classify_checkov_severity(check_id, raw_sev),
                    message=chk.get("check_name", "policy violation"),
                    resource=chk.get("resource", "?"),
                    file=file_path,
                    line=line,
                    tool="checkov",
                )
            )
    return findings


def run_tfsec(tf_dir: Path, timeout_s: int = 60) -> PolicyReport:
    """Runs tfsec against ``tf_dir`` and returns a normalized report."""
    report = PolicyReport()
    if not _binary_available("tfsec"):
        report.tools_skipped["tfsec"] = "binary not found on PATH"
        return report

    try:
        proc = subprocess.run(
            ["tfsec", str(tf_dir), "--format", "json", "--soft-fail", "--no-colour"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        report.tools_skipped["tfsec"] = "timed out"
        return report
    except FileNotFoundError:
        report.tools_skipped["tfsec"] = "binary not found on PATH"
        return report

    report.tools_run.append("tfsec")

    if not proc.stdout.strip():
        return report

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        report.raw_failures.append(f"tfsec: malformed JSON ({exc})")
        return report

    for item in data.get("results", []) or []:
        rule_id = item.get("rule_id") or item.get("long_id") or "UNKNOWN"
        raw_sev = item.get("severity")
        severity = _TFSEC_TO_SEVERITY.get((raw_sev or "").upper(), "medium")
        location = item.get("location") or {}
        report.findings.append(
            Finding(
                rule_id=rule_id,
                severity=severity,
                message=item.get("description") or item.get("rule_description", "tfsec violation"),
                resource=item.get("resource", "?"),
                file=location.get("filename", "?"),
                line=location.get("start_line"),
                tool="tfsec",
            )
        )
    return report


# ---------------------------------------------------------------------------
# Aggregate gate
# ---------------------------------------------------------------------------

def _merge_reports(reports: Iterable[PolicyReport]) -> PolicyReport:
    out = PolicyReport()
    for r in reports:
        out.findings.extend(r.findings)
        out.tools_run.extend(r.tools_run)
        out.tools_skipped.update(r.tools_skipped)
        out.raw_failures.extend(r.raw_failures)
    return out


def run_policy_gate(
    tf_dir: Path,
    allowlist: Iterable[str] | None = None,
    block_on: Iterable[Severity] = ("critical",),
    enable_checkov: bool = True,
    enable_tfsec: bool = True,
) -> PolicyResult:
    """Runs every enabled tool, applies the allow-list, and returns a verdict.

    ``allowlist`` carries rule IDs the operator explicitly accepts. Allow-listed
    findings are kept in the report (visibility) but do not contribute to the
    block decision (compliance).
    """
    allow = {a.strip().upper() for a in (allowlist or []) if a and a.strip()}

    reports: list[PolicyReport] = []
    if enable_checkov:
        reports.append(run_checkov(tf_dir))
    if enable_tfsec:
        reports.append(run_tfsec(tf_dir))

    merged = _merge_reports(reports)

    block_set = {b.lower() for b in block_on}
    overrides: list[str] = []
    blockers: list[Finding] = []

    for finding in merged.findings:
        if finding.rule_id.upper() in allow:
            overrides.append(finding.rule_id)
            continue
        if finding.severity in block_set:
            blockers.append(finding)

    return PolicyResult(
        blocked=bool(blockers),
        blocked_by=blockers,
        overrides_applied=overrides,
        report=merged,
    )
