from __future__ import annotations

import pytest

from aetherdeploy.agent.graph import build_graph
from aetherdeploy.agent.state import AetherState


def _initial_state(project_path: str, dry_run: bool = True) -> AetherState:
    return {
        "user_message": "despliega mi app",
        "project_path": project_path,
        "github_url": None,
        "target_environments": ["prod"],
        "preferred_provider": "aws",
        "dry_run": dry_run,
        "messages": [{"role": "user", "content": "despliega mi app"}],
        "current_step": "start",
        "errors": [],
        "terraform_configs": {},
        "docker_compose": None,
        "project_analysis": None,
        "architecture_proposal": None,
        "deployment_result": None,
        "user_approved": None,
        "user_modifications": None,
    }


@pytest.mark.asyncio
async def test_graph_pauses_at_confirmation(sample_node_project):
    """El grafo debe pausar antes del nodo confirmation (HIL)."""
    graph = build_graph()
    config = {"configurable": {"thread_id": "test-pause-001"}}

    events = []
    async for event in graph.astream(_initial_state(str(sample_node_project)), config):
        events.append(event)

    state = graph.get_state(config)
    # Debe estar esperando en el nodo confirmation
    assert "confirmation" in state.next


@pytest.mark.asyncio
async def test_graph_completes_after_approval(sample_node_project):
    """Tras aprobar, el grafo debe completar el despliegue (dry-run)."""
    graph = build_graph()
    config = {"configurable": {"thread_id": "test-approve-001"}}

    # Primera ejecución — pausa en confirmation
    async for _ in graph.astream(_initial_state(str(sample_node_project), dry_run=True), config):
        pass

    assert "confirmation" in graph.get_state(config).next

    # Simular aprobación del usuario
    graph.update_state(config, {"user_approved": True, "user_modifications": None})

    # Reanudar hasta el final
    async for _ in graph.astream(None, config):
        pass

    final = graph.get_state(config).values
    assert final.get("deployment_result") is not None
    assert final["deployment_result"].success is True


@pytest.mark.asyncio
async def test_graph_ends_on_rejection(sample_node_project):
    """Si el usuario rechaza sin modificaciones, el grafo termina."""
    graph = build_graph()
    config = {"configurable": {"thread_id": "test-reject-001"}}

    async for _ in graph.astream(_initial_state(str(sample_node_project)), config):
        pass

    graph.update_state(config, {"user_approved": False, "user_modifications": None})

    async for _ in graph.astream(None, config):
        pass

    final = graph.get_state(config).values
    # No debe haber resultado de despliegue
    assert final.get("deployment_result") is None
