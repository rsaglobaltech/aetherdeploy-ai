from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Literal

# Env var honored by build_graph() — set by the CLI when --persist is passed.
# Lets call sites that don't directly construct the checkpointer (legacy paths
# in cli.py) still get persistence without threading a parameter through every
# function. Documented in MEJORAS.md §1.2.
_ENV_PERSIST_PATH = "AETHER_CHECKPOINT_PATH"

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, StateGraph

from ..models import (
    ApplicationTopology,
    ArchitectureProposal,
    Decision,
    DeploymentResult,
    EnvironmentConfig,
    LanguageAnalysis,
    ProjectAnalysis,
    ProposalMetadata,
    ServiceRecommendation,
    ServiceSummary,
)
from ..observability import traced_node

# LangGraph 1.x serializa dataclasses en checkpoints con msgpack ext-types.
# Registrarlas explícitamente evita warnings en stderr y deja el CLI compatible
# con LANGGRAPH_STRICT_MSGPACK=true.
_SERDE = JsonPlusSerializer(
    allowed_msgpack_modules=[
        ApplicationTopology,
        ArchitectureProposal,
        Decision,
        DeploymentResult,
        EnvironmentConfig,
        LanguageAnalysis,
        ProjectAnalysis,
        ProposalMetadata,
        ServiceRecommendation,
        ServiceSummary,
    ],
)

from .nodes import (
    analysis_node,
    confirmation_node,
    discovery_node,
    execution_node,
    generation_node,
    promotion_node,
    proposal_node,
)
from .state import AetherState


def _route_after_discovery(state: AetherState) -> Literal["analysis", "__end__"]:
    if state.get("current_step") == "discovery_needs_github":
        return "__end__"
    return "analysis"


def _route_after_proposal(state: AetherState) -> Literal["confirmation", "__end__"]:
    if state.get("requested_action") == "init":
        return "__end__"
    return "confirmation"


def _route_after_confirmation(state: AetherState) -> Literal["generation", "proposal", "__end__"]:
    step = state.get("current_step", "")
    if step == "confirmed":
        return "generation"
    if step == "needs_revision":
        return "proposal"
    return "__end__"


def _route_after_execution(state: AetherState) -> Literal["promotion", "__end__"]:
    """Route to promotion after a successful feature-env deploy; otherwise finish."""
    action = state.get("requested_action")
    if action in ("plan", "destroy"):
        return "__end__"
    result = state.get("deployment_result")
    envs = state.get("target_environments") or []
    if "feature" in envs and result and result.success:
        return "promotion"
    return "__end__"


def _route_after_promotion(state: AetherState) -> Literal["execution", "__end__"]:
    step = state.get("current_step", "")
    if step == "promote_approved":
        return "execution"
    return "__end__"


def default_checkpoint_path() -> Path:
    """Canonical location for the persistent agent checkpoint database."""
    return Path.home() / ".aetherdeploy" / "agent.sqlite"


def make_sqlite_checkpointer(
    path: Path | str | None = None,
) -> "BaseCheckpointSaver":
    """Return a persistent SQLite-backed checkpoint saver.

    Survives across CLI invocations: HIL interrupts can be resumed days later
    via ``aetherdeploy resume <thread-id>``. See MEJORAS.md §1.2.

    The directory is created on demand. ``check_same_thread=False`` lets the
    same connection be reused across the async tasks LangGraph spawns.
    """
    target = Path(path) if path else default_checkpoint_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    from langgraph.checkpoint.sqlite import SqliteSaver

    conn = sqlite3.connect(str(target), check_same_thread=False)
    return SqliteSaver(conn, serde=_SERDE)


def build_graph(checkpointer: "BaseCheckpointSaver | None" = None):
    """Construye y compila el grafo LangGraph de AetherDeploy.

    El flujo es:
      discovery → analysis → proposal → [HIL interrupt] → confirmation
        → generation → execution
          → (si env=feature y éxito) → [HIL interrupt] → promotion
            → (si aprueba) → execution (prod)

    interrupt_before=["confirmation", "promotion"] implementa dos Human-in-the-Loop:
    1. Antes de confirmation: el usuario aprueba la propuesta de arquitectura.
    2. Antes de promotion: el usuario decide si promover de LocalStack a prod real.

    Parameters
    ----------
    checkpointer:
        Optional saver to persist agent state. Defaults to an in-memory saver
        (state evaporates when the process exits). Pass the result of
        :func:`make_sqlite_checkpointer` to get a durable SQLite backend that
        allows resuming sessions across CLI invocations (MEJORAS.md §1.2).
    """
    builder = StateGraph(AetherState)

    builder.add_node("discovery", traced_node(discovery_node))
    builder.add_node("analysis", traced_node(analysis_node))
    builder.add_node("proposal", traced_node(proposal_node))
    builder.add_node("confirmation", traced_node(confirmation_node))
    builder.add_node("generation", traced_node(generation_node))
    builder.add_node("execution", traced_node(execution_node))
    builder.add_node("promotion", traced_node(promotion_node))

    builder.set_entry_point("discovery")

    builder.add_conditional_edges(
        "discovery",
        _route_after_discovery,
        {"analysis": "analysis", "__end__": END},
    )
    builder.add_edge("analysis", "proposal")
    builder.add_conditional_edges(
        "proposal",
        _route_after_proposal,
        {"confirmation": "confirmation", "__end__": END},
    )

    builder.add_conditional_edges(
        "confirmation",
        _route_after_confirmation,
        {"generation": "generation", "proposal": "proposal", "__end__": END},
    )

    builder.add_edge("generation", "execution")

    builder.add_conditional_edges(
        "execution",
        _route_after_execution,
        {"promotion": "promotion", "__end__": END},
    )

    builder.add_conditional_edges(
        "promotion",
        _route_after_promotion,
        {"execution": "execution", "__end__": END},
    )

    if checkpointer is None:
        env_path = os.environ.get(_ENV_PERSIST_PATH)
        if env_path:
            checkpointer = make_sqlite_checkpointer(env_path)
    saver = checkpointer if checkpointer is not None else MemorySaver(serde=_SERDE)
    return builder.compile(
        checkpointer=saver,
        interrupt_before=["confirmation", "promotion"],
    )
