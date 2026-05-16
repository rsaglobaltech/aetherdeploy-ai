"""AWS IAM ``simulate_principal_policy`` pre-flight (MEJORAS.md §3.4).

Asks IAM "given the current STS identity, will the actions Terraform is about
to perform succeed?" Fast failure with a clear message ("missing
lambda:CreateFunction on role X") beats a cryptic Terraform error four
minutes into apply.

The action map is intentionally conservative: each entry lists the verbs that
*creating* the resource needs. Read-only verbs (``Describe…``) are added
because Terraform refreshes state before/after apply.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass
class IamCheck:
    action: str                 # e.g. "lambda:CreateFunction"
    decision: str = "unknown"   # "allowed" | "denied" | "unknown" | "error"
    detail: str | None = None
    matched_statements: int = 0

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "decision": self.decision,
            "detail": self.detail,
            "matched_statements": self.matched_statements,
        }


@dataclass
class IamReport:
    checks: list[IamCheck] = field(default_factory=list)
    principal_arn: str | None = None
    skipped_reason: str | None = None

    @property
    def has_denied(self) -> bool:
        return any(c.decision == "denied" for c in self.checks)


_ACTION_MAP: dict[str, tuple[str, ...]] = {
    "aws_lambda_function": (
        "lambda:CreateFunction",
        "lambda:UpdateFunctionCode",
        "lambda:UpdateFunctionConfiguration",
        "lambda:GetFunction",
        "iam:PassRole",
    ),
    "aws_ecs_cluster": (
        "ecs:CreateCluster",
        "ecs:DescribeClusters",
        "ec2:DescribeSubnets",
    ),
    "aws_ecs_service": (
        "ecs:CreateService",
        "ecs:UpdateService",
        "ecs:RegisterTaskDefinition",
        "iam:PassRole",
    ),
    "aws_db_instance": (
        "rds:CreateDBInstance",
        "rds:DescribeDBInstances",
        "rds:ModifyDBInstance",
    ),
    "aws_rds_cluster": (
        "rds:CreateDBCluster",
        "rds:DescribeDBClusters",
    ),
    "aws_s3_bucket": (
        "s3:CreateBucket",
        "s3:GetBucketLocation",
        "s3:PutBucketPolicy",
    ),
    "aws_dynamodb_table": (
        "dynamodb:CreateTable",
        "dynamodb:DescribeTable",
    ),
    "aws_secretsmanager_secret": (
        "secretsmanager:CreateSecret",
        "secretsmanager:PutSecretValue",
        "secretsmanager:DescribeSecret",
    ),
    "aws_iam_role": (
        "iam:CreateRole",
        "iam:AttachRolePolicy",
        "iam:PassRole",
    ),
    "aws_ecr_repository": (
        "ecr:CreateRepository",
        "ecr:DescribeRepositories",
    ),
}


def iam_actions_for_proposal(proposal: Any) -> list[str]:
    """Returns the distinct, sorted list of IAM actions the deploy will require."""
    actions: set[str] = set()
    services = getattr(proposal, "services", None) or []
    for svc in services:
        resource = getattr(svc, "terraform_resource", "") or ""
        actions.update(_ACTION_MAP.get(resource, ()))
    return sorted(actions)


def _iam_clients(region: str) -> tuple[Any, Any]:
    try:
        import boto3  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("boto3 is required (install `aetherdeploy[aws]`).") from exc
    return boto3.client("iam", region_name=region), boto3.client("sts", region_name=region)


def simulate_aws_iam(
    actions: Iterable[str],
    region: str = "us-east-1",
    boto3_iam: Any | None = None,
    boto3_sts: Any | None = None,
) -> IamReport:
    """Runs ``iam.simulate_principal_policy`` against the current STS identity."""
    actions = list(actions)
    report = IamReport()
    if not actions:
        return report

    try:
        if boto3_iam is None or boto3_sts is None:
            iam, sts = _iam_clients(region)
            boto3_iam = boto3_iam or iam
            boto3_sts = boto3_sts or sts
    except RuntimeError as exc:
        report.skipped_reason = str(exc)
        for action in actions:
            report.checks.append(IamCheck(action=action, decision="unknown", detail=report.skipped_reason))
        return report

    try:
        identity = boto3_sts.get_caller_identity()
        principal_arn = identity.get("Arn") or ""
        report.principal_arn = principal_arn
    except Exception as exc:  # noqa: BLE001
        report.skipped_reason = f"sts:GetCallerIdentity failed: {exc}"
        for action in actions:
            report.checks.append(IamCheck(action=action, decision="unknown", detail=report.skipped_reason))
        return report

    if not principal_arn:
        report.skipped_reason = "empty STS principal ARN"
        return report

    try:
        response = boto3_iam.simulate_principal_policy(
            PolicySourceArn=principal_arn,
            ActionNames=list(actions),
        )
    except Exception as exc:  # noqa: BLE001
        report.skipped_reason = f"iam.simulate_principal_policy failed: {exc}"
        for action in actions:
            report.checks.append(IamCheck(action=action, decision="unknown", detail=report.skipped_reason))
        return report

    by_action: dict[str, IamCheck] = {}
    for entry in response.get("EvaluationResults", []) or []:
        action = entry.get("EvalActionName", "")
        decision_raw = (entry.get("EvalDecision") or "").lower()
        matched = entry.get("MatchedStatements") or []
        # IAM returns "allowed" or "explicitDeny" / "implicitDeny"
        if decision_raw == "allowed":
            decision = "allowed"
        elif "deny" in decision_raw:
            decision = "denied"
        else:
            decision = "unknown"
        by_action[action] = IamCheck(
            action=action,
            decision=decision,
            detail=None if decision == "allowed" else f"IAM decision: {entry.get('EvalDecision')}",
            matched_statements=len(matched),
        )

    for action in actions:
        report.checks.append(
            by_action.get(action, IamCheck(action=action, decision="unknown", detail="action not in IAM response"))
        )

    return report
