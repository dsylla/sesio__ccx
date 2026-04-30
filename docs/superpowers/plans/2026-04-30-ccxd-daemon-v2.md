# `ccxd` V2 Implementation Plan (Plan 2 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land V2 of `ccxd`: SQLite-backed persistent storage, working incremental jsonl-MODIFY application (V1 had a `pass` stub here), exposed history-query RPC methods (`history.closed_today`, `history.tokens_for_period`), and removal of the V1 underscore-alias back-compat shim in `ccx.sessions`.

**Architecture:** Same single-daemon shape as V1. `Store` protocol gains a real `SqliteStore` implementation alongside `MemoryStore`; `__main__.py` defaults to SqliteStore but `--memory-store` keeps the V1 behavior available for tests/dev. A new `tailer.py` module owns the per-jsonl-path `JsonlTailer` registry and applies MODIFY events to `StateManager`. The `api.py` handlers gain two read-only methods that hit the store's history APIs. No changes to wire protocol version (`protocol_version: 1` covers it via additive methods).

**Tech Stack:** Python 3.13+ stdlib `sqlite3` (no new deps), existing `inotify_simple` + `pytest-asyncio`. Tests: pytest as before; SQLite work uses tmp-dir DBs via fixtures.

**Working directory:** `/home/david/Work/sesio/sesio__ccx`

---

## File Structure

```
sesio__ccx/
├── control-plane/
│   ├── ccx/
│   │   ├── monitor_tui.py                # MODIFY: switch to public ccx.sessions names
│   │   ├── sessions.py                   # MODIFY: drop underscore aliases (back-compat ends)
│   │   └── ccxd/
│   │       ├── store.py                  # MODIFY: add SqliteStore alongside MemoryStore
│   │       ├── api.py                    # MODIFY: add history.closed_today, history.tokens_for_period
│   │       ├── tailer.py                 # CREATE: per-path JsonlTailer registry + apply
│   │       └── __main__.py               # MODIFY: SqliteStore default; wire tailer registry
│   └── tests/
│       ├── test_sessions.py              # MODIFY: drop tests for underscore aliases
│       └── ccxd/
│           ├── test_store.py             # MODIFY: add SqliteStore tests
│           ├── test_api.py               # MODIFY: add history-method tests
│           └── test_tailer.py            # CREATE
└── docs/superpowers/plans/2026-04-30-ccxd-daemon-v2.md   # this file
```

**Boundaries:**
- `tailer.py` only depends on `state.py`, `jsonl.py`, `inotify.py`. No store coupling.
- `SqliteStore` lives in the existing `store.py` and is interchangeable with `MemoryStore` via the `Store` protocol.
- `monitor_tui.py` is the only V1 consumer of `_project_jsonl_files` / `_process_uptime_seconds` underscore names; once migrated, the aliases come out.

---

## Prerequisites

- Plan 1 (V1 daemon) is shipped and on `main`. 74 ccxd tests + 170 total all green.
- `~/.local/share/ccxd/` will be created at first run by `SqliteStore` (or `$XDG_DATA_HOME/ccxd/` if set).
- Python `sqlite3` stdlib has been available since 2.5; no new dep.
- Plan 1 already removed the SIGTERM shutdown traceback (commit `c11e902`) — `_run` returns normally on signal.

---

### Task 1: tailer.py — JsonlTailer Registry + apply

**Why first:** Closes the V1 functional gap (`__main__._process_inotify` line ~155 has a `pass` for MODIFY events). Once landed, sessions actually update from raw jsonl writes, not just hook events.

**Files:**
- Create: `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/ccxd/tailer.py`
- Create: `/home/david/Work/sesio/sesio__ccx/control-plane/tests/ccxd/test_tailer.py`

- [ ] **Step 1: Write failing test**

Create `control-plane/tests/ccxd/test_tailer.py`:

```python
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
```

- [ ] **Step 2: Expose `state.store` and add `state.upsert_blank`**

V1's `StateManager` keeps the store as `self._store` (private). Tests and `tailer.py` need read access to the store, and we need a tiny "create with defaults" helper. Add both to `control-plane/ccx/ccxd/state.py`:

```python
@property
def store(self) -> "Store":
    """Read-only access to the underlying store."""
    return self._store

def upsert_blank(self, session_id: str, cwd: str, pid: int | None = None) -> None:
    """Create a Session with default fields if it doesn't exist."""
    if self._store.get(session_id) is not None:
        return
    import time
    self._store.upsert(Session(
        session_id=session_id, cwd=cwd, pid=pid,
        model=None, summary=None,
        tokens_in=0, tokens_out=0,
        last_subagent=None, subagent_in_flight=None, attention=None,
        last_activity_at=time.time(), started_at=time.time(),
    ))
```

- [ ] **Step 3: Run test to verify it fails (ImportError on tailer)**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/ccxd/test_tailer.py -v
```

Expected: ImportError or collection error — `TailerRegistry` doesn't exist yet.

- [ ] **Step 4: Implement tailer.py**

Create `control-plane/ccx/ccxd/tailer.py`:

```python
"""Per-jsonl-path JsonlTailer registry + delta application.

When inotify fires MODIFY on a session jsonl, the registry looks up
(or creates) the JsonlTailer for that path, reads new entries, runs
parse_deltas, and merges the result into StateManager.
Returns the broadcast events the server should fan out.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ccx.ccxd.jsonl import JsonlTailer, parse_deltas
from ccx.ccxd.state import StateManager

log = logging.getLogger("ccxd.tailer")


class TailerRegistry:
    """Owns one JsonlTailer per known session jsonl path."""

    def __init__(self, state: StateManager) -> None:
        self.state = state
        self._tailers: dict[Path, JsonlTailer] = {}

    def apply(self, path: Path, session_id: str) -> list[dict]:
        """Read new entries from `path`, merge into state, return broadcast events.

        Subagent-transcript paths (anything with `subagents` in it) are skipped —
        sidechain billings are accounted via the parent session's hook events.
        """
        if "subagents" in path.parts:
            return []

        tailer = self._tailers.get(path)
        if tailer is None:
            tailer = JsonlTailer(path)
            self._tailers[path] = tailer

        events: list[dict] = []
        for entry in tailer.read_new():
            deltas = parse_deltas(entry)
            if not deltas:
                continue
            if self.state.store.get(session_id) is None:
                # Bootstrap a blank session so updates have somewhere to land.
                self.state.upsert_blank(session_id=session_id, cwd=str(path.parent))
            self.state.update_fields(session_id, **deltas)
            events.append({
                "event": "session.updated",
                "data": {"session_id": session_id, **deltas},
            })
        return events

    def forget(self, path: Path) -> None:
        """Drop a tailer (e.g. on file delete). Idempotent."""
        self._tailers.pop(path, None)
```

- [ ] **Step 5: Run test — expect PASS**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/ccxd/test_tailer.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Wire tailer registry into __main__**

In `control-plane/ccx/ccxd/__main__.py`, replace the `pass` stub at the end of `_process_inotify` with a call to a new module-level `TailerRegistry`:

```python
# At top of file (with other ccxd imports)
from ccx.ccxd.tailer import TailerRegistry
```

In `_run`, after creating `state_mgr`, instantiate the registry and pass it to `_process_inotify`:

```python
tailer_registry = TailerRegistry(state_mgr)
...
loop.add_reader(watcher.fd, lambda: _process_inotify(watcher, state_mgr, server, tailer_registry))
```

Update `_process_inotify` signature and the MODIFY-handling block:

```python
def _process_inotify(watcher, state_mgr, server, tailer_registry) -> None:
    ...
    loop = asyncio.get_event_loop()
    for event in events:
        if event.mask & iflags.MODIFY:
            path = watcher.resolve_event_path(event)
            if not path or path.suffix != ".jsonl" or "subagents" in path.parts:
                continue
            session_id = path.stem
            for ev in tailer_registry.apply(path, session_id):
                loop.create_task(server.broadcast(ev))
```

(`server.broadcast` is async; from this sync inotify callback we schedule it on the running loop.)

- [ ] **Step 7: Run full ccxd suite — expect PASS**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/ccxd/ -q
```

Expected: 78 passed (74 prior + 4 tailer).

- [ ] **Step 8: Commit**

Use `/commit`. Message: `feat(ccxd): tailer registry — apply jsonl MODIFY events to state`

---

### Task 2: SqliteStore — schema + CRUD

**Files:**
- Modify: `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/ccxd/store.py`
- Modify: `/home/david/Work/sesio/sesio__ccx/control-plane/tests/ccxd/test_store.py`

- [ ] **Step 1: Append SqliteStore tests to test_store.py**

Add at the end of `control-plane/tests/ccxd/test_store.py`:

```python
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
```

(Reuse the `_stub_session` factory already defined at the top of `test_store.py`.)

- [ ] **Step 2: Run tests — expect ImportError on SqliteStore**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/ccxd/test_store.py -v
```

Expected: collection error — `SqliteStore` doesn't exist.

- [ ] **Step 3: Implement SqliteStore in store.py**

Append to `control-plane/ccx/ccxd/store.py`:

```python
import sqlite3
import time
from pathlib import Path

_SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    cwd TEXT NOT NULL,
    pid INTEGER,
    model TEXT,
    summary TEXT,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    last_subagent_json TEXT,
    subagent_in_flight_json TEXT,
    attention_json TEXT,
    last_activity_at REAL NOT NULL,
    started_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions_history (
    session_id TEXT NOT NULL,
    cwd TEXT NOT NULL,
    model TEXT,
    summary TEXT,
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_history_ended_at
    ON sessions_history(ended_at);
"""


class SqliteStore:
    """Persistent Store backed by SQLite at $XDG_DATA_HOME/ccxd/state.db.

    Active sessions live in `sessions`. Removal moves the row to
    `sessions_history` (preserving final tokens / summary / timing).
    Reads still go through a hot in-memory cache populated on open.
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)
        self._migrate()
        self._cache: dict[str, "Session"] = {}
        for row in self._conn.execute("SELECT * FROM sessions"):
            self._cache[row["session_id"]] = self._row_to_session(row)

    def _migrate(self) -> None:
        cur = self._conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        if cur is None:
            self._conn.execute("INSERT INTO schema_version(version) VALUES (?)", (_SCHEMA_VERSION,))
        # Future: bump _SCHEMA_VERSION and add ALTER TABLE branches here.

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> "Session":
        from ccx.ccxd.state import Session
        import json as _json
        def _j(s): return _json.loads(s) if s else None
        return Session(
            session_id=row["session_id"], cwd=row["cwd"], pid=row["pid"],
            model=row["model"], summary=row["summary"],
            tokens_in=row["tokens_in"], tokens_out=row["tokens_out"],
            last_subagent=_j(row["last_subagent_json"]),
            subagent_in_flight=_j(row["subagent_in_flight_json"]),
            attention=_j(row["attention_json"]),
            last_activity_at=row["last_activity_at"], started_at=row["started_at"],
        )

    @staticmethod
    def _session_to_params(s: "Session") -> tuple:
        import json as _json
        def _j(o): return _json.dumps(o) if o is not None else None
        return (
            s.session_id, s.cwd, s.pid, s.model, s.summary,
            s.tokens_in, s.tokens_out,
            _j(s.last_subagent), _j(s.subagent_in_flight), _j(s.attention),
            s.last_activity_at, s.started_at,
        )

    def upsert(self, session: "Session") -> None:
        self._conn.execute("""
            INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET
                cwd=excluded.cwd, pid=excluded.pid, model=excluded.model,
                summary=excluded.summary, tokens_in=excluded.tokens_in,
                tokens_out=excluded.tokens_out,
                last_subagent_json=excluded.last_subagent_json,
                subagent_in_flight_json=excluded.subagent_in_flight_json,
                attention_json=excluded.attention_json,
                last_activity_at=excluded.last_activity_at,
                started_at=excluded.started_at
        """, self._session_to_params(session))
        self._cache[session.session_id] = session

    def remove(self, session_id: str) -> None:
        sess = self._cache.pop(session_id, None)
        if sess is None:
            return
        self._conn.execute(
            "INSERT INTO sessions_history(session_id, cwd, model, summary, "
            "tokens_in, tokens_out, started_at, ended_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (sess.session_id, sess.cwd, sess.model, sess.summary,
             sess.tokens_in, sess.tokens_out, sess.started_at, time.time()),
        )
        self._conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))

    def get(self, session_id: str) -> "Session | None":
        return self._cache.get(session_id)

    def all(self) -> list["Session"]:
        return list(self._cache.values())

    def count_active(self) -> int:
        return len(self._cache)

    def closed_today(self, since_epoch: float) -> list["Session"]:
        # Implemented in Task 3.
        return []

    def tokens_for_period(self, start: float, end: float) -> dict:
        # Implemented in Task 3.
        return {}

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run test_store — expect PASS**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/ccxd/test_store.py -v
```

Expected: 13 passed (9 prior MemoryStore + 4 SqliteStore).

- [ ] **Step 5: Commit**

Use `/commit`. Message: `feat(ccxd): SqliteStore — schema, migration, CRUD`

---

### Task 3: SqliteStore — history queries

**Files:**
- Modify: `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/ccxd/store.py`
- Modify: `/home/david/Work/sesio/sesio__ccx/control-plane/tests/ccxd/test_store.py`

- [ ] **Step 1: Add history-query tests**

Append to `control-plane/tests/ccxd/test_store.py`:

```python
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
```

- [ ] **Step 2: Run — expect 2 failures (return [] / {} stubs)**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/ccxd/test_store.py -v
```

Expected: 2 failed, 13 passed.

- [ ] **Step 3: Implement history queries in SqliteStore**

Replace the two stub methods in `control-plane/ccx/ccxd/store.py`:

```python
    def closed_today(self, since_epoch: float) -> list["Session"]:
        from ccx.ccxd.state import Session
        rows = self._conn.execute(
            "SELECT session_id, cwd, model, summary, tokens_in, tokens_out, "
            "started_at, ended_at FROM sessions_history "
            "WHERE ended_at >= ? ORDER BY ended_at DESC",
            (since_epoch,),
        ).fetchall()
        out: list[Session] = []
        for r in rows:
            out.append(Session(
                session_id=r["session_id"], cwd=r["cwd"], pid=None,
                model=r["model"], summary=r["summary"],
                tokens_in=r["tokens_in"], tokens_out=r["tokens_out"],
                last_subagent=None, subagent_in_flight=None, attention=None,
                last_activity_at=r["ended_at"], started_at=r["started_at"],
            ))
        return out

    def tokens_for_period(self, start: float, end: float) -> dict:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(tokens_in), 0) AS i, "
            "COALESCE(SUM(tokens_out), 0) AS o, "
            "COUNT(*) AS n FROM sessions_history "
            "WHERE ended_at >= ? AND ended_at < ?",
            (start, end),
        ).fetchone()
        return {"input": row["i"], "output": row["o"], "sessions": row["n"]}
```

- [ ] **Step 4: Run — expect PASS**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/ccxd/test_store.py -v
```

Expected: 15 passed.

- [ ] **Step 5: Commit**

Use `/commit`. Message: `feat(ccxd): SqliteStore history queries — closed_today + tokens_for_period`

---

### Task 4: Expose history via new RPC methods

**Files:**
- Modify: `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/ccxd/api.py`
- Modify: `/home/david/Work/sesio/sesio__ccx/control-plane/tests/ccxd/test_api.py`

- [ ] **Step 1: Add api tests for the two new methods**

Append to `control-plane/tests/ccxd/test_api.py` (the existing test file uses `handle_rpc(mgr, msg)` and a `_make_mgr_with_session()` helper at the top — reuse that pattern):

```python
def test_history_closed_today_dispatches_to_store():
    mgr = _make_mgr_with_session()
    resp = handle_rpc(mgr, {
        "id": 9, "method": "history.closed_today",
        "params": {"since_epoch": 1000.0},
    })
    assert resp["id"] == 9
    assert resp["result"] == {"sessions": []}  # MemoryStore returns []


def test_history_tokens_for_period_dispatches_to_store():
    mgr = _make_mgr_with_session()
    resp = handle_rpc(mgr, {
        "id": 10, "method": "history.tokens_for_period",
        "params": {"start": 1000.0, "end": 2000.0},
    })
    assert resp["id"] == 10
    assert resp["result"] == {}  # MemoryStore returns {}


def test_history_method_validates_params():
    mgr = _make_mgr_with_session()
    resp = handle_rpc(mgr, {
        "id": 11, "method": "history.closed_today", "params": {},
    })
    assert resp["error"]["code"] == "invalid_params"
```

- [ ] **Step 2: Run — expect failures (unknown method)**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/ccxd/test_api.py -v
```

Expected: 3 new tests fail (the assertions check `resp["result"]` / `resp["error"]["code"]` which won't match the `unknown_method` error).

- [ ] **Step 3: Add the handlers in api.py**

In `control-plane/ccx/ccxd/api.py`, add two new module-level helpers and two `elif` branches in `handle_rpc`. After `_handle_unsubscribe`, add:

```python
def _handle_history_closed_today(msg_id, mgr: "StateManager", params: dict) -> dict:
    since = params.get("since_epoch")
    if not isinstance(since, (int, float)):
        return {
            "id": msg_id,
            "error": {"code": "invalid_params",
                      "message": "history.closed_today requires numeric 'since_epoch'"},
        }
    sessions = mgr.store.closed_today(float(since))
    return {
        "id": msg_id,
        "result": {"sessions": [s.__dict__ for s in sessions]},
    }


def _handle_history_tokens_for_period(msg_id, mgr: "StateManager", params: dict) -> dict:
    start = params.get("start")
    end = params.get("end")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return {
            "id": msg_id,
            "error": {"code": "invalid_params",
                      "message": "history.tokens_for_period requires numeric 'start' and 'end'"},
        }
    return {"id": msg_id, "result": mgr.store.tokens_for_period(float(start), float(end))}
```

Add the elif branches in `handle_rpc` (between the `unsubscribe` branch and the `unknown_method` else):

```python
    elif method == "history.closed_today":
        return _handle_history_closed_today(msg_id, mgr, params)
    elif method == "history.tokens_for_period":
        return _handle_history_tokens_for_period(msg_id, mgr, params)
```

- [ ] **Step 4: Run — expect PASS**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/ccxd/test_api.py -v
```

Expected: 11 passed (8 prior + 3 new).

- [ ] **Step 5: Commit**

Use `/commit`. Message: `feat(ccxd): RPC history.closed_today + history.tokens_for_period`

---

### Task 5: Wire SqliteStore as default in __main__

**Files:**
- Modify: `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/ccxd/__main__.py`
- Modify: `/home/david/Work/sesio/sesio__ccx/control-plane/tests/ccxd/test_main.py`

- [ ] **Step 1: Add CLI flag test**

Append to `control-plane/tests/ccxd/test_main.py`:

```python
def test_main_default_picks_sqlite_store(monkeypatch, tmp_path):
    """Without --memory-store, __main__ should pick SqliteStore."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from ccx.ccxd.__main__ import _select_store
    store = _select_store(memory=False)
    from ccx.ccxd.store import SqliteStore
    assert isinstance(store, SqliteStore)
    store.close()


def test_main_memory_store_flag(monkeypatch):
    from ccx.ccxd.__main__ import _select_store
    store = _select_store(memory=True)
    from ccx.ccxd.store import MemoryStore
    assert isinstance(store, MemoryStore)
```

- [ ] **Step 2: Run — expect ImportError on _select_store**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/ccxd/test_main.py -v
```

Expected: collection error.

- [ ] **Step 3: Update __main__.py**

In `control-plane/ccx/ccxd/__main__.py`:

1. Add a CLI flag:

```python
parser.add_argument(
    "--memory-store", action="store_true",
    help="Use in-memory store (V1 behavior; non-persistent).",
)
```

2. Add the `_select_store` helper:

```python
def _select_store(memory: bool):
    if memory:
        return MemoryStore()
    from ccx.ccxd.store import SqliteStore
    data_home = Path(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")))
    return SqliteStore(data_home / "ccxd" / "state.db")
```

3. In `_run`, replace `store = MemoryStore()` with `store = _select_store(args.memory_store)`.

- [ ] **Step 4: Run — expect PASS**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/ccxd/test_main.py -v
```

Expected: 5 passed (3 prior + 2 new).

- [ ] **Step 5: Smoke run**

```bash
rm -rf /tmp/ccxd-v2 && mkdir -p /tmp/ccxd-v2
XDG_RUNTIME_DIR=/tmp/ccxd-v2 XDG_DATA_HOME=/tmp/ccxd-v2 \
  CCXD_SKIP_DISCOVERY=1 timeout 2 \
  /usr/bin/uv run python -m ccx.ccxd --log-level info 2>&1 | head -10 || true
ls -la /tmp/ccxd-v2/ccxd/
```

Expected: `state.db` file is present in `/tmp/ccxd-v2/ccxd/`.

- [ ] **Step 6: Commit**

Use `/commit`. Message: `feat(ccxd): SqliteStore is the default store; --memory-store opt-in`

---

### Task 6: Drop V1 underscore-alias back-compat

**Why last:** Plan 1 deferred this. After this task, ccxd is no longer pulling double duty supporting the old `monitor_tui` import shape.

**Files:**
- Modify: `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/sessions.py`
- Modify: `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/monitor_tui.py`
- Modify: `/home/david/Work/sesio/sesio__ccx/control-plane/tests/test_sessions.py`

- [ ] **Step 1: Audit consumers of the underscore names**

```bash
cd /home/david/Work/sesio/sesio__ccx && \
  grep -rn "_project_jsonl_files\|_process_uptime_seconds" control-plane/ \
  | grep -v __pycache__ | grep -v "test_sessions.py:.*aliases"
```

Expected: only `monitor_tui.py` and `sessions.py` itself reference them.

- [ ] **Step 2: Migrate monitor_tui.py to public names**

In `control-plane/ccx/monitor_tui.py`, replace every `sessions._project_jsonl_files(...)` with `sessions.project_jsonl_files(...)` and every `sessions._process_uptime_seconds(...)` with `sessions.process_uptime_seconds(...)`.

- [ ] **Step 3: Remove the alias lines from sessions.py**

Delete these two lines from `control-plane/ccx/sessions.py` (added in Plan 1 Task 1):

```python
project_jsonl_files = _project_jsonl_files
process_uptime_seconds = _process_uptime_seconds
```

Then rename the underscore functions to drop the prefix:

```python
def process_uptime_seconds(pid: int) -> float | None:
    ...
def project_jsonl_files(cwd: str) -> list[Path]:
    ...
```

Update internal call sites within `sessions.py` (`_usage_for_agent`, `collect_sessions`) accordingly.

- [ ] **Step 4: Drop the now-stale alias test**

In `control-plane/tests/test_sessions.py`, remove `test_promoted_helpers_are_importable`.

- [ ] **Step 5: Run full ccx suite — expect PASS**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/ -q
```

Expected: 168 passed (170 prior - 1 dropped alias test - 1 dropped duplicate, ± Task 1-5 additions = ~178).

- [ ] **Step 6: Commit**

Use `/commit`. Message: `refactor(ccx sessions): drop underscore-aliased back-compat shim (V2)`

---

## Self-Review Checklist

After completing all tasks, verify:

- [ ] **tailer.py:** Per-path JsonlTailer registry; subagents/ paths skipped; bootstraps blank session if unknown.
- [ ] **inotify wiring:** `_process_inotify` no longer has a `pass` stub — MODIFY events drive `tailer_registry.apply` and broadcast `session.updated`.
- [ ] **SqliteStore schema:** `sessions`, `sessions_history`, `schema_version` tables created on open. `_SCHEMA_VERSION = 1`.
- [ ] **SqliteStore CRUD:** in-memory cache populated on open; `upsert` writes through; `remove` moves the row to `sessions_history` with `ended_at = now`.
- [ ] **History queries:** `closed_today(since_epoch)` returns a list of `Session`s ordered by `ended_at DESC`; `tokens_for_period(start, end)` returns `{"input", "output", "sessions"}`.
- [ ] **api.py:** Two new methods (`history.closed_today`, `history.tokens_for_period`) reachable through `query`-style RPC. Param validation returns `invalid_params`.
- [ ] **__main__.py:** `--memory-store` flag exists; default is `SqliteStore` at `$XDG_DATA_HOME/ccxd/state.db`.
- [ ] **Back-compat removed:** No `_project_jsonl_files` / `_process_uptime_seconds` references survive.
- [ ] **Type consistency:** Session field types unchanged from V1 (still `int | None`, `str | None`, etc.).
- [ ] **No new deps:** Stdlib `sqlite3` only.
- [ ] **Tests:** All ccxd tests pass; full repo suite passes; SqliteStore tests use `tmp_path` (no real `~/.local/share` writes).
- [ ] **No V3 scope:** No Codex agent support (deferred), no rate-limit ingestion (deferred), no widget push (deferred).
