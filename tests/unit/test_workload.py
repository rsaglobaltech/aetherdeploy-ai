"""Tests for the WorkloadFingerprint schema (workload.py)."""
from __future__ import annotations

from aetherdeploy.models import ApplicationTopology, ServiceSummary
from aetherdeploy.workload import (
    SLO,
    BackgroundJob,
    DataAccessPattern,
    Endpoint,
    TrafficProfile,
    WorkloadFingerprint,
)


def _topology(hints: list[str], *, service_count: int = 1, framework: str | None = "fastapi") -> ApplicationTopology:
    services = [
        ServiceSummary(
            name=f"svc-{i}",
            path_relative=f"./svc-{i}",
            language="python",
            framework=framework,
            hints=hints,
            ports=[8000 + i],
            has_dockerfile=True,
        )
        for i in range(service_count)
    ]
    return ApplicationTopology(
        project_name="demo",
        profile="small",
        service_count=service_count,
        services=services,
        all_hints=hints,
        has_compose=False,
        compose_service_count=0,
    )


def test_from_topology_maps_basic_fields():
    fp = WorkloadFingerprint.from_topology(_topology([]))
    assert fp.project_name == "demo"
    assert fp.languages == ["python"]
    assert fp.frameworks == ["fastapi"]
    assert fp.architecture == "monolith"
    assert fp.has_dockerfile is True
    assert fp.has_compose is False


def test_from_topology_detects_microservices_by_count():
    fp = WorkloadFingerprint.from_topology(_topology([], service_count=6))
    assert fp.architecture == "microservices"


def test_from_topology_detects_microservices_by_hint():
    fp = WorkloadFingerprint.from_topology(_topology(["has-service-discovery"]))
    assert fp.architecture == "microservices"


def test_from_topology_detects_workers():
    fp = WorkloadFingerprint.from_topology(_topology(["has-worker"]))
    assert fp.architecture == "monolith-with-workers"


def test_from_topology_propagates_state_and_capability_hints():
    fp = WorkloadFingerprint.from_topology(
        _topology(["uses-websockets", "stateful", "cpu-bound", "memory-bound", "long-running-task"])
    )
    assert fp.uses_websockets is True
    assert fp.state_kind == "stateful"
    assert fp.cpu_bound is True
    assert fp.memory_bound is True
    assert fp.long_running is True


def test_as_legacy_hints_roundtrip():
    fp = WorkloadFingerprint(
        project_name="demo",
        uses_websockets=True,
        state_kind="stateful",
        cpu_bound=True,
    )
    hints = fp.as_legacy_hints()
    assert "uses-websockets" in hints
    assert "stateful" in hints
    assert "cpu-bound" in hints


def test_as_legacy_hints_preserves_existing_strings():
    fp = WorkloadFingerprint(
        project_name="demo",
        uses_websockets=True,
        legacy_hints=["has-worker", "fullstack"],
    )
    hints = fp.as_legacy_hints()
    assert hints[:2] == ["has-worker", "fullstack"]
    assert "uses-websockets" in hints


def test_dataclass_subobjects_construct():
    """Smoke test: every nested dataclass should construct with no required args
    aside from those declared mandatory."""
    Endpoint(path="/x", method="GET")
    BackgroundJob(name="reporter")
    DataAccessPattern(store="sql")
    SLO(p99_latency_ms=200, availability_pct=99.9)
    TrafficProfile(avg_rps=10, peak_rps=200)


def test_fingerprint_serialisable_to_dict():
    """The fingerprint must be JSON-safe (relevant for LangGraph state)."""
    import dataclasses
    fp = WorkloadFingerprint(
        project_name="demo",
        slo=SLO(p99_latency_ms=200),
        traffic=TrafficProfile(avg_rps=10),
        data_access=[DataAccessPattern(store="sql", has_transactions=True)],
    )
    d = dataclasses.asdict(fp)
    assert d["project_name"] == "demo"
    assert d["slo"]["p99_latency_ms"] == 200
    assert d["data_access"][0]["store"] == "sql"
