"""AWS service-quotas pre-flight (MEJORAS.md §3.4).

Asks the Service Quotas API the question that catches the surprise failure
modes: do we even have headroom for what the proposal wants to create? Hits
that 4-minute terraform-apply-then-failure loop before it starts.

Only AWS today — GCP / Azure equivalents live behind their own modules and
will plug in via :func:`required_quota_codes_for_proposal` once their
catalogues exist.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


# (service_code, quota_code, friendly_name, required_value)
# Picked because they are the quotas that produce cryptic ``LimitExceeded``
# errors mid-apply when exhausted. ``required_value`` is the count the deploy
# expects to use; the check fails when the account quota is below it.
@dataclass
class QuotaCheck:
    service_code: str           # "lambda" / "ec2" / "rds" / "ecs"
    quota_code: str             # "L-..."
    name: str                   # human label
    required: float             # the count the proposal will consume
    available: float | None = None
    status: str = "unknown"     # "ok" | "exhausted" | "skipped" | "error"
    detail: str | None = None

    def to_dict(self) -> dict:
        return {
            "service_code": self.service_code,
            "quota_code": self.quota_code,
            "name": self.name,
            "required": self.required,
            "available": self.available,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class QuotaReport:
    checks: list[QuotaCheck] = field(default_factory=list)
    skipped_reason: str | None = None

    @property
    def has_exhausted(self) -> bool:
        return any(c.status == "exhausted" for c in self.checks)


# Mapping ``ServiceRecommendation.terraform_resource`` → list of quota tuples
# we want to verify. Sourced from the AWS Service Quotas console: each
# ``L-…`` is stable across regions even when the value differs.
#
#   - Lambda concurrent executions: L-B99A9384, default 1000
#   - EC2 vCPUs on-demand standard: L-1216C47A, default 5 (new accounts!)
#   - RDS DB instances: L-7B6409FD, default 40
#   - ECS Fargate vCPU on-demand: L-3032A538, default 6 per region
_QUOTA_MAP: dict[str, tuple[tuple[str, str, str, float], ...]] = {
    "aws_lambda_function": (
        ("lambda", "L-B99A9384", "Lambda concurrent executions", 10.0),
    ),
    "aws_ecs_cluster": (
        ("ecs", "L-3032A538", "Fargate vCPU on-demand", 4.0),
    ),
    "aws_ecs_service": (
        ("ecs", "L-3032A538", "Fargate vCPU on-demand", 4.0),
    ),
    "aws_db_instance": (
        ("rds", "L-7B6409FD", "RDS DB instances", 1.0),
    ),
    "aws_rds_cluster": (
        ("rds", "L-7B6409FD", "RDS DB instances", 1.0),
    ),
    "aws_instance": (
        ("ec2", "L-1216C47A", "EC2 vCPUs on-demand standard", 4.0),
    ),
}


def required_quota_codes_for_proposal(proposal: Any) -> list[QuotaCheck]:
    """Builds an unverified list of :class:`QuotaCheck` from the proposal's services."""
    out: dict[tuple[str, str], QuotaCheck] = {}
    services = getattr(proposal, "services", None) or []
    for svc in services:
        resource = getattr(svc, "terraform_resource", "") or ""
        for service_code, quota_code, name, required in _QUOTA_MAP.get(resource, ()):
            key = (service_code, quota_code)
            if key in out:
                # multiple services share the same quota → sum the requirements
                out[key].required += required
            else:
                out[key] = QuotaCheck(
                    service_code=service_code,
                    quota_code=quota_code,
                    name=name,
                    required=required,
                )
    return list(out.values())


def _service_quotas_client(region: str) -> Any:
    try:
        import boto3  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("boto3 is required (install `aetherdeploy[aws]`).") from exc
    return boto3.client("service-quotas", region_name=region)


def check_aws_quotas(
    checks: Iterable[QuotaCheck],
    region: str,
    boto3_client: Any | None = None,
) -> QuotaReport:
    """Runs ``get_service_quota`` for every check and fills in availability.

    A check that the API does not know about (rare — usually a typo in the
    quota_code map) becomes ``status="error"`` so the operator notices, but it
    does not block the deploy on its own.
    """
    checks = list(checks)
    report = QuotaReport(checks=checks)
    if not checks:
        return report

    try:
        client = boto3_client or _service_quotas_client(region)
    except RuntimeError as exc:
        report.skipped_reason = str(exc)
        for c in checks:
            c.status = "skipped"
            c.detail = report.skipped_reason
        return report

    for c in checks:
        try:
            response = client.get_service_quota(
                ServiceCode=c.service_code, QuotaCode=c.quota_code
            )
            quota_value = float((response.get("Quota") or {}).get("Value") or 0.0)
            c.available = quota_value
            c.status = "ok" if quota_value >= c.required else "exhausted"
            if c.status == "exhausted":
                c.detail = (
                    f"requested {c.required} but account limit is {quota_value}. "
                    "Request an increase in the AWS Service Quotas console."
                )
        except Exception as exc:  # noqa: BLE001
            c.status = "error"
            c.detail = str(exc)

    return report
