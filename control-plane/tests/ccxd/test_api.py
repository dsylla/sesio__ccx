"""Tests for ccx.ccxd.api — RPC method handlers."""
from __future__ import annotations

import time

import pytest

from ccx.ccxd.api import PROTOCOL_VERSION, handle_rpc
from ccx.ccxd.state import Session, StateManager
from ccx.ccxd.store import MemoryStore


def _make_mgr_with_session() -> StateManager:
    mgr = StateManager(MemoryStore())
    mgr.upsert(Session(
        session_id="ses-1", cwd="/work/proj", pid=1000,
        model="claude-sonnet-4-20250514", summary="working",
        tokens_in=500, tokens_out=200,
        last_subagent=None, subagent_in_flight=None,
        attention=None, last_activity_at=time.time(),
        started_at=time.time() - 300,
    ))
    return mgr


class TestHandleRpc:
    def test_query_returns_sessions_with_protocol_version(self):
        mgr = _make_mgr_with_session()
        response = handle_rpc(mgr, {"id": 1, "method": "query", "params": {}})
        assert response["id"] == 1
        result = response["result"]
        assert result["protocol_version"] == PROTOCOL_VERSION
        assert len(result["sessions"]) == 1
        assert result["sessions"][0]["session_id"] == "ses-1"

    def test_subscribe_returns_sub_id(self):
        mgr = _make_mgr_with_session()
        response = handle_rpc(mgr, {
            "id": 2, "method": "subscribe",
            "params": {"events": ["session.*"]},
        })
        assert response["id"] == 2
        assert "sub_id" in response["result"]

    def test_subscribe_unknown_event_glob_errors(self):
        mgr = _make_mgr_with_session()
        response = handle_rpc(mgr, {
            "id": 3, "method": "subscribe",
            "params": {"events": ["nope.*"]},
        })
        assert "error" in response
        assert response["error"]["code"] == "unknown_event_glob"

    def test_unsubscribe_returns_ok(self):
        mgr = _make_mgr_with_session()
        # First subscribe
        sub_resp = handle_rpc(mgr, {
            "id": 2, "method": "subscribe",
            "params": {"events": ["session.*"]},
        })
        sub_id = sub_resp["result"]["sub_id"]
        # Then unsubscribe
        response = handle_rpc(mgr, {
            "id": 3, "method": "unsubscribe",
            "params": {"sub_id": sub_id},
        })
        assert response["id"] == 3
        assert response["result"] == {"ok": True}

    def test_unknown_method_error(self):
        mgr = _make_mgr_with_session()
        response = handle_rpc(mgr, {"id": 99, "method": "bogus", "params": {}})
        assert response["error"]["code"] == "unknown_method"

    def test_missing_id_still_returns_error(self):
        mgr = _make_mgr_with_session()
        response = handle_rpc(mgr, {"method": "bogus", "params": {}})
        assert response.get("id") is None
        assert "error" in response

    def test_query_empty_state(self):
        mgr = StateManager(MemoryStore())
        response = handle_rpc(mgr, {"id": 1, "method": "query", "params": {}})
        assert response["result"]["sessions"] == []
        assert response["result"]["protocol_version"] == PROTOCOL_VERSION

    def test_valid_event_globs(self):
        """All session.* globs are accepted."""
        mgr = _make_mgr_with_session()
        for glob in ["session.*", "session.added", "session.updated",
                     "session.removed", "session.attention",
                     "session.subagent_start", "session.subagent_end"]:
            resp = handle_rpc(mgr, {
                "id": 1, "method": "subscribe",
                "params": {"events": [glob]},
            })
            assert "result" in resp, f"Failed for glob: {glob}"
