"""Aggregate pre-flight runner (MEJORAS.md §3.4)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .iam import IamReport, iam_actions_for_proposal, simulate_aws_iam
from .quotas import QuotaReport, check_aws_quotas, required_quota_codes_for_proposal


@dataclass
class PreflightResult:
    blocked: bool = False
    blockers: list[str] = field(default_factory=list)
    quotas: QuotaReport = field(default_factory=QuotaReport)
    iam: IamReport = field(default_factory=IamReport)

    def summary(self) -> str:
        parts: list[str] = []
        if self.quotas.skipped_reason:
            parts.append(f"quotas: skipped ({self.quotas.skipped_reason})")
        else:
            ok = sum(1 for c in self.quotas.checks if c.status == "ok")
            ex = sum(1 for c in self.quotas.checks if c.status == "exhausted")
            err = sum(1 for c in self.quotas.checks if c.status == "error")
            parts.append(f"quotas: {ok} ok, {ex} exhausted, {err} error")

        if self.iam.skipped_reason:
            parts.append(f"iam: skipped ({self.iam.skipped_reason})")
        else:
            allowed = sum(1 for c in self.iam.checks if c.decision == "allowed")
            denied = sum(1 for c in self.iam.checks if c.decision == "denied")
            unknown = sum(1 for c in self.iam.checks if c.decision == "unknown")
            parts.append(f"iam ({self.iam.principal_arn or '?'}): {allowed} allowed, {denied} denied, {unknown} unknown")
        return " | ".join(parts)

    def to_dict(self) -> dict:
        return {
            "blocked": self.blocked,
            "blockers": list(self.blockers),
            "quotas": {
                "skipped_reason": self.quotas.skipped_reason,
                "checks": [c.to_dict() for c in self.quotas.checks],
            },
            "iam": {
                "principal_arn": self.iam.principal_arn,
                "skipped_reason": self.iam.skipped_reason,
                "checks": [c.to_dict() for c in self.iam.checks],
            },
        }


def run_preflight(proposal: Any, region: str = "us-east-1") -> PreflightResult:
    """Runs both quota and IAM checks. Only AWS today; other providers no-op."""
    result = PreflightResult()

    provider = (getattr(proposal, "provider", "") or "").lower()
    if provider != "aws":
        result.quotas.skipped_reason = f"preflight not implemented for provider '{provider}'"
        result.iam.skipped_reason = result.quotas.skipped_reason
        return result

    # Quotas
    quota_checks = required_quota_codes_for_proposal(proposal)
    if quota_checks:
        result.quotas = check_aws_quotas(quota_checks, region=region)
        for check in result.quotas.checks:
            if check.status == "exhausted":
                result.blocked = True
                result.blockers.append(
                    f"quota '{check.name}' (service={check.service_code}): {check.detail}"
                )
    else:
        result.quotas.skipped_reason = "no quota-tracked services in proposal"

    # IAM
    actions = iam_actions_for_proposal(proposal)
    if actions:
        result.iam = simulate_aws_iam(actions, region=region)
        for check in result.iam.checks:
            if check.decision == "denied":
                result.blocked = True
                result.blockers.append(
                    f"IAM denies {check.action} for {result.iam.principal_arn or 'current principal'}"
                )
    else:
        result.iam.skipped_reason = "no IAM-tracked actions for this proposal"

    return result
