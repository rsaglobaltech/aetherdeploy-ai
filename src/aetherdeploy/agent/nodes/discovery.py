from __future__ import annotations

import os
from pathlib import Path

from ..state import AetherState

# Archivos que indican que hay un proyecto en el directorio
_PROJECT_MARKERS = {
    "package.json", "pyproject.toml", "requirements.txt", "setup.py",
    "pom.xml", "build.gradle", "go.mod", "Cargo.toml", "Dockerfile",
    "docker-compose.yml", "docker-compose.yaml",
}


async def discovery_node(state: AetherState) -> dict:
    """Detecta si hay un proyecto local o solicita URL de GitHub."""
    project_path = state.get("project_path") or os.getcwd()
    path = Path(project_path)

    if _has_project_files(path):
        return {
            "project_path": str(path.resolve()),
            "current_step": "discovery_done",
            "messages": [
            *state.get("messages", []),
            {"role": "assistant", "content": f"Project detected in `{path.resolve()}`"},
            ],
        }

    # No hay proyecto local — stub: pide GitHub URL
    return {
        "project_path": None,
        "current_step": "discovery_needs_github",
        "messages": [
            *state.get("messages", []),
            {
                "role": "assistant",
                "content": (
                    "I couldn't find a project in the current directory. "
                    "Do you have the URL of a GitHub repository?"
                ),
            },
        ],
    }


def _has_project_files(path: Path) -> bool:
    if not path.is_dir():
        return False
    return any((path / marker).exists() for marker in _PROJECT_MARKERS)
