from __future__ import annotations

from pathlib import Path


class GitHubTool:
    """Herramientas para interactuar con repositorios GitHub. [STUB — Fase 2]"""

    def clone_repo(self, url: str, dest: Path) -> Path:
        """Clona un repositorio en dest y retorna la ruta."""
        import git
        repo_name = url.rstrip("/").split("/")[-1].removesuffix(".git")
        target = dest / repo_name
        git.Repo.clone_from(url, target)
        return target

    def get_repo_info(self, url: str) -> dict:
        """Retorna metadata básica del repo sin clonar. [STUB]"""
        return {"url": url, "name": url.split("/")[-1]}
