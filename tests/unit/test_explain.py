"""Tests for the Decision graph + explain renderer (MEJORAS.md §17.1)."""
from __future__ import annotations

from aetherdeploy.explain import render_markdown, render_text
from aetherdeploy.models import (
    ArchitectureProposal,
    Decision,
    ProposalMetadata,
    ServiceRecommendation,
)


def _proposal_with_decisions() -> ArchitectureProposal:
    return ArchitectureProposal(
        provider="aws",
        region="us-east-1",
        services=[
            ServiceRecommendation(
                service_name="Lambda",
                purpose="compute",
                justification="serverless API",
                estimated_monthly_cost="~$10",
                terraform_resource="aws_lambda_function",
            ),
        ],
        total_estimated_cost="~$35/month",
        metadata=ProposalMetadata(
            profile="small",
            confidence=0.82,
            deploy_time_estimate="3-5 min",
            cost_low_usd=20,
            cost_high_usd=60,
        ),
        decisions=[
            Decision(
                purpose="compute",
                chosen_service="Lambda",
                alternatives_considered=[
                    ("ECS Fargate", 0.85, "more expensive than the chosen option"),
                    ("App Runner", 0.72, "valid candidate, not selected by optimizer"),
                ],
                signals_used=["python", "small", "rps_low"],
                constraints_applied=["gdpr-eu"],
                confidence=0.88,
            ),
        ],
    )


def test_render_markdown_contains_header_and_decision():
    md = render_markdown(_proposal_with_decisions())
    assert "Architecture decision record" in md
    assert "aws/us-east-1" in md
    assert "compute → **Lambda**" in md
    assert "ECS Fargate" in md
    assert "gdpr-eu" in md


def test_render_markdown_with_no_decisions_is_friendly():
    proposal = ArchitectureProposal(provider="aws", region="us-east-1")
    md = render_markdown(proposal)
    assert "No decision metadata" in md


def test_render_text_compact():
    txt = render_text(_proposal_with_decisions())
    assert "Proposal — aws/us-east-1" in txt
    assert "compute = Lambda" in txt
    assert "alt ECS Fargate" in txt


def test_decision_dataclass_defaults():
    d = Decision(purpose="db", chosen_service="DynamoDB")
    assert d.alternatives_considered == []
    assert d.signals_used == []
    assert d.confidence == 1.0


def test_architecture_proposal_has_decisions_field():
    p = ArchitectureProposal(provider="aws", region="us-east-1")
    assert p.decisions == []
    p.decisions.append(Decision(purpose="x", chosen_service="y"))
    assert len(p.decisions) == 1


def test_build_decisions_populates_for_aws_with_alternatives():
    """The proposal node's _build_decisions should produce Decision entries
    for an AWS proposal when SERVICE_ALTERNATIVES has data for the purpose."""
    from aetherdeploy.agent.nodes.proposal import _build_decisions
    from aetherdeploy.models import ProjectAnalysis

    analysis = ProjectAnalysis(
        primary_language="python",
        frameworks=["fastapi"],
        architecture="monolith",
        infrastructure_hints=["small", "rps_low"],
    )
    proposal = ArchitectureProposal(
        provider="aws",
        region="us-east-1",
        services=[
            ServiceRecommendation(
                service_name="Lambda",
                purpose="compute",
                justification="",
                estimated_monthly_cost="~$10",
                terraform_resource="aws_lambda_function",
            ),
        ],
    )
    decisions = _build_decisions(proposal, analysis, "aws")
    assert len(decisions) == 1
    assert decisions[0].purpose == "compute"
    assert decisions[0].chosen_service == "Lambda"


def test_build_decisions_minimal_for_unknown_provider():
    from aetherdeploy.agent.nodes.proposal import _build_decisions
    from aetherdeploy.models import ProjectAnalysis

    analysis = ProjectAnalysis(primary_language="python", infrastructure_hints=["small"])
    proposal = ArchitectureProposal(
        provider="gcp",
        region="us-central1",
        services=[
            ServiceRecommendation(
                service_name="Cloud Run",
                purpose="compute",
                justification="",
                estimated_monthly_cost="~$5",
                terraform_resource="google_cloud_run_service",
            ),
        ],
    )
    decisions = _build_decisions(proposal, analysis, "gcp")
    assert len(decisions) == 1
    assert decisions[0].chosen_service == "Cloud Run"
    assert decisions[0].alternatives_considered == []
