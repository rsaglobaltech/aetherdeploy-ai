"""Tests for the post-deploy health check module (MEJORAS.md §2.4)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from aetherdeploy.agent.nodes import health
from aetherdeploy.models import ProjectAnalysis


def _stub_response(status_code: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    return resp


def test_infer_probe_paths_defaults():
    paths = health.infer_probe_paths(None)
    assert paths == health.DEFAULT_PROBE_PATHS


def test_infer_probe_paths_spring_actuator():
    analysis = ProjectAnalysis(primary_language="java", frameworks=["spring-boot"])
    paths = health.infer_probe_paths(analysis)
    assert paths[0] == "/actuator/health"


def test_infer_probe_paths_django():
    analysis = ProjectAnalysis(primary_language="python", frameworks=["django"])
    paths = health.infer_probe_paths(analysis)
    assert paths[0] == "/health/"


def test_is_probeable_filters_non_http():
    assert health._is_probeable("http://localhost:8080")
    assert health._is_probeable("https://api.example.com")
    assert not health._is_probeable("arn:aws:sqs:us-east-1:123:queue")
    assert not health._is_probeable("(plan: no changes applied)")
    assert not health._is_probeable("")


def test_join_path_handles_base_and_trailing_slashes():
    assert health._join_path("http://h", "/healthz") == "http://h/healthz"
    assert health._join_path("http://h/", "/healthz") == "http://h/healthz"
    assert health._join_path("http://h/api", "/health") == "http://h/api/health"
    assert health._join_path("http://h/api/", "health") == "http://h/api/health"


def test_check_endpoints_returns_ok_when_first_path_responds():
    client_mock = MagicMock()
    client_mock.__aenter__ = AsyncMock(return_value=client_mock)
    client_mock.__aexit__ = AsyncMock(return_value=False)
    client_mock.get = AsyncMock(return_value=_stub_response(200))

    with patch.object(health.httpx, "AsyncClient", return_value=client_mock):
        report = asyncio.run(
            health.check_endpoints(
                ["http://localhost:8080"],
                analysis=None,
                timeout_s=5.0,
                interval_s=0.01,
            )
        )

    assert report.ok
    assert len(report.checks) == 1
    check = report.checks[0]
    assert check.status == "ok"
    assert check.http_status == 200
    assert check.url.endswith("/healthz")
    assert client_mock.get.await_count == 1


def test_check_endpoints_skips_non_http():
    report = asyncio.run(
        health.check_endpoints(
            ["arn:aws:sqs:us-east-1:123:queue", "(none)"],
            analysis=None,
            timeout_s=5.0,
            interval_s=0.01,
        )
    )
    assert report.ok  # skipped endpoints don't fail the report
    assert all(c.status == "skipped" for c in report.checks)


def test_check_endpoints_fails_after_timeout_when_no_route_works():
    client_mock = MagicMock()
    client_mock.__aenter__ = AsyncMock(return_value=client_mock)
    client_mock.__aexit__ = AsyncMock(return_value=False)
    client_mock.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    with patch.object(health.httpx, "AsyncClient", return_value=client_mock):
        report = asyncio.run(
            health.check_endpoints(
                ["http://localhost:9"],
                analysis=None,
                timeout_s=0.05,
                interval_s=0.01,
            )
        )

    assert not report.ok
    check = report.checks[0]
    assert check.status == "fail"
    assert "connection refused" in (check.error or "")
    assert check.attempts >= 1


def test_check_endpoints_fails_on_persistent_5xx():
    client_mock = MagicMock()
    client_mock.__aenter__ = AsyncMock(return_value=client_mock)
    client_mock.__aexit__ = AsyncMock(return_value=False)
    client_mock.get = AsyncMock(return_value=_stub_response(503))

    with patch.object(health.httpx, "AsyncClient", return_value=client_mock):
        report = asyncio.run(
            health.check_endpoints(
                ["http://localhost:8080"],
                analysis=None,
                timeout_s=0.05,
                interval_s=0.01,
            )
        )

    assert not report.ok
    check = report.checks[0]
    assert check.status == "fail"
    assert check.http_status == 503


def test_check_endpoints_no_urls_returns_empty_ok_report():
    report = asyncio.run(health.check_endpoints([]))
    assert report.ok
    assert report.checks == []


def test_summary_lists_results():
    report = health.HealthReport(
        checks=[
            health.EndpointHealth(url="http://a/healthz", status="ok", http_status=200, latency_ms=12),
            health.EndpointHealth(url="http://b", status="fail", error="boom"),
        ]
    )
    summary = report.summary()
    assert "✓ http://a/healthz" in summary
    assert "✗ http://b" in summary
    assert "boom" in summary
