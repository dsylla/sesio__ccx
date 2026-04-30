"""Tests for ccx.ccxd.hooks — hook payload parsing and state mutation."""
from __future__ import annotations

import time

import pytest

from ccx.ccxd.hooks import handle_hook, parse_hook_payload
from ccx.ccxd.state import Session, StateManager
from ccx.ccxd.store import MemoryStore


def _make_mgr() -> StateManager:
    return StateManager(MemoryStore())


def _make_session(sid: str = "ses-1", **kw) -> Session:
    defaults = dict(
        session_id=sid, cwd="/work/proj", pid=1000,
        model="claude-sonnet-4-20250514", summary="test",
        tokens_in=0, tokens_out=0,
        last_subagent=None, subagent_in_flight=None,
        attention=None, last_activity_at=time.time(),
        started_at=time.time() - 300,
    )
    defaults.update(kw)
    return Session(**defaults)


class TestParseHookPayload:
    def test_extracts_event_and_session_id(self):
        raw = {
            "event": "PreToolUse",
            "payload": {
                "hook_event_name": "PreToolUse",
                "session_id": "ses-1",
                "cwd": "/work/proj",
                "tool_name": "Task",
                "tool_input": {"description": "write tests"},
            },
        }
        parsed = parse_hook_payload(raw)
        assert parsed["event"] == "PreToolUse"
        assert parsed["session_id"] == "ses-1"
        assert parsed["tool_name"] == "Task"

    def test_falls_back_to_outer_event(self):
        raw = {"event": "Stop", "payload": {"session_id": "ses-2", "cwd": "/x"}}
        parsed = parse_hook_payload(raw)
        assert parsed["event"] == "Stop"

    def test_handles_malformed(self):
        parsed = parse_hook_payload({"garbage": True})
        assert parsed["event"] == "Unknown"


class TestHandleHook:
    def test_pretooluse_task_sets_subagent_in_flight(self):
        mgr = _make_mgr()
        mgr.upsert(_make_session("ses-1"))
        events = handle_hook(mgr, {
            "event": "PreToolUse",
            "payload": {
                "hook_event_name": "PreToolUse",
                "session_id": "ses-1",
                "cwd": "/work/proj",
                "tool_name": "Task",
                "tool_input": {"description": "refactoring", "tool_use_id": "tu_1"},
            },
        })
        s = mgr.get("ses-1")
        assert s.subagent_in_flight is not None
        assert s.subagent_in_flight["tool_use_id"] == "tu_1"
        assert "session.subagent_start" in [e["event"] for e in events]

    def test_posttooluse_task_clears_matching_subagent(self):
        mgr = _make_mgr()
        mgr.upsert(_make_session("ses-1", subagent_in_flight={
            "tool_use_id": "tu_1", "subagent_type": "general-purpose",
            "description": "refactoring", "dispatched_at": time.time(),
        }))
        events = handle_hook(mgr, {
            "event": "PostToolUse",
            "payload": {
                "hook_event_name": "PostToolUse",
                "session_id": "ses-1",
                "cwd": "/work/proj",
                "tool_name": "Task",
                "tool_input": {"tool_use_id": "tu_1"},
            },
        })
        s = mgr.get("ses-1")
        assert s.subagent_in_flight is None
        assert "session.subagent_end" in [e["event"] for e in events]

    def test_posttooluse_mismatched_id_is_noop(self):
        mgr = _make_mgr()
        mgr.upsert(_make_session("ses-1", subagent_in_flight={
            "tool_use_id": "tu_INNER", "subagent_type": "general-purpose",
            "description": "inner task", "dispatched_at": time.time(),
        }))
        events = handle_hook(mgr, {
            "event": "PostToolUse",
            "payload": {
                "hook_event_name": "PostToolUse",
                "session_id": "ses-1",
                "cwd": "/work/proj",
                "tool_name": "Task",
                "tool_input": {"tool_use_id": "tu_OUTER"},
            },
        })
        s = mgr.get("ses-1")
        # inner is still in flight — outer PostToolUse doesn't clear it
        assert s.subagent_in_flight is not None
        assert s.subagent_in_flight["tool_use_id"] == "tu_INNER"

    def test_notification_blocking_sets_attention(self):
        mgr = _make_mgr()
        mgr.upsert(_make_session("ses-1"))
        events = handle_hook(mgr, {
            "event": "Notification",
            "payload": {
                "hook_event_name": "Notification",
                "session_id": "ses-1",
                "cwd": "/work/proj",
                "notification_type": "permission_prompt",
            },
        })
        s = mgr.get("ses-1")
        assert s.attention is not None
        assert s.attention["kind"] == "blocking"
        assert "session.attention" in [e["event"] for e in events]

    def test_notification_idle_sets_attention_idle(self):
        mgr = _make_mgr()
        mgr.upsert(_make_session("ses-1"))
        handle_hook(mgr, {
            "event": "Notification",
            "payload": {
                "hook_event_name": "Notification",
                "session_id": "ses-1",
                "cwd": "/work/proj",
                "notification_type": "idle_prompt",
            },
        })
        s = mgr.get("ses-1")
        assert s.attention["kind"] == "idle"

    def test_notification_noise_ignored(self):
        mgr = _make_mgr()
        mgr.upsert(_make_session("ses-1"))
        events = handle_hook(mgr, {
            "event": "Notification",
            "payload": {
                "hook_event_name": "Notification",
                "session_id": "ses-1",
                "cwd": "/work/proj",
                "notification_type": "auth_success",
            },
        })
        s = mgr.get("ses-1")
        assert s.attention is None
        assert events == []

    def test_stop_clears_attention(self):
        mgr = _make_mgr()
        mgr.upsert(_make_session("ses-1", attention={"kind": "blocking", "since": 1.0}))
        handle_hook(mgr, {
            "event": "Stop",
            "payload": {"hook_event_name": "Stop", "session_id": "ses-1", "cwd": "/x"},
        })
        s = mgr.get("ses-1")
        assert s.attention is None

    def test_user_prompt_submit_clears_attention(self):
        mgr = _make_mgr()
        mgr.upsert(_make_session("ses-1", attention={"kind": "idle", "since": 1.0}))
        handle_hook(mgr, {
            "event": "UserPromptSubmit",
            "payload": {"hook_event_name": "UserPromptSubmit", "session_id": "ses-1", "cwd": "/x"},
        })
        s = mgr.get("ses-1")
        assert s.attention is None

    def test_session_start_seeds_stub_if_unknown(self):
        mgr = _make_mgr()
        events = handle_hook(mgr, {
            "event": "SessionStart",
            "payload": {
                "hook_event_name": "SessionStart",
                "session_id": "new-ses",
                "cwd": "/work/new",
            },
        })
        s = mgr.get("new-ses")
        assert s is not None
        assert s.cwd == "/work/new"
        assert "session.added" in [e["event"] for e in events]

    def test_updates_last_activity(self):
        mgr = _make_mgr()
        old_time = 1000.0
        mgr.upsert(_make_session("ses-1", last_activity_at=old_time))
        handle_hook(mgr, {
            "event": "SubagentStop",
            "payload": {"hook_event_name": "SubagentStop", "session_id": "ses-1", "cwd": "/x"},
        })
        s = mgr.get("ses-1")
        assert s.last_activity_at > old_time
