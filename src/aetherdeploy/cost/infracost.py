"""Infracost wrapper for live Terraform cost estimation (MEJORAS.md §3.1).

Replaces the static ``~$35-85/mes`` hints from ``providers/*/services.py`` with
real per-resource monthly cost from the Infracost CLI. The wrapper is
intentionally lenient: a missing binary produces a soft-skipped report so the
deploy still runs in environments where Infracost is not installed.

The Infracost JSON schema (v0.2) groups resources under ``projects[].breakdown``;
this module flattens it into a uniform :class:`CostReport` keyed by resource
name so callers can compare against a budget or render a top-N breakdown.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ResourceCost:
    name: str                          # e.g. aws_lambda_function.app
    resource_type: str
    monthly_cost: float                # USD
    monthly_quantity: float | None = None
    unit: str | None = None
    hourly_cost: float | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "resource_type": self.resource_type,
            "monthly_cost": round(self.monthly_cost, 2),
            "monthly_quantity": self.monthly_quantity,
            "unit": self.unit,
            "hourly_cost": self.hourly_cost,
        }


@dataclass
class CostBreakdown:
    resources: list[ResourceCost] = field(default_factory=list)
    total_monthly_cost: float = 0.0
    total_hourly_cost: float = 0.0
    currency: str = "USD"

    def top_resources(self, n: int = 5) -> list[ResourceCost]:
        return sorted(self.resources, key=lambda r: r.monthly_cost, reverse=True)[:n]


@dataclass
class CostReport:
    breakdown: CostBreakdown = field(default_factory=CostBreakdown)
    ok: bool = True                    # set to False on infracost failure / parse error
    skipped_reason: str | None = None
    raw_errors: list[str] = field(default_factory=list)

    @property
    def total_monthly_cost(self) -> float:
        return self.breakdown.total_monthly_cost

    def summary(self) -> str:
        if self.skipped_reason:
            return f"Infracost skipped: {self.skipped_reason}"
        if not self.ok:
            return f"Infracost failed: {'; '.join(self.raw_errors) or 'unknown error'}"
        top = self.breakdown.top_resources(5)
        if not top:
            return f"Infracost: ${self.total_monthly_cost:.2f}/mo (no priced resources)"
        lines = [f"Infracost: ${self.total_monthly_cost:.2f}/mo total. Top 5:"]
        for res in top:
            lines.append(f"  - {res.name} ({res.resource_type}): ${res.monthly_cost:.2f}/mo")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "skipped_reason": self.skipped_reason,
            "raw_errors": list(self.raw_errors),
            "total_monthly_cost": round(self.total_monthly_cost, 2),
            "currency": self.breakdown.currency,
            "resources": [r.to_dict() for r in self.breakdown.resources],
        }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _binary_available(name: str = "infracost") -> bool:
    return shutil.which(name) is not None


def _flatten_resources(node: dict, prefix: str = "") -> list[ResourceCost]:
    """Walks an Infracost breakdown subtree collecting priced resources.

    Modules nest under ``modules[].resources``; this recursion ignores the
    container nodes and emits a leaf per actual resource.
    """
    out: list[ResourceCost] = []
    for resource in node.get("resources", []) or []:
        name = resource.get("name") or "unknown"
        try:
            monthly = float(resource.get("monthlyCost") or 0.0)
        except (TypeError, ValueError):
            monthly = 0.0
        try:
            hourly = float(resource.get("hourlyCost") or 0.0)
        except (TypeError, ValueError):
            hourly = 0.0
        rtype = name.split(".")[0] if "." in name else (resource.get("resourceType") or "?")
        out.append(
            ResourceCost(
                name=name,
                resource_type=rtype,
                monthly_cost=monthly,
                hourly_cost=hourly,
            )
        )
    for sub in node.get("subresources", []) or []:
        out.extend(_flatten_resources(sub, prefix=prefix))
    for module in node.get("modules", []) or []:
        out.extend(_flatten_resources(module, prefix=prefix))
    return out


def _parse_infracost_json(payload: Any) -> CostBreakdown:
    breakdown = CostBreakdown()
    if not isinstance(payload, dict):
        return breakdown

    currency = payload.get("currency") or "USD"
    breakdown.currency = currency

    try:
        total_monthly = float(payload.get("totalMonthlyCost") or 0.0)
    except (TypeError, ValueError):
        total_monthly = 0.0
    try:
        total_hourly = float(payload.get("totalHourlyCost") or 0.0)
    except (TypeError, ValueError):
        total_hourly = 0.0

    resources: list[ResourceCost] = []
    for project in payload.get("projects", []) or []:
        project_break = project.get("breakdown") or {}
        resources.extend(_flatten_resources(project_break))

    breakdown.resources = resources
    # Prefer the SDK-provided total; fall back to summing resources when zero.
    if total_monthly:
        breakdown.total_monthly_cost = total_monthly
    else:
        breakdown.total_monthly_cost = sum(r.monthly_cost for r in resources)
    if total_hourly:
        breakdown.total_hourly_cost = total_hourly
    else:
        breakdown.total_hourly_cost = sum((r.hourly_cost or 0.0) for r in resources)
    return breakdown


def run_infracost(
    tf_dir: Path,
    timeout_s: int = 90,
    extra_args: list[str] | None = None,
) -> CostReport:
    """Runs ``infracost breakdown --path tf_dir --format json`` and parses the result.

    Network access is required for the pricing API; Infracost handles its own
    auth via ``INFRACOST_API_KEY``. Failure to authenticate surfaces as a soft
    error so the deploy can still proceed (operator-visible warning).
    """
    report = CostReport()
    if not _binary_available("infracost"):
        report.skipped_reason = "infracost binary not found on PATH"
        return report

    cmd = ["infracost", "breakdown", "--path", str(tf_dir), "--format", "json"]
    if extra_args:
        cmd.extend(extra_args)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        report.ok = False
        report.raw_errors.append(f"infracost timed out after {timeout_s}s")
        return report
    except FileNotFoundError:
        report.skipped_reason = "infracost binary not found on PATH"
        return report

    if proc.returncode != 0:
        report.ok = False
        report.raw_errors.append(proc.stderr.strip() or proc.stdout.strip() or "infracost exited non-zero")
        return report

    if not proc.stdout.strip():
        report.ok = False
        report.raw_errors.append("infracost produced empty output")
        return report

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        report.ok = False
        report.raw_errors.append(f"infracost: malformed JSON ({exc})")
        return report

    report.breakdown = _parse_infracost_json(payload)
    return report
