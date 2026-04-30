"""TUI claude monitor for `ccxctl monitor tui`.

A single module: dataclass, both fetchers (local + ccx), pure render
helpers, and the rich.live polling loop. Pure functions are unit-tested
directly; the loop has a deterministic non-TTY single-frame path that's
also tested.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Literal


Source = Literal["local", "ccx"]


@dataclass(frozen=True)
class SessionRow:
    source: Source
    agent: str
    slug: str
    cwd: str
    pid: int | None
    uptime_seconds: float | None
    tokens_in: int
    tokens_out: int

    @classmethod
    def from_dict(cls, raw: dict, *, source: Source) -> "SessionRow":
        toks = raw.get("tokens_today") or {"input": 0, "output": 0}
        pid = raw.get("agent_pid") or raw.get("claude_pid")
        return cls(
            source=source,
            agent=str(raw.get("agent", "claude")),
            slug=str(raw.get("slug", "?")),
            cwd=str(raw.get("cwd", "?")),
            pid=int(pid) if pid is not None else None,
            uptime_seconds=(
                float(raw["uptime_seconds"])
                if raw.get("uptime_seconds") is not None
                else None
            ),
            tokens_in=int(toks.get("input", 0)),
            tokens_out=int(toks.get("output", 0)),
        )


from ccx.sessions import collect_sessions  # noqa: E402  (deliberate late import)


def fetch_local() -> list[SessionRow]:
    """Sessions on the local host. Reuses ccx.sessions.collect_sessions()."""
    return [SessionRow.from_dict(r, source="local") for r in collect_sessions()]
