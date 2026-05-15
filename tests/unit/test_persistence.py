"""Tests for sqlite-backed agent persistence (MEJORAS.md §1.2)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aetherdeploy.agent import graph as graph_mod
from aetherdeploy.agent.graph import (
    _ENV_PERSIST_PATH,
    build_graph,
    default_checkpoint_path,
    make_sqlite_checkpointer,
)


def test_default_checkpoint_path_under_home():
    p = default_checkpoint_path()
    assert p.name == "agent.sqlite"
    assert p.parent.name == ".aetherdeploy"


def test_make_sqlite_checkpointer_creates_file(tmp_path: Path):
    db = tmp_path / "nested" / "agent.sqlite"
    saver = make_sqlite_checkpointer(db)
    assert db.exists()
    # The conn must be usable: a simple query should succeed.
    cur = saver.conn.execute("SELECT 1")
    assert cur.fetchone() == (1,)
    saver.conn.close()


def test_make_sqlite_checkpointer_in_memory():
    saver = make_sqlite_checkpointer(":memory:")
    assert saver.conn.execute("SELECT 1").fetchone() == (1,)
    saver.conn.close()


def test_build_graph_uses_passed_checkpointer(tmp_path: Path):
    saver = make_sqlite_checkpointer(tmp_path / "g.sqlite")
    g = build_graph(checkpointer=saver)
    # LangGraph stores the saver on the compiled graph.
    assert g.checkpointer is saver
    saver.conn.close()


def test_build_graph_reads_env_var_when_no_checkpointer(monkeypatch, tmp_path: Path):
    db = tmp_path / "env.sqlite"
    monkeypatch.setenv(_ENV_PERSIST_PATH, str(db))
    g = build_graph()
    assert db.exists()
    # Make sure the resulting saver is NOT the in-memory MemorySaver.
    from langgraph.checkpoint.memory import MemorySaver
    assert not isinstance(g.checkpointer, MemorySaver)


def test_build_graph_defaults_to_memory_saver_without_env(monkeypatch):
    monkeypatch.delenv(_ENV_PERSIST_PATH, raising=False)
    g = build_graph()
    from langgraph.checkpoint.memory import MemorySaver
    assert isinstance(g.checkpointer, MemorySaver)


def test_history_listing_empty_when_no_threads(tmp_path: Path):
    """A freshly created DB (no threads written) lists zero records."""
    from aetherdeploy.cli_history import list_threads

    db = tmp_path / "empty.sqlite"
    saver = make_sqlite_checkpointer(db)
    saver.conn.close()
    # The langgraph table may not exist until first checkpoint is written —
    # list_threads must handle that gracefully and return [].
    records = list_threads(db)
    assert records == []


def test_history_raises_when_db_absent(tmp_path: Path):
    from aetherdeploy.cli_history import list_threads
    with pytest.raises(FileNotFoundError):
        list_threads(tmp_path / "nonexistent.sqlite")


def test_persistence_survives_rebuild(tmp_path: Path):
    """Two build_graph() calls with the same DB path share state."""
    db = tmp_path / "shared.sqlite"
    saver1 = make_sqlite_checkpointer(db)
    g1 = build_graph(checkpointer=saver1)
    # Write a tiny state for thread "test-1"
    config = {"configurable": {"thread_id": "test-1"}}
    g1.update_state(config, {"current_step": "discovery"})
    saver1.conn.close()

    saver2 = make_sqlite_checkpointer(db)
    g2 = build_graph(checkpointer=saver2)
    snapshot = g2.get_state(config)
    assert snapshot is not None
    assert snapshot.values.get("current_step") == "discovery"
    saver2.conn.close()
