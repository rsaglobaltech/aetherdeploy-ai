from __future__ import annotations

from aetherdeploy.agent.state import (
    ArchitectureProposal,
    DeploymentResult,
    EnvironmentConfig,
    LanguageAnalysis,
    ProjectAnalysis,
    ServiceRecommendation,
)


def test_project_analysis_defaults():
    analysis = ProjectAnalysis(primary_language="python")
    assert analysis.languages == []
    assert analysis.frameworks == []
    assert analysis.architecture == "monolith"
    assert analysis.has_dockerfile is False


def test_architecture_proposal_defaults():
    proposal = ArchitectureProposal(provider="aws", region="us-east-1")
    assert proposal.services == []
    assert proposal.total_estimated_cost == "~$0/mes"
    assert proposal.environments == {}


def test_deployment_result_defaults():
    result = DeploymentResult()
    assert result.success is False
    assert result.endpoints == []
    assert result.environments == {}


def test_service_recommendation():
    svc = ServiceRecommendation(
        service_name="ECS Fargate",
        purpose="compute",
        justification="Serverless containers",
        estimated_monthly_cost="~$45",
        terraform_resource="aws_ecs_cluster",
    )
    assert svc.service_name == "ECS Fargate"
    assert svc.purpose == "compute"
