from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def sample_node_project(tmp_path: Path) -> Path:
    """Proyecto Node.js mínimo con Express."""
    pkg = {
        "name": "sample-app",
        "version": "1.0.0",
        "main": "index.js",
        "scripts": {"start": "node index.js"},
        "dependencies": {"express": "^4.18.0"},
        "devDependencies": {"jest": "^29.0.0"},
        "engines": {"node": ">=18.0.0"},
    }
    import json
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    (tmp_path / "index.js").write_text("const express = require('express');\nconst app = express();\napp.listen(3000);\n")
    (tmp_path / "Dockerfile").write_text("FROM node:18-alpine\nWORKDIR /app\nCOPY . .\nRUN npm install\nEXPOSE 3000\nCMD [\"node\", \"index.js\"]\n")
    return tmp_path


@pytest.fixture
def sample_python_project(tmp_path: Path) -> Path:
    """Proyecto Python mínimo con FastAPI."""
    pyproject = """
[project]
name = "sample-api"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["fastapi>=0.110", "uvicorn>=0.29"]
"""
    (tmp_path / "pyproject.toml").write_text(pyproject)
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    return tmp_path


@pytest.fixture
def sample_java_project(tmp_path: Path) -> Path:
    """Proyecto Java mínimo con Spring Boot."""
    pom = """<?xml version="1.0" encoding="UTF-8"?>
<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>demo</artifactId>
  <version>0.0.1-SNAPSHOT</version>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.2.0</version>
  </parent>
</project>"""
    (tmp_path / "pom.xml").write_text(pom)
    return tmp_path


@pytest.fixture
def mock_config(monkeypatch):
    """Config con valores de test que no requieren servicios externos."""
    monkeypatch.setenv("AETHER_LLM_BACKEND", "ollama")
    monkeypatch.setenv("AETHER_LLM_MODEL", "gemma3")
    monkeypatch.setenv("AETHER_DEFAULT_PROVIDER", "aws")

    # Reset la instancia global para que tome los valores del monkeypatch
    import aetherdeploy.config as cfg_module
    cfg_module._config = None
    yield
    cfg_module._config = None
