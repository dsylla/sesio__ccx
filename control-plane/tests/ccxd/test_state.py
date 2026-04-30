"""Tests for ccx.ccxd.state — Session dataclass + StateManager."""
from __future__ import annotations

import time

import pytest

from ccx.ccxd.state import Session, StateManager
from ccx.ccxd.store import MemoryStore


def _make_session(sid: str = "ses-1", **kw) -> Session:
    defaults = dict(
        session_id=sid,
        cwd="/work/proj",
        pid=1000,
        model="claude-sonnet-4-20250514",
        summary="working on feature",
        tokens_in=500,
        tokens_out=200,
        last_subagent=None,
        subagent_in_flight=None,
        attention=None,
        last_activity_at=time.time(),
        started_at=time.time() - 300,
    )
    defaults.update(kw)
    return Session(**defaults)


class TestSession:
    def test_fields_present(self):
        s = _make_session()
        assert s.session_id == "ses-1"
        assert s.cwd == "/work/proj"
        assert s.pid == 1000
        assert s.model == "claude-sonnet-4-20250514"
        assert s.tokens_in == 500
        assert s.tokens_out == 200

    def test_to_dict_serializes_all_fields(self):
        s = _make_session(
            last_subagent={"tool_use_id": "tu_1", "subagent_type": "general-purpose",
                           "description": "writing code", "dispatched_at": 1700000000.0},
            attention={"kind": "blocking", "since": 1700000001.0},
        )
        d = s.to_dict()
        assert d["session_id"] == "ses-1"
        assert d["last_subagent"]["tool_use_id"] == "tu_1"
        assert d["attention"]["kind"] == "blocking"

    def test_optional_fields_default_to_none(self):
        s = Session(
            session_id="x", cwd="/tmp", pid=None, model=None,
            summary=None, tokens_in=0, tokens_out=0,
            last_subagent=None, subagent_in_flight=None,
            attention=None, last_activity_at=0.0, started_at=0.0,
        )
        assert s.pid is None
        assert s.model is None
        assert s.summary is None


class TestStateManager:
    def test_add_session_and_get(self):
        mgr = StateManager(MemoryStore())
        s = _make_session("abc")
        mgr.upsert(s)
        assert mgr.get("abc") is s

    def test_remove_session(self):
        mgr = StateManager(MemoryStore())
        mgr.upsert(_make_session("abc"))
        mgr.remove("abc")
        assert mgr.get("abc") is None

    def test_all_sessions(self):
        mgr = StateManager(MemoryStore())
        mgr.upsert(_make_session("a"))
        mgr.upsert(_make_session("b"))
        assert len(mgr.all()) == 2

    def test_update_fields(self):
        mgr = StateManager(MemoryStore())
        mgr.upsert(_make_session("a", tokens_in=100))
        mgr.update_fields("a", tokens_in=200, model="opus")
        s = mgr.get("a")
        assert s.tokens_in == 200
        assert s.model == "opus"

    def test_update_fields_missing_session_is_noop(self):
        mgr = StateManager(MemoryStore())
        mgr.update_fields("nope", tokens_in=999)  # no raise

    def test_snapshot_returns_dict_list(self):
        mgr = StateManager(MemoryStore())
        mgr.upsert(_make_session("a"))
        snap = mgr.snapshot()
        assert isinstance(snap, list)
        assert snap[0]["session_id"] == "a"
