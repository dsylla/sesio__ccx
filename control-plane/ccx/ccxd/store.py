"""Store protocol and MemoryStore (V1 in-memory backend).

V2 will add SqliteStore implementing the same protocol. No code outside
this file cares about the storage layer.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ccx.ccxd.state import Session


@runtime_checkable
class Store(Protocol):
    """Abstract session store — V1 is MemoryStore, V2 will be SqliteStore."""

    def upsert(self, session: "Session") -> None: ...
    def remove(self, session_id: str) -> None: ...
    def get(self, session_id: str) -> "Session | None": ...
    def all(self) -> list["Session"]: ...
    def count_active(self) -> int: ...
    def closed_today(self, since_epoch: float) -> list["Session"]: ...
    def tokens_for_period(self, start: float, end: float) -> dict: ...


class MemoryStore:
    """Dict-backed in-memory store. All operations are O(1) or O(n)."""

    def __init__(self) -> None:
        self._data: dict[str, "Session"] = {}

    def upsert(self, session: "Session") -> None:
        self._data[session.session_id] = session

    def remove(self, session_id: str) -> None:
        self._data.pop(session_id, None)

    def get(self, session_id: str) -> "Session | None":
        return self._data.get(session_id)

    def all(self) -> list["Session"]:
        return list(self._data.values())

    def count_active(self) -> int:
        return len(self._data)

    def closed_today(self, since_epoch: float) -> list["Session"]:
        """V1: no history tracking — always returns empty."""
        return []

    def tokens_for_period(self, start: float, end: float) -> dict:
        """V1: no period reporting — always returns empty dict."""
        return {}


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
