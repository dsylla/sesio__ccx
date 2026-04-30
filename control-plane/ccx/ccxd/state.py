"""Session dataclass and StateManager — in-memory session index.

The StateManager wraps a Store and provides higher-level mutation methods.
Mutations are synchronous (no awaits) because the store is in-memory (V1).
The server layer is responsible for broadcasting changes after mutations.

Nested subagents: Claude Code can dispatch Task -> sub-Task -> sub-sub-Task.
We track **deepest in-flight only**: each PreToolUse(Task) overwrites
`subagent_in_flight`; PostToolUse(Task) clears it only if the tool_use_id
matches. The TUI shows "deepest active subagent" which is what users want.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ccx.ccxd.store import Store


@dataclass
class Session:
    """One running Claude Code session on this host."""

    session_id: str
    cwd: str
    pid: int | None
    model: str | None
    summary: str | None
    tokens_in: int
    tokens_out: int
    last_subagent: dict | None
    subagent_in_flight: dict | None
    attention: dict | None
    last_activity_at: float
    started_at: float

    def to_dict(self) -> dict:
        return asdict(self)


class StateManager:
    """High-level mutations over the Store."""

    def __init__(self, store: "Store") -> None:
        self._store = store

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

    def upsert(self, session: Session) -> None:
        self._store.upsert(session)

    def remove(self, session_id: str) -> None:
        self._store.remove(session_id)

    def get(self, session_id: str) -> Session | None:
        return self._store.get(session_id)

    def all(self) -> list[Session]:
        return self._store.all()

    def update_fields(self, session_id: str, **fields) -> Session | None:
        """Update specific fields on an existing session. Returns updated or None."""
        existing = self._store.get(session_id)
        if existing is None:
            return None
        updated = replace(existing, **fields)
        self._store.upsert(updated)
        return updated

    def snapshot(self) -> list[dict]:
        """Serialized snapshot of all sessions for API responses."""
        return [s.to_dict() for s in self._store.all()]

    def count_active(self) -> int:
        return self._store.count_active()
