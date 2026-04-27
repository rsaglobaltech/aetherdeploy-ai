from __future__ import annotations

import pytest
import yaml

from aetherdeploy.docker.composer import DockerComposer
from aetherdeploy.models import ArchitectureProposal, ProjectAnalysis


def _analysis(**kwargs) -> ProjectAnalysis:
    defaults = dict(
        primary_language="Node.js",
        frameworks=["Express"],
        architecture="monolith",
        infrastructure_hints=["api-only"],
        dependencies=[],
        exposed_ports=[3000],
        has_dockerfile=True,
    )
    defaults.update(kwargs)
    return ProjectAnalysis(**defaults)


def _proposal() -> ArchitectureProposal:
    return ArchitectureProposal(provider="aws", region="us-east-1")


# ---------------------------------------------------------------------------
# DockerComposer
# ---------------------------------------------------------------------------

def test_compose_has_app_service():
    composer = DockerComposer()
    content = composer.generate(_analysis(), _proposal())
    data = yaml.safe_load(content)
    assert "app" in data["services"]


def test_compose_node_app_exposes_correct_port():
    composer = DockerComposer()
    content = composer.generate(_analysis(exposed_ports=[3000]), _proposal())
    data = yaml.safe_load(content)
    assert any("3000" in str(p) for p in data["services"]["app"]["ports"])


def test_compose_app_uses_build_when_dockerfile_exists():
    composer = DockerComposer()
    content = composer.generate(_analysis(has_dockerfile=True), _proposal())
    data = yaml.safe_load(content)
    assert "build" in data["services"]["app"]


def test_compose_app_uses_image_when_no_dockerfile():
    composer = DockerComposer()
    content = composer.generate(_analysis(has_dockerfile=False), _proposal())
    data = yaml.safe_load(content)
    assert "image" in data["services"]["app"]


def test_compose_adds_postgres_for_database_hint():
    composer = DockerComposer()
    analysis = _analysis(infrastructure_hints=["api-only", "has-database"])
    content = composer.generate(analysis, _proposal())
    data = yaml.safe_load(content)
    assert "db" in data["services"]
    assert data["services"]["db"]["image"].startswith("postgres")


def test_compose_adds_redis_for_cache_hint():
    composer = DockerComposer()
    analysis = _analysis(infrastructure_hints=["api-only", "has-cache"])
    content = composer.generate(analysis, _proposal())
    data = yaml.safe_load(content)
    assert "cache" in data["services"]
    assert "redis" in data["services"]["cache"]["image"]


def test_compose_adds_postgres_for_psycopg2_dep():
    composer = DockerComposer()
    analysis = _analysis(
        primary_language="Python",
        frameworks=["FastAPI"],
        dependencies=["fastapi", "psycopg2"],
        infrastructure_hints=["api-only"],
    )
    content = composer.generate(analysis, _proposal())
    data = yaml.safe_load(content)
    assert "db" in data["services"]


def test_compose_database_env_injected_into_app():
    composer = DockerComposer()
    analysis = _analysis(infrastructure_hints=["api-only", "has-database"])
    content = composer.generate(analysis, _proposal())
    data = yaml.safe_load(content)
    env_vars = data["services"]["app"].get("environment", [])
    assert any("DATABASE_URL" in str(e) for e in env_vars)


def test_compose_python_fastapi_default_command():
    composer = DockerComposer()
    analysis = _analysis(
        primary_language="Python",
        frameworks=["FastAPI"],
        has_dockerfile=False,
        infrastructure_hints=["api-only"],
    )
    content = composer.generate(analysis, _proposal())
    data = yaml.safe_load(content)
    assert "uvicorn" in data["services"]["app"]["command"]


def test_compose_includes_network():
    composer = DockerComposer()
    content = composer.generate(_analysis(), _proposal())
    data = yaml.safe_load(content)
    assert "networks" in data
    assert "app-net" in data["networks"]
