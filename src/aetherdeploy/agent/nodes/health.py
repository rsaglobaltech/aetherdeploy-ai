"""Post-deploy health checks (MEJORAS.md §2.4).

Polls deployment endpoints until they respond OK or a timeout elapses. Probe
paths are inferred from the project analysis when possible (``/healthz``,
``/health``, ``/`` fallback). The result is structured so callers can decide
whether to trigger a rollback (§2.3) or surface the issue to the user.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urlparse, urlunparse

import httpx

from ...models import ProjectAnalysis


DEFAULT_PROBE_PATHS: tuple[str, ...] = ("/healthz", "/health", "/api/health", "/")
DEFAULT_TIMEOUT_S: float = 120.0
DEFAULT_INTERVAL_S: float = 3.0
PER_REQUEST_TIMEOUT_S: float = 5.0
SUCCESS_STATUSES: frozenset[int] = frozenset({200, 204})


@dataclass
class EndpointHealth:
    url: str
    status: str  # "ok" | "fail" | "skipped"
    http_status: int | None = None
    latency_ms: int | None = None
    attempts: int = 0
    error: str | None = None


@dataclass
class HealthReport:
    checks: list[EndpointHealth] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        if not self.checks:
            return True
        return all(c.status in ("ok", "skipped") for c in self.checks)

    @property
    def any_probed(self) -> bool:
        return any(c.status != "skipped" for c in self.checks)

    def summary(self) -> str:
        if not self.checks:
            return "No endpoints to probe."
        parts = []
        for c in self.checks:
            if c.status == "ok":
                parts.append(f"✓ {c.url} ({c.http_status}, {c.latency_ms}ms)")
            elif c.status == "skipped":
                parts.append(f"– {c.url} (skipped: {c.error or 'unprobeable'})")
            else:
                parts.append(f"✗ {c.url} ({c.error or 'no response'})")
        return "\n".join(parts)


def infer_probe_paths(analysis: ProjectAnalysis | None) -> tuple[str, ...]:
    """Infers candidate health-check paths from the project analysis.

    Spring Boot exposes ``/actuator/health``; FastAPI/Flask typically ``/healthz``
    or ``/health``. When unknown, fall back to the defaults plus root.
    """
    if analysis is None:
        return DEFAULT_PROBE_PATHS

    frameworks = {f.lower() for f in (analysis.frameworks or [])}
    hints = {h.lower() for h in (analysis.infrastructure_hints or [])}

    if "spring-boot" in frameworks or "spring" in frameworks:
        return ("/actuator/health", *DEFAULT_PROBE_PATHS)
    if "django" in frameworks:
        return ("/health/", *DEFAULT_PROBE_PATHS)
    if "rails" in frameworks:
        return ("/up", *DEFAULT_PROBE_PATHS)
    if any("graphql" in h for h in hints):
        return ("/graphql", *DEFAULT_PROBE_PATHS)
    return DEFAULT_PROBE_PATHS


def _is_probeable(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False
    return True


def _join_path(url: str, path: str) -> str:
    parsed = urlparse(url)
    new_path = path if path.startswith("/") else "/" + path
    base_path = parsed.path.rstrip("/")
    if base_path and base_path != "/":
        new_path = base_path + new_path
    return urlunparse((parsed.scheme, parsed.netloc, new_path, "", "", ""))


async def _probe_once(client: httpx.AsyncClient, url: str) -> tuple[int | None, str | None]:
    try:
        resp = await client.get(url, timeout=PER_REQUEST_TIMEOUT_S)
        return resp.status_code, None
    except httpx.HTTPError as exc:
        return None, str(exc)


async def _probe_endpoint(
    client: httpx.AsyncClient,
    url: str,
    probe_paths: Iterable[str],
    timeout_s: float,
    interval_s: float,
) -> EndpointHealth:
    if not _is_probeable(url):
        return EndpointHealth(url=url, status="skipped", error="non-http endpoint")

    candidates = [_join_path(url, p) for p in probe_paths]
    deadline = time.monotonic() + timeout_s
    attempts = 0
    last_error: str | None = None
    last_status: int | None = None

    while time.monotonic() < deadline:
        for candidate in candidates:
            attempts += 1
            t0 = time.monotonic()
            code, err = await _probe_once(client, candidate)
            latency_ms = int((time.monotonic() - t0) * 1000)
            if code is not None and code in SUCCESS_STATUSES:
                return EndpointHealth(
                    url=candidate,
                    status="ok",
                    http_status=code,
                    latency_ms=latency_ms,
                    attempts=attempts,
                )
            last_status = code if code is not None else last_status
            last_error = err or (f"HTTP {code}" if code is not None else last_error)
        if time.monotonic() + interval_s >= deadline:
            break
        await asyncio.sleep(interval_s)

    return EndpointHealth(
        url=url,
        status="fail",
        http_status=last_status,
        attempts=attempts,
        error=last_error or "no response before timeout",
    )


async def check_endpoints(
    endpoints: Iterable[str],
    analysis: ProjectAnalysis | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    interval_s: float = DEFAULT_INTERVAL_S,
) -> HealthReport:
    """Probes every endpoint until one path returns success or the timeout elapses.

    A single ``HealthReport`` aggregates the per-endpoint results. The report is
    considered OK when every endpoint either passed or was skipped (e.g. a
    non-http URL such as an SQS queue ARN).
    """
    urls = [u for u in endpoints if u]
    if not urls:
        return HealthReport()

    probe_paths = infer_probe_paths(analysis)
    report = HealthReport()
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for url in urls:
            check = await _probe_endpoint(client, url, probe_paths, timeout_s, interval_s)
            report.checks.append(check)
    return report
