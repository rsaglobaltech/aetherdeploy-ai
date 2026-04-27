from __future__ import annotations

from pathlib import Path


def list_project_files(path: Path, max_depth: int = 3) -> list[str]:
    """Lista ficheros del proyecto hasta max_depth niveles."""
    result = []
    for item in path.rglob("*"):
        try:
            rel = item.relative_to(path)
            if len(rel.parts) <= max_depth and not _is_ignored(rel):
                result.append(str(rel))
        except ValueError:
            continue
    return sorted(result)


def read_file_safe(path: Path, max_bytes: int = 32_768) -> str | None:
    """Lee un fichero con límite de tamaño. Retorna None si no existe o es binario."""
    try:
        content = path.read_bytes()
        if b"\x00" in content[:512]:
            return None
        return content[:max_bytes].decode("utf-8", errors="replace")
    except (OSError, PermissionError):
        return None


_IGNORED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".aetherdeploy", ".terraform",
}


def _is_ignored(rel: Path) -> bool:
    return any(part in _IGNORED_DIRS for part in rel.parts)
