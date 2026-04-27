"""Tests for LLMAnalysisEnricher.

All tests mock the LLM backend so they run without a live model.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from aetherdeploy.analyzers.llm_enricher import (
    LLMAnalysisEnricher,
    EnrichmentResult,
    _build_context,
    _read_snippet,
)


# ---------------------------------------------------------------------------
# Mock backends
# ---------------------------------------------------------------------------

class _PaymentBackend:
    async def complete_json(self, messages, system=""):
        return {
            "additional_hints": ["has-payment"],
            "remove_hints": [],
            "confidence": 0.95,
            "reasoning": "stripe-java found in pom.xml",
        }


class _RemoveBackend:
    async def complete_json(self, messages, system=""):
        return {
            "additional_hints": [],
            "remove_hints": ["api-only"],
            "confidence": 0.8,
            "reasoning": "service has real dependencies",
        }


class _EmptyBackend:
    async def complete_json(self, messages, system=""):
        return {"additional_hints": [], "remove_hints": [], "confidence": 0.9}


# ---------------------------------------------------------------------------
# Gating tests (sync — no LLM call expected)
# ---------------------------------------------------------------------------

def test_gating_sparse_hints_triggers(tmp_path):
    enricher = LLMAnalysisEnricher()
    assert enricher._should_enrich(["api-only"], service_count=1, project_path=tmp_path)


def test_gating_compose_triggers(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services:\n  db:\n    image: mysql\n")
    enricher = LLMAnalysisEnricher()
    assert enricher._should_enrich(["has-database"], service_count=1, project_path=tmp_path)


def test_gating_multi_service_triggers(tmp_path):
    enricher = LLMAnalysisEnricher()
    assert enricher._should_enrich(["has-database"], service_count=3, project_path=tmp_path)


def test_gating_rich_single_service_skips(tmp_path):
    enricher = LLMAnalysisEnricher()
    hints = ["has-database", "has-migrations", "has-cache"]
    assert not enricher._should_enrich(hints, service_count=1, project_path=tmp_path)


def test_gating_multi_service_hint_triggers(tmp_path):
    enricher = LLMAnalysisEnricher()
    assert enricher._should_enrich(["multi-service", "has-database"], 1, tmp_path)


# ---------------------------------------------------------------------------
# Async enrichment tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrich_adds_missed_signal(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services:\n  db:\n    image: mysql\n")
    with patch("aetherdeploy.analyzers.llm_enricher.LLMBackendFactory.create",
               return_value=_PaymentBackend()):
        result = await LLMAnalysisEnricher().enrich(
            ["api-only"], service_count=1, project_path=tmp_path
        )
    assert "has-payment" in result.hints
    assert result.llm_used is True
    assert result.confidence == 0.95
    assert "stripe" in result.reasoning.lower()


@pytest.mark.asyncio
async def test_enrich_removes_wrong_hint(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services:\n  db:\n    image: postgres\n")
    with patch("aetherdeploy.analyzers.llm_enricher.LLMBackendFactory.create",
               return_value=_RemoveBackend()):
        result = await LLMAnalysisEnricher().enrich(
            ["api-only", "has-database"], service_count=1, project_path=tmp_path
        )
    assert "api-only" not in result.hints
    assert "has-database" in result.hints
    assert result.llm_used is True


@pytest.mark.asyncio
async def test_enrich_skipped_for_rich_small_project(tmp_path):
    # No compose, single service, rich hints → should skip LLM entirely
    hints = ["has-database", "has-migrations"]
    result = await LLMAnalysisEnricher().enrich(
        hints, service_count=1, project_path=tmp_path
    )
    assert result.llm_used is False
    assert result.hints == hints
    assert result.confidence == 1.0


@pytest.mark.asyncio
async def test_enrich_fallback_on_backend_creation_error(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services:\n  web:\n    build: .\n")
    with patch("aetherdeploy.analyzers.llm_enricher.LLMBackendFactory.create",
               side_effect=Exception("LLM offline")):
        result = await LLMAnalysisEnricher().enrich(
            ["api-only"], service_count=2, project_path=tmp_path
        )
    # Must return original hints unchanged — never crash
    assert result.hints == ["api-only"]
    assert result.llm_used is False
    assert result.confidence == 0.7


@pytest.mark.asyncio
async def test_enrich_fallback_on_complete_json_error(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services:\n  db:\n    image: redis\n")

    class _BrokenBackend:
        async def complete_json(self, messages, system=""):
            raise ValueError("model crashed")

    with patch("aetherdeploy.analyzers.llm_enricher.LLMBackendFactory.create",
               return_value=_BrokenBackend()):
        result = await LLMAnalysisEnricher().enrich(
            ["has-cache"], service_count=1, project_path=tmp_path
        )
    assert result.hints == ["has-cache"]
    assert result.llm_used is False


@pytest.mark.asyncio
async def test_enrich_rejects_invalid_hint_from_llm(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services:\n  db:\n    image: mysql\n")

    class _BadVocabBackend:
        async def complete_json(self, messages, system=""):
            return {
                "additional_hints": ["invented-signal", "has-database"],
                "remove_hints": [],
                "confidence": 0.9,
            }

    with patch("aetherdeploy.analyzers.llm_enricher.LLMBackendFactory.create",
               return_value=_BadVocabBackend()):
        result = await LLMAnalysisEnricher().enrich(
            ["api-only"], service_count=1, project_path=tmp_path
        )
    # "invented-signal" must be filtered out; valid "has-database" accepted
    assert "invented-signal" not in result.hints
    assert "has-database" in result.hints


@pytest.mark.asyncio
async def test_enrich_does_not_duplicate_existing_hints(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services:\n  db:\n    image: mysql\n")

    class _DuplicateBackend:
        async def complete_json(self, messages, system=""):
            return {
                "additional_hints": ["has-database", "has-cache"],
                "remove_hints": [],
                "confidence": 0.85,
            }

    with patch("aetherdeploy.analyzers.llm_enricher.LLMBackendFactory.create",
               return_value=_DuplicateBackend()):
        result = await LLMAnalysisEnricher().enrich(
            ["has-database"], service_count=1, project_path=tmp_path
        )
    # has-database was already present — must not be duplicated
    assert result.hints.count("has-database") == 1
    assert "has-cache" in result.hints


# ---------------------------------------------------------------------------
# Context builder tests
# ---------------------------------------------------------------------------

def test_context_contains_static_hints(tmp_path):
    ctx = _build_context(tmp_path, ["has-database", "has-migrations"])
    assert "has-database" in ctx
    assert "has-migrations" in ctx


def test_context_includes_compose_file(tmp_path):
    compose = "services:\n  db:\n    image: mysql:5.7\n  web:\n    build: .\n"
    (tmp_path / "docker-compose.yml").write_text(compose)
    ctx = _build_context(tmp_path, ["api-only"])
    assert "mysql" in ctx


def test_context_includes_env_example(tmp_path):
    (tmp_path / ".env.example").write_text("STRIPE_SECRET_KEY=sk_test_xxx\nDB_URL=postgres://...\n")
    (tmp_path / "docker-compose.yml").write_text("services:\n  web:\n    build: .\n")
    ctx = _build_context(tmp_path, ["api-only"])
    assert "STRIPE_SECRET_KEY" in ctx


def test_context_caps_budget(tmp_path):
    # Large file — must not exceed budget
    large = "services:\n" + "\n".join(f"  svc{i}:\n    image: nginx" for i in range(300))
    (tmp_path / "docker-compose.yml").write_text(large)
    ctx = _build_context(tmp_path, ["api-only"])
    # 8000 nominal + small header + tolerance
    assert len(ctx) <= 9_000


def test_context_package_json_extracts_deps(tmp_path):
    pkg = {"dependencies": {"stripe": "^12.0", "express": "^4.0"}, "devDependencies": {}}
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    (tmp_path / "docker-compose.yml").write_text("services:\n  web:\n    build: .\n")
    ctx = _build_context(tmp_path, ["api-only"])
    assert "stripe" in ctx
    assert "express" in ctx


def test_context_pom_xml_extracts_artifact_ids(tmp_path):
    pom = """<project>
  <dependencies>
    <dependency><artifactId>stripe-java</artifactId></dependency>
    <dependency><artifactId>spring-boot-starter-web</artifactId></dependency>
  </dependencies>
</project>"""
    (tmp_path / "pom.xml").write_text(pom)
    (tmp_path / "docker-compose.yml").write_text("services:\n  web:\n    build: .\n")
    ctx = _build_context(tmp_path, ["api-only"])
    assert "stripe-java" in ctx


# ---------------------------------------------------------------------------
# _read_snippet tests
# ---------------------------------------------------------------------------

def test_read_snippet_package_json(tmp_path):
    pkg = {"dependencies": {"stripe": "^12.0"}, "devDependencies": {"jest": "^29"}}
    path = tmp_path / "package.json"
    path.write_text(json.dumps(pkg))
    snippet = _read_snippet(path, "package.json", 40)
    assert "stripe" in snippet
    assert "jest" in snippet
    assert "dependencies:" in snippet


def test_read_snippet_pom_xml(tmp_path):
    pom = "<project><artifactId>stripe-java</artifactId></project>"
    path = tmp_path / "pom.xml"
    path.write_text(pom)
    snippet = _read_snippet(path, "pom.xml", 30)
    assert "stripe-java" in snippet
    assert "artifactIds:" in snippet


def test_read_snippet_plain_file(tmp_path):
    content = "\n".join(f"line {i}" for i in range(100))
    path = tmp_path / "README.md"
    path.write_text(content)
    snippet = _read_snippet(path, "README.md", 30)
    lines = snippet.splitlines()
    assert len(lines) <= 30


def test_read_snippet_missing_file(tmp_path):
    snippet = _read_snippet(tmp_path / "nonexistent.txt", "nonexistent.txt", 10)
    assert snippet == ""
