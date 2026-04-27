"""Shared runtime context for agent nodes.

Centralises the ContextVar used to inject the event emitter so that nodes
(analysis, proposal, generation, execution…) can all import from here
without creating cross-node dependencies.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Callable

# Callable emitter type: (event_dict) -> None
Emitter = Callable[[dict], None]
_noop: Emitter = lambda _: None  # noqa: E731

# cli.py injects the real emitter via _emitter_var.set() before streaming.
_emitter_var: ContextVar[Emitter] = ContextVar("_emitter", default=_noop)
