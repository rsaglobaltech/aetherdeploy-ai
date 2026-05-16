"""Tests for the pre-flight quota / IAM gate (MEJORAS.md §3.4)."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from aetherdeploy.agent.context import _emitter_var
from aetherdeploy.agent.nodes import preflight as preflight_node_mod
from aetherdeploy.preflight import iam, quotas, runner


@contextmanager
def _emitter_ctx():
    events: list[dict] = []
    token = _emitter_var.set(events.append)
    try:
        yield events
    finally:
        _emitter_var.reset(token)


class _StubService:
    def __init__(self, terraform_resource: str):
        self.terraform_resource = terraform_resource


class _StubProposal:
    def __init__(self, services, provider="aws", region="us-east-1"):
        self.services = services
        self.provider = provider
        self.region = region


# ---------------------------------------------------------------------------
# Quotas
# ---------------------------------------------------------------------------

def test_required_quota_codes_dedups_shared_quota():
    proposal = _StubProposal([
        _StubService("aws_ecs_cluster"),
        _StubService("aws_ecs_service"),
    ])
    checks = quotas.required_quota_codes_for_proposal(proposal)
    fargate = [c for c in checks if c.quota_code == "L-3032A538"]
    assert len(fargate) == 1
    # Shared quota → required is the sum (4 + 4 = 8)
    assert fargate[0].required == 8.0


def test_check_aws_quotas_marks_exhausted_when_below_required():
    client = MagicMock()
    client.get_service_quota.return_value = {"Quota": {"Value": 5.0}}
    check = quotas.QuotaCheck(
        service_code="lambda",
        quota_code="L-B99A9384",
        name="Lambda concurrent executions",
        required=10.0,
    )
    report = quotas.check_aws_quotas([check], region="us-east-1", boto3_client=client)
    assert report.has_exhausted
    assert report.checks[0].status == "exhausted"
    assert report.checks[0].available == 5.0


def test_check_aws_quotas_marks_ok_when_above_required():
    client = MagicMock()
    client.get_service_quota.return_value = {"Quota": {"Value": 1000.0}}
    check = quotas.QuotaCheck(
        service_code="lambda",
        quota_code="L-B99A9384",
        name="Lambda concurrent executions",
        required=10.0,
    )
    report = quotas.check_aws_quotas([check], region="us-east-1", boto3_client=client)
    assert not report.has_exhausted
    assert report.checks[0].status == "ok"


def test_check_aws_quotas_skipped_when_boto3_missing():
    check = quotas.QuotaCheck(
        service_code="lambda", quota_code="L-X", name="x", required=1.0
    )

    def raise_runtime(_region):
        raise RuntimeError("boto3 missing")

    with patch.object(quotas, "_service_quotas_client", side_effect=raise_runtime):
        report = quotas.check_aws_quotas([check], region="us-east-1")
    assert report.skipped_reason
    assert report.checks[0].status == "skipped"


# ---------------------------------------------------------------------------
# IAM
# ---------------------------------------------------------------------------

def test_iam_actions_for_proposal_unions_resources():
    proposal = _StubProposal([
        _StubService("aws_lambda_function"),
        _StubService("aws_db_instance"),
    ])
    actions = iam.iam_actions_for_proposal(proposal)
    assert "lambda:CreateFunction" in actions
    assert "rds:CreateDBInstance" in actions
    # Deduped + sorted
    assert actions == sorted(set(actions))


def test_simulate_aws_iam_marks_denied():
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Arn": "arn:aws:iam::1:user/dev"}
    iam_client = MagicMock()
    iam_client.simulate_principal_policy.return_value = {
        "EvaluationResults": [
            {"EvalActionName": "lambda:CreateFunction", "EvalDecision": "allowed", "MatchedStatements": [{}]},
            {"EvalActionName": "iam:PassRole", "EvalDecision": "explicitDeny"},
        ]
    }
    report = iam.simulate_aws_iam(
        ["lambda:CreateFunction", "iam:PassRole"],
        boto3_iam=iam_client,
        boto3_sts=sts,
    )
    assert report.principal_arn == "arn:aws:iam::1:user/dev"
    assert report.has_denied
    by_action = {c.action: c for c in report.checks}
    assert by_action["lambda:CreateFunction"].decision == "allowed"
    assert by_action["iam:PassRole"].decision == "denied"


def test_simulate_aws_iam_skipped_when_sts_fails():
    sts = MagicMock()
    sts.get_caller_identity.side_effect = Exception("AccessDenied")
    iam_client = MagicMock()
    report = iam.simulate_aws_iam(
        ["lambda:CreateFunction"],
        boto3_iam=iam_client,
        boto3_sts=sts,
    )
    assert report.skipped_reason
    iam_client.simulate_principal_policy.assert_not_called()


def test_simulate_aws_iam_empty_actions_is_noop():
    report = iam.simulate_aws_iam([])
    assert report.checks == []
    assert report.skipped_reason is None


# ---------------------------------------------------------------------------
# Aggregate runner
# ---------------------------------------------------------------------------

def test_run_preflight_blocks_on_exhausted_quota():
    proposal = _StubProposal([_StubService("aws_lambda_function")])
    fake_quota = quotas.QuotaReport(
        checks=[
            quotas.QuotaCheck(
                service_code="lambda",
                quota_code="L-B99A9384",
                name="Lambda concurrent executions",
                required=10.0,
                available=5.0,
                status="exhausted",
                detail="requested 10 but limit is 5",
            )
        ]
    )
    fake_iam = iam.IamReport(
        principal_arn="arn:aws:iam::1:user/x",
        checks=[iam.IamCheck(action="lambda:CreateFunction", decision="allowed")],
    )
    with patch.object(runner, "check_aws_quotas", return_value=fake_quota), \
         patch.object(runner, "simulate_aws_iam", return_value=fake_iam):
        result = runner.run_preflight(proposal)
    assert result.blocked
    assert any("quota" in b.lower() for b in result.blockers)


def test_run_preflight_blocks_on_denied_iam():
    proposal = _StubProposal([_StubService("aws_lambda_function")])
    fake_quota = quotas.QuotaReport(
        checks=[
            quotas.QuotaCheck(
                service_code="lambda",
                quota_code="L-B99A9384",
                name="Lambda concurrent executions",
                required=10.0,
                available=1000.0,
                status="ok",
            )
        ]
    )
    fake_iam = iam.IamReport(
        principal_arn="arn:aws:iam::1:user/x",
        checks=[iam.IamCheck(action="lambda:CreateFunction", decision="denied", detail="explicitDeny")],
    )
    with patch.object(runner, "check_aws_quotas", return_value=fake_quota), \
         patch.object(runner, "simulate_aws_iam", return_value=fake_iam):
        result = runner.run_preflight(proposal)
    assert result.blocked
    assert any("IAM denies" in b for b in result.blockers)


def test_run_preflight_noop_for_non_aws_provider():
    proposal = _StubProposal([_StubService("aws_lambda_function")], provider="gcp")
    result = runner.run_preflight(proposal)
    assert not result.blocked
    assert result.quotas.skipped_reason
    assert result.iam.skipped_reason


# ---------------------------------------------------------------------------
# preflight_node
# ---------------------------------------------------------------------------

def test_preflight_node_skips_dry_run():
    with _emitter_ctx():
        out = asyncio.run(
            preflight_node_mod.preflight_node(
                {"dry_run": True, "target_environments": ["prod"]}
            )
        )
    assert out["current_step"] == "preflight_skipped"


def test_preflight_node_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("AETHER_PREFLIGHT_DISABLED", "1")
    with _emitter_ctx():
        out = asyncio.run(
            preflight_node_mod.preflight_node({"target_environments": ["prod"]})
        )
    assert out["current_step"] == "preflight_skipped"


def test_preflight_node_skips_feature_and_local():
    with _emitter_ctx():
        out = asyncio.run(
            preflight_node_mod.preflight_node(
                {"target_environments": ["local", "feature"], "requested_action": "deploy"}
            )
        )
    assert out["current_step"] == "preflight_skipped"


def test_preflight_node_runs_and_blocks_on_failure():
    proposal = _StubProposal([_StubService("aws_lambda_function")])
    blocked = runner.PreflightResult(
        blocked=True,
        blockers=["IAM denies lambda:CreateFunction"],
        quotas=quotas.QuotaReport(skipped_reason="ok"),
        iam=iam.IamReport(
            principal_arn="arn:aws:iam::1:user/x",
            checks=[iam.IamCheck(action="lambda:CreateFunction", decision="denied")],
        ),
    )
    with patch.object(preflight_node_mod, "run_preflight", return_value=blocked):
        with _emitter_ctx():
            out = asyncio.run(
                preflight_node_mod.preflight_node(
                    {
                        "target_environments": ["prod"],
                        "requested_action": "deploy",
                        "architecture_proposal": proposal,
                    }
                )
            )
    assert out["current_step"] == "preflight_error"
    assert any("Pre-flight blocked" in e for e in out["errors"])


def test_preflight_node_passes_when_no_blocks():
    proposal = _StubProposal([_StubService("aws_lambda_function")])
    ok = runner.PreflightResult()
    ok.quotas.skipped_reason = "no quotas"
    ok.iam.skipped_reason = "no actions"
    with patch.object(preflight_node_mod, "run_preflight", return_value=ok):
        with _emitter_ctx():
            out = asyncio.run(
                preflight_node_mod.preflight_node(
                    {
                        "target_environments": ["prod"],
                        "requested_action": "deploy",
                        "architecture_proposal": proposal,
                    }
                )
            )
    assert out["current_step"] == "preflight_done"
    assert out["preflight_results"]["prod"]["blocked"] is False
