from __future__ import annotations

import json
from pathlib import Path

import pytest

from aetherdeploy.analyzers.detector import ProjectDetector
from aetherdeploy.analyzers.node import NodeAnalyzer
from aetherdeploy.analyzers.python import PythonAnalyzer
from aetherdeploy.analyzers.java import JavaAnalyzer
from aetherdeploy.analyzers.go import GoAnalyzer


# ---------------------------------------------------------------------------
# NodeAnalyzer
# ---------------------------------------------------------------------------

def test_node_analyzer_detects_express(sample_node_project):
    analyzer = NodeAnalyzer()
    assert analyzer.can_analyze(sample_node_project)
    result = analyzer.analyze(sample_node_project)
    assert result.language in ("Node.js", "TypeScript")
    assert result.framework == "Express"
    assert result.has_dockerfile is True
    assert result.has_tests is True
    assert 3000 in result.exposed_ports


def test_node_analyzer_detects_nextjs(tmp_path):
    pkg = {"dependencies": {"next": "14.0.0", "react": "18.0.0"}, "devDependencies": {}}
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    result = NodeAnalyzer().analyze(tmp_path)
    assert result.framework == "Next.js"
    assert "fullstack" in result.architecture_hints


def test_node_analyzer_not_applicable(tmp_path):
    # Sin package.json
    assert not NodeAnalyzer().can_analyze(tmp_path)


# ---------------------------------------------------------------------------
# PythonAnalyzer
# ---------------------------------------------------------------------------

def test_python_analyzer_detects_fastapi(sample_python_project):
    analyzer = PythonAnalyzer()
    assert analyzer.can_analyze(sample_python_project)
    result = analyzer.analyze(sample_python_project)
    assert result.language == "Python"
    assert result.framework == "FastAPI"
    assert 8000 in result.exposed_ports


def test_python_analyzer_detects_django(tmp_path):
    (tmp_path / "requirements.txt").write_text("django>=4.0\npsycopg2>=2.9\n")
    result = PythonAnalyzer().analyze(tmp_path)
    assert result.framework == "Django"
    assert "has-database" in result.architecture_hints


def test_python_analyzer_detects_worker(tmp_path):
    (tmp_path / "requirements.txt").write_text("fastapi>=0.110\ncelery>=5.0\nredis>=5.0\n")
    result = PythonAnalyzer().analyze(tmp_path)
    assert "has-worker" in result.architecture_hints
    assert "has-cache" in result.architecture_hints


# ---------------------------------------------------------------------------
# JavaAnalyzer
# ---------------------------------------------------------------------------

def test_java_analyzer_detects_spring_boot(sample_java_project):
    analyzer = JavaAnalyzer()
    assert analyzer.can_analyze(sample_java_project)
    result = analyzer.analyze(sample_java_project)
    assert result.language == "Java"
    assert result.framework == "Spring Boot"
    assert 8080 in result.exposed_ports


# ---------------------------------------------------------------------------
# GoAnalyzer
# ---------------------------------------------------------------------------

def test_go_analyzer_detects_gin(tmp_path):
    go_mod = "module example.com/myapp\n\ngo 1.21\n\nrequire (\n\tgithub.com/gin-gonic/gin v1.9.1\n)\n"
    (tmp_path / "go.mod").write_text(go_mod)
    analyzer = GoAnalyzer()
    assert analyzer.can_analyze(tmp_path)
    result = analyzer.analyze(tmp_path)
    assert result.language == "Go"
    assert result.framework == "Gin"
    assert result.version == "1.21"


# ---------------------------------------------------------------------------
# ProjectDetector (orquestador)
# ---------------------------------------------------------------------------

def test_detector_node_project(sample_node_project):
    detector = ProjectDetector()
    analysis = detector.detect(sample_node_project)
    assert analysis.primary_language in ("Node.js", "TypeScript")
    assert "Express" in analysis.frameworks
    assert analysis.has_dockerfile is True


def test_detector_python_project(sample_python_project):
    detector = ProjectDetector()
    analysis = detector.detect(sample_python_project)
    assert analysis.primary_language == "Python"
    assert "FastAPI" in analysis.frameworks


def test_detector_unknown_project(tmp_path):
    detector = ProjectDetector()
    analysis = detector.detect(tmp_path)
    assert analysis.primary_language == "unknown"
