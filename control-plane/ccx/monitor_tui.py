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


def fetch_ccx(
    *, ssh_user: str, hostname: str, ssh_key: str, timeout: float = 5.0
) -> list[SessionRow]:
    """Sessions on the ccx host, via `ssh ... ccxctl session list --json`.

    Failure modes (ssh down, unreachable, timeout, non-zero exit, garbage
    stdout) all return [] — the loop keeps drawing local rows and renders
    ccx as `(unreachable)` via the render layer's `unreachable_sources`
    argument.
    """
    cmd = [
        "ssh",
        "-i", ssh_key,
        "-o", "ConnectTimeout=3",
        "-o", "BatchMode=yes",
        "-o", "ControlMaster=auto",
        "-o", "ControlPath=~/.ssh/cm-%r@%h:%p",
        "-o", "ControlPersist=120",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=2",
        f"{ssh_user}@{hostname}",
        "ccxctl", "session", "list", "--json",
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return []
    if r.returncode != 0:
        return []
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [SessionRow.from_dict(d, source="ccx") for d in data if isinstance(d, dict)]


from pathlib import Path
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _fmt_uptime(secs: float | None) -> str:
    if secs is None:
        return "-"
    if secs < 60:
        return f"{int(secs)}s"
    if secs < 3600:
        return f"{int(secs // 60)}m"
    return f"{int(secs // 3600)}h{int((secs % 3600) // 60)}m"


_DEFAULT_RATE_LIMITS_FILE = Path.home() / ".cache" / "claude_status" / "state.json"


def load_rate_limits(path: Path | None = None) -> dict | None:
    """Read 5h/7d Anthropic rate-limit windows from state.json. None on miss."""
    p = path or _DEFAULT_RATE_LIMITS_FILE
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("rate_limits") or None


def _rate_limit_line(rl: dict) -> Text:
    parts: list[str] = []
    fh = rl.get("five_hour") or {}
    sd = rl.get("seven_day") or {}
    if "used_percentage" in fh:
        parts.append(f"5h {fh['used_percentage']:.0f}%")
    if "used_percentage" in sd:
        parts.append(f"7d {sd['used_percentage']:.0f}%")
    return Text(" · ".join(parts), style="dim")


def build_panel(
    rows: list[SessionRow],
    *,
    unreachable_sources: list[str] | None = None,
    rate_limits: dict | None = None,
) -> Panel:
    """Compose the full TUI frame: table + (optional) rate-limit footer.

    Tokens are aggregated per-cwd, not per-pid — see help caption.
    """
    table = Table(
        show_header=True,
        header_style="bold cyan",
        expand=True,
        caption="(tokens are per-cwd, not per-pid)",
        caption_style="dim",
    )
    table.add_column("SOURCE", style="dim", width=8)
    table.add_column("AGENT", width=8)
    table.add_column("SLUG", overflow="fold")
    table.add_column("PID", justify="right", width=8)
    table.add_column("UPTIME", justify="right", width=8)
    table.add_column("IN", justify="right", width=8)
    table.add_column("OUT", justify="right", width=8)
    table.add_column("CWD", overflow="fold")

    if not rows and not (unreachable_sources or []):
        table.caption = "no sessions"

    for r in rows:
        src_style = "green" if r.source == "local" else "magenta"
        table.add_row(
            Text(r.source, style=src_style),
            r.agent,
            r.slug,
            str(r.pid) if r.pid else "-",
            _fmt_uptime(r.uptime_seconds),
            _fmt_tokens(r.tokens_in),
            _fmt_tokens(r.tokens_out),
            r.cwd,
        )

    for src in unreachable_sources or []:
        table.add_row(
            Text(src, style="red"),
            "-", "(unreachable)", "-", "-", "-", "-", "-",
        )

    body = [table]
    if rate_limits:
        body.append(_rate_limit_line(rate_limits))
    return Panel(
        Group(*body),
        title="agent monitor — q quit · r refresh · f cycle filter",
        title_align="left",
        border_style="cyan",
    )
