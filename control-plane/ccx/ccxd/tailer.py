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
