"""Inspect and resume persisted agent sessions (MEJORAS.md §1.2).

Reads the SQLite checkpoint database produced by
:func:`aetherdeploy.agent.graph.make_sqlite_checkpointer` and exposes the list
of past threads with their last known state.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .agent.graph import default_checkpoint_path


@dataclass
class ThreadRecord:
    thread_id: str
    last_step: str
    last_checkpoint_ts: str
    project_path: str | None
    requested_action: str | None


def _open(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(
            f"No checkpoint database at {path}. Run a deploy with --persist first."
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def list_threads(db_path: Path | str | None = None, limit: int = 50) -> list[ThreadRecord]:
    """List the most recent threads in the checkpoint DB.

    Returns the latest checkpoint per ``thread_id`` ordered by recency. Empty
    list if the table exists but holds no rows.
    """
    path = Path(db_path) if db_path else default_checkpoint_path()
    conn = _open(path)
    try:
        # Discover the checkpoints table — SqliteSaver creates "checkpoints".
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='checkpoints'"
        ).fetchall()
        if not rows:
            return []
        # Latest checkpoint per thread.
        cur = conn.execute(
            """
            SELECT thread_id, checkpoint, metadata
            FROM checkpoints
            WHERE (thread_id, checkpoint_id) IN (
                SELECT thread_id, MAX(checkpoint_id) FROM checkpoints GROUP BY thread_id
            )
            ORDER BY checkpoint_id DESC
            LIMIT ?
            """,
            (limit,),
        )
        out: list[ThreadRecord] = []
        for row in cur.fetchall():
            checkpoint_blob = row["checkpoint"]
            metadata_blob = row["metadata"]
            last_step, project_path, requested_action, ts = _extract_summary(
                checkpoint_blob, metadata_blob
            )
            out.append(
                ThreadRecord(
                    thread_id=row["thread_id"],
                    last_step=last_step,
                    last_checkpoint_ts=ts,
                    project_path=project_path,
                    requested_action=requested_action,
                )
            )
        return out
    finally:
        conn.close()


def _extract_summary(checkpoint_blob: bytes | None, metadata_blob: bytes | None) -> tuple[str, str | None, str | None, str]:
    """Best-effort extraction of human-friendly fields from msgpack-encoded state.

    Falls back to ``"unknown"`` if the blob can't be decoded. We don't fail on
    decode errors — listing should remain useful even if a row is malformed.
    """
    try:
        import msgpack  # langgraph dependency, always present at runtime
    except ImportError:
        return ("unknown", None, None, "")

    def _safe_unpack(blob: bytes | None) -> dict:
        if not blob:
            return {}
        try:
            return msgpack.unpackb(blob, raw=False) or {}
        except Exception:
            return {}

    checkpoint = _safe_unpack(checkpoint_blob)
    metadata = _safe_unpack(metadata_blob)

    ts = checkpoint.get("ts") if isinstance(checkpoint, dict) else ""
    channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}

    last_step = "unknown"
    project_path = None
    requested_action = None
    if isinstance(channel_values, dict):
        last_step = channel_values.get("current_step", "unknown") or "unknown"
        project_path = channel_values.get("project_path")
        requested_action = channel_values.get("requested_action")

    if last_step == "unknown" and isinstance(metadata, dict):
        last_step = metadata.get("step", "unknown") or "unknown"

    return (str(last_step), project_path, requested_action, str(ts or ""))
