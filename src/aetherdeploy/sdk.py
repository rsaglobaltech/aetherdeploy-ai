"""SDK pública de AetherDeploy — uso programático sin CLI."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

from .agent.graph import build_graph, make_sqlite_checkpointer
from .agent.state import AetherState
from .models import ArchitectureProposal, DeploymentResult, ProjectAnalysis


class AetherDeploy:
    """Cliente programático de AetherDeploy.

    Uso básico::

        agent = AetherDeploy()
        result = await agent.deploy(
            project=".",
            instruction="despliega en producción",
            environments=["prod"],
        )
        print(result.endpoints)

    Uso con callback HIL (Human-in-the-Loop)::

        async def my_approval(proposal: ArchitectureProposal) -> bool:
            print(f"Propuesta: {proposal.provider} — {proposal.total_estimated_cost}")
            return True  # aprobar automáticamente

        result = await agent.deploy(
            project=".",
            instruction="despliega en producción",
            on_proposal=my_approval,
        )
    """

    def __init__(
        self,
        provider: str = "aws",
        region: str | None = None,
        llm_backend: str = "ollama",
        llm_model: str = "gemma3",
        persist_path: str | Path | None = None,
    ) -> None:
        """Build an AetherDeploy SDK client.

        Parameters
        ----------
        persist_path:
            If provided, agent state is persisted to a SQLite database at this
            path. Survives across process restarts — call :meth:`resume` with
            the saved ``thread_id`` to continue. If ``None`` (default) state is
            held only in memory and lost when the process exits.
        """
        self._provider = provider
        self._region = region
        self._llm_backend = llm_backend
        self._llm_model = llm_model
        checkpointer = make_sqlite_checkpointer(persist_path) if persist_path else None
        self._graph = build_graph(checkpointer=checkpointer)

    async def deploy(
        self,
        project: str = ".",
        instruction: str = "Deploy this project",
        environments: list[str] | None = None,
        dry_run: bool = False,
        on_proposal: Callable[[ArchitectureProposal], Awaitable[bool]] | None = None,
    ) -> DeploymentResult:
        """Despliega el proyecto en los entornos especificados.

        Args:
            project: Ruta al directorio del proyecto.
            instruction: Instrucción en lenguaje natural.
            environments: Lista de entornos: ["local", "feature", "prod"].
            dry_run: Si True, genera IaC pero no despliega.
            on_proposal: Callback async para aprobación HIL.
                         Si es None, aprueba automáticamente.
        """
        envs = environments or ["prod"]
        config = {"configurable": {"thread_id": str(uuid4())}}

        initial_state: AetherState = {
            "user_message": instruction,
            "project_path": project,
            "github_url": None,
            "target_environments": envs,
            "preferred_provider": self._provider,
            "dry_run": dry_run,
            "messages": [{"role": "user", "content": instruction}],
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

        async for _ in self._graph.astream(initial_state, config):
            pass

        while self._graph.get_state(config).next:
            state_values = self._graph.get_state(config).values
            proposal = state_values.get("architecture_proposal")

            if proposal and on_proposal:
                approved = await on_proposal(proposal)
            else:
                approved = True  # auto-approve en modo no-interactivo

            self._graph.update_state(config, {"user_approved": approved, "user_modifications": None})
            async for _ in self._graph.astream(None, config):
                pass

        final = self._graph.get_state(config).values
        result = final.get("deployment_result")
        return result or DeploymentResult(success=False)

    async def analyze(self, project: str = ".") -> ProjectAnalysis:
        """Analiza el proyecto sin desplegar."""
        config = {"configurable": {"thread_id": str(uuid4())}}
        initial_state: AetherState = {
            "user_message": "analyze",
            "project_path": project,
            "github_url": None,
            "target_environments": [],
            "preferred_provider": self._provider,
            "dry_run": True,
            "messages": [],
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

        # Solo ejecutar hasta el nodo analysis (antes del interrupt)
        async for _ in self._graph.astream(initial_state, config):
            state = self._graph.get_state(config).values
            if state.get("current_step") == "analysis_done":
                break

        final = self._graph.get_state(config).values
        analysis = final.get("project_analysis")
        if analysis is None:
            from .models import ProjectAnalysis as PA
            return PA(primary_language="unknown")
        return analysis

    async def destroy(self, project: str = ".", environment: str = "prod") -> None:
        """Destruye la infraestructura de un entorno."""
        from pathlib import Path
        from .terraform.runner import TerraformRunner
        tf_dir = Path(project) / ".aetherdeploy" / "terraform" / environment
        runner = TerraformRunner()
        if not runner.is_available():
            raise RuntimeError("terraform CLI no está disponible")
        result = runner.destroy(tf_dir, auto_approve=True)
        if not result.ok:
            raise RuntimeError(f"terraform destroy falló: {result.stderr}")
