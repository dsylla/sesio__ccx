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
