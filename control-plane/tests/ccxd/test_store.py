"""Tests for ccx.ccxd.store — MemoryStore and Store protocol."""
from __future__ import annotations

import pytest

from ccx.ccxd.store import MemoryStore, Store


def _stub_session(sid: str = "ses-1", **kw):
    """Import Session here to avoid circular; minimal construction."""
    from ccx.ccxd.state import Session
    defaults = dict(
        session_id=sid,
        cwd="/work/proj",
        pid=1000,
        model="claude-sonnet-4-20250514",
        summary="test session",
        tokens_in=100,
        tokens_out=50,
        last_subagent=None,
        subagent_in_flight=None,
        attention=None,
        last_activity_at=1700000000.0,
        started_at=1699999000.0,
    )
    defaults.update(kw)
    return Session(**defaults)


class TestMemoryStore:
    def test_implements_store_protocol(self):
        store = MemoryStore()
        assert isinstance(store, Store)

    def test_upsert_and_get(self):
        store = MemoryStore()
        s = _stub_session("abc")
        store.upsert(s)
        assert store.get("abc") is s

    def test_get_missing_returns_none(self):
        store = MemoryStore()
        assert store.get("nope") is None

    def test_remove(self):
        store = MemoryStore()
        store.upsert(_stub_session("abc"))
        store.remove("abc")
        assert store.get("abc") is None

    def test_remove_missing_is_noop(self):
        store = MemoryStore()
        store.remove("nope")  # no raise

    def test_all_returns_list(self):
        store = MemoryStore()
        store.upsert(_stub_session("a"))
        store.upsert(_stub_session("b"))
        all_s = store.all()
        assert len(all_s) == 2
        assert {s.session_id for s in all_s} == {"a", "b"}

    def test_count_active(self):
        store = MemoryStore()
        assert store.count_active() == 0
        store.upsert(_stub_session("x"))
        assert store.count_active() == 1

    def test_closed_today_returns_empty(self):
        store = MemoryStore()
        assert store.closed_today(0.0) == []

    def test_tokens_for_period_returns_empty(self):
        store = MemoryStore()
        assert store.tokens_for_period(0.0, 9999999999.0) == {}
