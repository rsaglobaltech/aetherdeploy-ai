from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from ..models import LanguageAnalysis


class LanguageAnalyzer(ABC):
    """Interfaz base para analizadores de lenguaje/framework."""

    @abstractmethod
    def can_analyze(self, path: Path) -> bool:
        """Retorna True si este analizador aplica al proyecto."""

    @abstractmethod
    def analyze(self, path: Path) -> LanguageAnalysis:
        """Analiza el proyecto y retorna un LanguageAnalysis."""

    def _read_json(self, path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _file_exists(self, path: Path, *names: str) -> bool:
        return any((path / name).exists() for name in names)

    def _read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
