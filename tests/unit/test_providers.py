from __future__ import annotations

import pytest

from aetherdeploy.models import ProjectAnalysis
from aetherdeploy.providers import get_provider
from aetherdeploy.providers.aws.provider import AWSProvider
from aetherdeploy.providers.gcp.provider import GCPProvider
from aetherdeploy.providers.azure.provider import AzureProvider


def _node_analysis(**kwargs) -> ProjectAnalysis:
    defaults = dict(
        primary_language="Node.js",
        languages=["Node.js"],
        frameworks=["Express"],
        architecture="monolith",
        infrastructure_hints=["api-only"],
        exposed_ports=[3000],
        has_dockerfile=True,
    )
    defaults.update(kwargs)
    return ProjectAnalysis(**defaults)


# ---------------------------------------------------------------------------
# get_provider
# ---------------------------------------------------------------------------

def test_get_provider_aws():
    p = get_provider("aws")
    assert isinstance(p, AWSProvider)

def test_get_provider_gcp():
    p = get_provider("gcp")
    assert isinstance(p, GCPProvider)

def test_get_provider_azure():
    p = get_provider("azure")
    assert isinstance(p, AzureProvider)

def test_get_provider_unknown():
    with pytest.raises(ValueError, match="desconocido"):
        get_provider("digitalocean")


# ---------------------------------------------------------------------------
# AWSProvider
# ---------------------------------------------------------------------------

def test_aws_api_only_selects_ecs():
    provider = AWSProvider()
    proposal = provider.recommend_architecture(_node_analysis(), ["prod"])
    compute = next(s for s in proposal.services if s.purpose == "compute")
    assert "ECS Fargate" in compute.service_name
    assert compute.terraform_resource == "aws_ecs_cluster"

def test_aws_proposal_always_has_networking():
    provider = AWSProvider()
    proposal = provider.recommend_architecture(_node_analysis(), ["prod"])
    purposes = {s.purpose for s in proposal.services}
    assert "networking" in purposes

def test_aws_adds_database_when_hinted():
    provider = AWSProvider()
    analysis = _node_analysis(infrastructure_hints=["api-only", "has-database"])
    proposal = provider.recommend_architecture(analysis, ["prod"])
    purposes = {s.purpose for s in proposal.services}
    assert "database" in purposes

def test_aws_adds_cache_when_hinted():
    provider = AWSProvider()
    analysis = _node_analysis(infrastructure_hints=["api-only", "has-cache"])
    proposal = provider.recommend_architecture(analysis, ["prod"])
    purposes = {s.purpose for s in proposal.services}
    assert "cache" in purposes

def test_aws_environments_mapped_correctly():
    provider = AWSProvider()
    proposal = provider.recommend_architecture(_node_analysis(), ["local", "feature", "prod"])
    assert proposal.environments["local"].strategy == "docker"
    assert proposal.environments["feature"].strategy == "ephemeral"
    assert proposal.environments["prod"].strategy == "terraform"

def test_aws_backend_config():
    provider = AWSProvider()
    cfg = provider.get_terraform_backend_config("prod", "myapp")
    assert cfg["backend"] == "s3"
    assert "myapp" in cfg["bucket"]
    assert cfg["encrypt"] is True

def test_aws_microservices_selects_eks():
    provider = AWSProvider()
    analysis = _node_analysis(
        architecture="microservices",
        infrastructure_hints=["multi-service"],
    )
    proposal = provider.recommend_architecture(analysis, ["prod"])
    compute = next(s for s in proposal.services if s.purpose == "compute")
    assert "EKS" in compute.service_name


# ---------------------------------------------------------------------------
# TerraformGenerator
# ---------------------------------------------------------------------------

def test_terraform_generator_produces_main_tf():
    from aetherdeploy.terraform.generator import TerraformGenerator
    provider = AWSProvider()
    proposal = provider.recommend_architecture(_node_analysis(), ["prod"])

    gen = TerraformGenerator()
    configs = gen.generate(proposal, "myapp", "prod")

    assert "main.tf" in configs
    assert "terraform {" in configs["main.tf"]
    assert "provider" in configs["main.tf"]

def test_terraform_generator_includes_ecs_for_api():
    from aetherdeploy.terraform.generator import TerraformGenerator
    provider = AWSProvider()
    proposal = provider.recommend_architecture(_node_analysis(), ["prod"])

    gen = TerraformGenerator()
    configs = gen.generate(proposal, "myapp", "prod")
    assert "aws_ecs_cluster" in configs["main.tf"]

def test_terraform_generator_unknown_provider_raises():
    from aetherdeploy.terraform.generator import TerraformGenerator
    from aetherdeploy.models import ArchitectureProposal
    proposal = ArchitectureProposal(provider="digitalocean", region="nyc1")

    gen = TerraformGenerator()
    with pytest.raises(ValueError, match="template"):
        gen.generate(proposal, "myapp", "prod")

def test_terraform_generator_write(tmp_path):
    from aetherdeploy.terraform.generator import TerraformGenerator
    provider = AWSProvider()
    proposal = provider.recommend_architecture(_node_analysis(), ["prod"])

    gen = TerraformGenerator()
    configs = gen.generate(proposal, "myapp", "prod")
    gen.write(configs, tmp_path)

    assert (tmp_path / "main.tf").exists()
    content = (tmp_path / "main.tf").read_text()
    assert "AetherDeploy" in content
