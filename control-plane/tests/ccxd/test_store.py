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


import sqlite3
import time
from pathlib import Path

import pytest

from ccx.ccxd.store import SqliteStore


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state.db"


def test_sqlite_store_creates_schema_on_open(db_path: Path) -> None:
    s = SqliteStore(db_path)
    s.close()
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    conn.close()
    names = {r[0] for r in rows}
    assert "sessions" in names
    assert "sessions_history" in names
    assert "schema_version" in names


def test_sqlite_store_upsert_get_remove(db_path: Path) -> None:
    s = SqliteStore(db_path)
    sess = _stub_session(session_id="s1", cwd="/x", tokens_in=10, tokens_out=5)
    s.upsert(sess)
    assert s.get("s1").session_id == "s1"
    assert s.count_active() == 1
    s.remove("s1")
    assert s.get("s1") is None
    assert s.count_active() == 0
    s.close()


def test_sqlite_store_persists_across_reopens(db_path: Path) -> None:
    s1 = SqliteStore(db_path)
    s1.upsert(_stub_session(session_id="s1", cwd="/x"))
    s1.close()

    s2 = SqliteStore(db_path)
    got = s2.get("s1")
    assert got is not None and got.session_id == "s1"
    s2.close()


def test_sqlite_store_remove_writes_to_history(db_path: Path) -> None:
    s = SqliteStore(db_path)
    s.upsert(_stub_session(session_id="s1", cwd="/x", tokens_in=42, tokens_out=21))
    s.remove("s1")
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT session_id, tokens_in, tokens_out FROM sessions_history WHERE session_id='s1'"
    ).fetchone()
    conn.close()
    assert row == ("s1", 42, 21)
    s.close()


def test_closed_today_returns_recently_ended(db_path: Path) -> None:
    s = SqliteStore(db_path)
    s.upsert(_stub_session(session_id="old", cwd="/x"))
    s.upsert(_stub_session(session_id="new", cwd="/y", tokens_in=100, tokens_out=50))
    # Force "old" into history with an old ended_at
    s.remove("old")
    s._conn.execute(
        "UPDATE sessions_history SET ended_at = ? WHERE session_id='old'",
        (time.time() - 86400 * 2,),  # 2 days ago
    )
    s.remove("new")  # ended_at = now
    midnight = time.time() - 86400  # 1 day ago — boundary
    closed = s.closed_today(midnight)
    sids = {x.session_id for x in closed}
    assert "new" in sids
    assert "old" not in sids
    s.close()


def test_tokens_for_period_aggregates(db_path: Path) -> None:
    s = SqliteStore(db_path)
    s.upsert(_stub_session(session_id="a", cwd="/x", tokens_in=10, tokens_out=2))
    s.upsert(_stub_session(session_id="b", cwd="/y", tokens_in=20, tokens_out=4))
    s.remove("a")
    s.remove("b")
    now = time.time()
    out = s.tokens_for_period(now - 60, now + 60)
    assert out == {"input": 30, "output": 6, "sessions": 2}
    s.close()
