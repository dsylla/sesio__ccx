"""Tests for ccx.ccxd.tailer — JsonlTailer registry."""
from __future__ import annotations

import json
from pathlib import Path

from ccx.ccxd.tailer import TailerRegistry
from ccx.ccxd.state import StateManager
from ccx.ccxd.store import MemoryStore


def _write(path: Path, *entries: dict) -> None:
    with path.open("a") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def test_apply_updates_session_tokens(tmp_path: Path) -> None:
    state = StateManager(MemoryStore())
    state.upsert_blank(session_id="s1", cwd=str(tmp_path))
    reg = TailerRegistry(state)

    p = tmp_path / "s1.jsonl"
    _write(p, {"sessionId": "s1", "type": "assistant", "message": {
        "model": "claude-sonnet-4-6",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }})
    events = reg.apply(p, "s1")

    s = state.store.get("s1")
    assert s.tokens_in == 10
    assert s.tokens_out == 5
    assert s.model == "claude-sonnet-4-6"
    assert any(e["event"] == "session.updated" for e in events)


def test_apply_is_incremental(tmp_path: Path) -> None:
    state = StateManager(MemoryStore())
    state.upsert_blank(session_id="s1", cwd=str(tmp_path))
    reg = TailerRegistry(state)
    p = tmp_path / "s1.jsonl"

    _write(p, {"sessionId": "s1", "type": "assistant", "message": {
        "usage": {"input_tokens": 10, "output_tokens": 5}}})
    reg.apply(p, "s1")
    _write(p, {"sessionId": "s1", "type": "assistant", "message": {
        "usage": {"input_tokens": 7, "output_tokens": 2}}})
    reg.apply(p, "s1")

    s = state.store.get("s1")
    # parse_deltas overwrites tokens_in/out with each delta — last wins per-message
    assert s.tokens_in == 7
    assert s.tokens_out == 2


def test_apply_unknown_session_creates_blank(tmp_path: Path) -> None:
    state = StateManager(MemoryStore())
    reg = TailerRegistry(state)
    p = tmp_path / "new.jsonl"
    _write(p, {"sessionId": "new", "type": "ai-title", "aiTitle": "Refactor X"})
    reg.apply(p, "new")
    s = state.store.get("new")
    assert s is not None
    assert s.summary == "Refactor X"


def test_apply_filters_subagent_path(tmp_path: Path) -> None:
    state = StateManager(MemoryStore())
    reg = TailerRegistry(state)
    sub_dir = tmp_path / "subagents"
    sub_dir.mkdir()
    p = sub_dir / "agent-1.jsonl"
    _write(p, {"sessionId": "s1", "type": "assistant", "message": {
        "usage": {"input_tokens": 99, "output_tokens": 88}}})
    events = reg.apply(p, "s1")
    assert events == []
    assert state.store.get("s1") is None
