from __future__ import annotations

import pytest

from aetherdeploy.sdk import AetherDeploy
from aetherdeploy.models import ArchitectureProposal


@pytest.mark.asyncio
async def test_sdk_analyze_returns_project_analysis(sample_node_project):
    agent = AetherDeploy(provider="aws")
    analysis = await agent.analyze(str(sample_node_project))
    assert analysis.primary_language in ("Node.js", "TypeScript")
    assert "Express" in analysis.frameworks


@pytest.mark.asyncio
async def test_sdk_deploy_dry_run(sample_node_project):
    agent = AetherDeploy(provider="aws")
    result = await agent.deploy(
        project=str(sample_node_project),
        instruction="despliega en producción",
        environments=["prod"],
        dry_run=True,
    )
    assert result.success is True
    assert "(dry-run)" in result.endpoints


@pytest.mark.asyncio
async def test_sdk_deploy_calls_on_proposal_callback(sample_python_project):
    """El callback on_proposal debe ser llamado con la propuesta generada."""
    received_proposals: list[ArchitectureProposal] = []

    async def capture_proposal(proposal: ArchitectureProposal) -> bool:
        received_proposals.append(proposal)
        return True  # aprobar

    agent = AetherDeploy(provider="aws")
    result = await agent.deploy(
        project=str(sample_python_project),
        instruction="despliega",
        environments=["prod"],
        dry_run=True,
        on_proposal=capture_proposal,
    )

    assert len(received_proposals) == 1
    assert received_proposals[0].provider == "aws"
    assert result.success is True


@pytest.mark.asyncio
async def test_sdk_deploy_auto_approves_without_callback(sample_node_project):
    """Sin callback, el deploy aprueba automáticamente."""
    agent = AetherDeploy(provider="aws")
    result = await agent.deploy(
        project=str(sample_node_project),
        instruction="despliega",
        environments=["prod"],
        dry_run=True,
        on_proposal=None,
    )
    assert result.success is True


def test_observability_noop_tracer():
    from aetherdeploy.observability import get_tracer
    tracer = get_tracer()
    # No lanza excepciones aunque OTel no esté configurado
    with tracer.start_as_current_span("test") as span:
        span.set_attribute("key", "value")
