# TUI Claude Monitor (`ccxctl monitor tui`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `ccxctl monitor tui` command that renders a live, terminal-only dashboard of Claude Code sessions running on the local laptop and/or the ccx EC2 box, with per-session uptime, token usage, and last activity timestamp — polling-based, no hooks, single-window rich.live.

**Architecture:** A single `ccx/monitor_tui.py` module owns the dataclass, both fetchers, the table builder, and the loop — flat, ~250 LOC. `SessionRow` is the common shape for local + ccx rows; the renderer doesn't care which produced which. Local fetch reuses `ccx.sessions.collect_sessions()`. Remote fetch shells out to `ssh -o ControlMaster=auto -o ControlPersist=120 david@ccx ccxctl session list --json`, so 12 polls/minute become 1 TCP connection serving ~24 polls. Rendering uses `rich.live.Live` with a `rich.table.Table` plus a footer line reading `~/.cache/claude_status/state.json` for 5h/7d Anthropic rate-limit windows. Stdin is read in raw mode via `termios + select` for a small set of keys (`q` quit, `r` refresh-now, `f` cycle filter both→local→remote→both). Termios restore is registered via `atexit` **and** a SIGTERM/SIGHUP handler, so a crash mid-Live doesn't strand the user in cbreak + alt-screen. No mouse, no panes, no popup.

**Tech Stack:** Python 3.13+, Typer (matches existing `ccxctl` shape), Rich 13+ (already a dep), stdlib `subprocess`/`select`/`termios`. Tests use the same `pytest` + `unittest.mock` style as `test_monitor.py` and `test_sessions.py`.

**Source-of-inspiration:** [`onikan27/claude-code-monitor`](https://github.com/onikan27/claude-code-monitor) — we lift the file-discovery pattern (`~/.claude/projects/{encoded_cwd}/{session_id}.jsonl`, encoding `/` → `-`) and the polling cadence; we drop everything macOS-specific (AppleScript focus, send-text, screen-capture) and the mobile web piece. **V2 follow-up:** install ccm's `Notification` hook so "ccx is blocked on a permission prompt" can light up — the one ccm feature that polling genuinely cannot replicate. Out of scope for V1.

---

## File Structure

```
sesio__ccx/
├── control-plane/
│   ├── ccx/
│   │   ├── sessions.py            # MODIFY: fix parse_jsonl_tokens_today() to include cache tokens
│   │   ├── monitor.py             # MODIFY: register the new `tui` subcommand
│   │   └── monitor_tui.py         # CREATE: SessionRow + fetch_local + fetch_ccx + render + loop
│   └── tests/
│       ├── test_sessions.py       # MODIFY: add cache-token assertions
│       └── test_monitor_tui.py    # CREATE
└── docs/
    └── superpowers/plans/2026-04-30-tui-claude-monitor.md   # this file
```

**Boundaries:**
- `monitor_tui.py` owns the dataclass, fetchers, pure-render functions, and the loop. Pure functions tested directly; the loop is exercised via a deterministic non-TTY path (one frame, exit 0).
- `monitor.py` only adds a typer entry point — no logic.
- `sessions.py` change is contained to `parse_jsonl_tokens_today()` only; nothing else needs to move.

---

## Prerequisites

- `ccxctl session list --json` already exists (`control-plane/ccx/sessions.py:285-292`). Output is a JSON array of dicts with keys: `agent, slug, window, cwd, pane_pid, agent_pid, claude_pid, uptime_seconds, usage_today {input,output,available}, tokens_today {input,output}`.
- `~/.ssh/keys/dsylla-ccx` exists locally and is the SSH key for `david@ccx.dsylla.sesio.io` (per `CFG.ssh_key` in `control-plane/ccx/cli.py:48-53`).
- `rich` is already in `pyproject.toml` (used by `motd.py`, `ui.py`).

---

### Task 0: Read existing patterns (no edits)

**Files (read-only):**
- `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/sessions.py` — `collect_sessions()`, `parse_jsonl_tokens_today()`, the JSON shape emitted by `session list --json`
- `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/monitor.py` — how the existing `monitor` Typer app is structured (status / logs / tunnel / open / close)
- `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/cli.py:30-100` — `Config` dataclass, especially `ssh_user`, `ssh_key`, `hostname`
- `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/ui.py` — the rich `console` instance other modules import
- `/home/david/Work/sesio/sesio__ccx/control-plane/tests/test_sessions.py` — pattern for mocking subprocess + filesystem
- `/home/david/Work/sesio/sesio__ccx/control-plane/tests/test_monitor.py` — pattern for testing a Typer subcommand

- [ ] **Step 1: Confirm the JSON shape**

Run: `cd /home/david/Work/sesio/sesio__ccx/control-plane && uv run ccxctl session list --json | python3 -m json.tool | head -40`

Expected: an array (possibly empty) of objects with the keys listed in **Prerequisites**. If the array is empty, launch a session first: `uv run ccxctl session launch --dir .`

- [ ] **Step 2: Note the shape exactly** — record the field names you'll consume. The renderer must use the *same* names for both fetchers so the merged table has stable columns.

---

### Task 1: Fix `parse_jsonl_tokens_today()` token math (PREREQ — affects both plans)

**Why this is Task 1, not "later":** the existing helper sums only `input_tokens + output_tokens`. Real Claude Code transcripts dominate on cache: `cache_creation_input_tokens` (e.g. 19793) and `cache_read_input_tokens` (e.g. 11752) — orders of magnitude larger than the raw input. The TUI's whole point is showing per-session usage; without this fix the headline number is meaningless. Also dedup by `message.id` so resumed/retried sessions don't double-count.

**Files:**
- Modify: `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/sessions.py:35-69`
- Modify: `/home/david/Work/sesio/sesio__ccx/control-plane/tests/test_sessions.py`

- [ ] **Step 1: Failing test for cache-token inclusion**

Append to `tests/test_sessions.py`:
```python
def test_parse_jsonl_tokens_today_includes_cache_tokens(tmp_path):
    f = tmp_path / "s.jsonl"
    today_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    f.write_text(json.dumps({
        "timestamp": today_iso,
        "message": {"id": "msg_1", "usage": {
            "input_tokens": 3, "output_tokens": 34,
            "cache_creation_input_tokens": 19793,
            "cache_read_input_tokens": 11752,
        }},
    }) + "\n")
    out = sessions.parse_jsonl_tokens_today([f])
    # cache reads/creates are inputs from the model's perspective and are
    # billed; both must be counted.
    assert out["input"] == 3 + 19793 + 11752
    assert out["output"] == 34


def test_parse_jsonl_tokens_today_dedupes_by_message_id(tmp_path):
    f = tmp_path / "s.jsonl"
    today_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    entry = json.dumps({
        "timestamp": today_iso,
        "message": {"id": "msg_dup", "usage": {"input_tokens": 100, "output_tokens": 50}},
    })
    f.write_text(entry + "\n" + entry + "\n")  # same message twice
    out = sessions.parse_jsonl_tokens_today([f])
    assert out["input"] == 100
    assert out["output"] == 50
```

(Make sure `import datetime as _dt` and `import json` are already at the top of `test_sessions.py`; add them if not.)

- [ ] **Step 2: Run; expect FAIL**

Run: `cd control-plane && uv run pytest tests/test_sessions.py -k tokens_today -v`
Expected: 2 FAIL — current impl ignores cache fields and re-sums duplicates.

- [ ] **Step 3: Update `parse_jsonl_tokens_today()`**

Edit `control-plane/ccx/sessions.py` — replace the body of `parse_jsonl_tokens_today` (the part inside the for-loops at lines ~42-65) with:
```python
def parse_jsonl_tokens_today(jsonl_files: list[Path]) -> dict[str, int]:
    """Sum input/output tokens for today (UTC) across the given jsonl files.

    Includes `cache_creation_input_tokens` and `cache_read_input_tokens` in the
    `input` total — both are billed input from the model's POV and dominate
    the actual context spend (cache reads in particular). Deduplicates by
    `message.id` so a resumed or retried session doesn't double-count.

    Tolerates non-JSON lines, missing keys, and missing files.
    """
    today = _dt.datetime.now(_dt.timezone.utc).date()
    total_in = 0
    total_out = 0
    seen_ids: set[str] = set()
    for f in jsonl_files:
        try:
            with open(f) as fh:
                for line in fh:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = entry.get("timestamp", "")
                    try:
                        entry_date = _dt.datetime.fromisoformat(
                            ts.replace("Z", "+00:00")
                        ).astimezone(_dt.timezone.utc).date()
                    except (ValueError, TypeError):
                        continue
                    if entry_date != today:
                        continue
                    msg = entry.get("message") or {}
                    msg_id = msg.get("id")
                    if msg_id:
                        if msg_id in seen_ids:
                            continue
                        seen_ids.add(msg_id)
                    usage = msg.get("usage") or {}
                    total_in += int(usage.get("input_tokens") or 0)
                    total_in += int(usage.get("cache_creation_input_tokens") or 0)
                    total_in += int(usage.get("cache_read_input_tokens") or 0)
                    total_out += int(usage.get("output_tokens") or 0)
        except FileNotFoundError:
            continue
    return {"input": total_in, "output": total_out}
```

- [ ] **Step 4: Run; expect PASS**

Run: `uv run pytest tests/test_sessions.py -k tokens_today -v`
Expected: PASS for the two new tests; the existing `parse_jsonl_tokens_today` tests should also still pass (the old assertions only checked `input_tokens` + `output_tokens`, which we still sum).

- [ ] **Step 5: Run the full test suite to make sure nothing downstream broke**

Run: `uv run pytest -q`
Expected: all green. `motd.py` consumes the same helper for the today's-usage line — its tests should still pass with the larger numbers.

- [ ] **Step 6: Commit**

Use `/commit`: subject `fix(control-plane sessions): include cache tokens, dedup by message.id`, body explaining the undercount (cache reads dominate; we were missing 50–100× of real spend) and the dedup motivation (resumed sessions).

---

### Task 2: `SessionRow` dataclass + `fetch_local()`

**Files:**
- Create: `control-plane/ccx/monitor_tui.py`
- Test: `control-plane/tests/test_monitor_tui.py`

- [ ] **Step 1: Write the failing test for `SessionRow.from_dict()`**

`control-plane/tests/test_monitor_tui.py`:
```python
"""Tests for ccx.monitor_tui — dataclass, fetchers, render, loop."""
from __future__ import annotations

import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from ccx import monitor_tui


def _sample_dict() -> dict:
    return {
        "agent": "claude",
        "slug": "demo",
        "window": "claude:demo",
        "cwd": "/home/david/demo",
        "pane_pid": 1234,
        "agent_pid": 1240,
        "claude_pid": 1240,
        "uptime_seconds": 600.0,
        "usage_today": {"input": 100, "output": 50, "available": True},
        "tokens_today": {"input": 100, "output": 50},
    }


def test_session_row_from_dict_populates_all_fields():
    row = monitor_tui.SessionRow.from_dict(_sample_dict(), source="local")
    assert row.source == "local"
    assert row.agent == "claude"
    assert row.slug == "demo"
    assert row.cwd == "/home/david/demo"
    assert row.uptime_seconds == 600.0
    assert row.tokens_in == 100
    assert row.tokens_out == 50
    assert row.pid == 1240
```

- [ ] **Step 2: Run it; expect ImportError / AttributeError**

Run: `cd control-plane && uv run pytest tests/test_monitor_tui.py::test_session_row_from_dict_populates_all_fields -v`
Expected: FAIL — module/attribute doesn't exist yet.

- [ ] **Step 3: Implement `SessionRow`**

`control-plane/ccx/monitor_tui.py`:
```python
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
```

- [ ] **Step 4: Run the test; expect PASS**

Run: `cd control-plane && uv run pytest tests/test_monitor_tui.py::test_session_row_from_dict_populates_all_fields -v`
Expected: PASS.

- [ ] **Step 5: Add the `fetch_local()` test (failing)**

Append to `tests/test_monitor_tui.py`:
```python
def test_fetch_local_uses_collect_sessions(monkeypatch):
    fake_rows = [_sample_dict()]
    monkeypatch.setattr(monitor_tui, "collect_sessions", lambda: fake_rows)
    out = monitor_tui.fetch_local()
    assert len(out) == 1
    assert out[0].source == "local"
    assert out[0].slug == "demo"
```

- [ ] **Step 6: Run it; expect FAIL**

Run: `uv run pytest tests/test_monitor_tui.py::test_fetch_local_uses_collect_sessions -v`
Expected: FAIL — `fetch_local` not defined.

- [ ] **Step 7: Implement `fetch_local()`**

Append to `ccx/monitor_tui.py`:
```python
from ccx.sessions import collect_sessions  # noqa: E402  (deliberate late import)


def fetch_local() -> list[SessionRow]:
    """Sessions on the local host. Reuses ccx.sessions.collect_sessions()."""
    return [SessionRow.from_dict(r, source="local") for r in collect_sessions()]
```

- [ ] **Step 8: Run the test; expect PASS**

Run: `uv run pytest tests/test_monitor_tui.py::test_fetch_local_uses_collect_sessions -v`
Expected: PASS.

- [ ] **Step 9: Commit**

Use the `/commit` skill: subject `feat(control-plane monitor_tui): add SessionRow + fetch_local`, body explaining this is the first slice of the TUI's data layer.

---

### Task 3: `fetch_ccx()` — SSH-poll with ControlPersist

**Files:**
- Modify: `control-plane/ccx/monitor_tui.py`
- Test: `control-plane/tests/test_monitor_tui.py`

`ControlMaster=auto` + `ControlPersist=120` is the load-bearing detail: 12 polls/min collapse to 1 TCP connection serving ~24 polls. Without it, sshd journald fills with `Accepted publickey` entries (17280/day for an overnight TUI). With it: ~720/day.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_monitor_tui.py`:
```python
def test_fetch_ccx_uses_controlpersist_and_parses_json(monkeypatch):
    captured: dict = {}

    def fake_run(cmd, *, capture_output, text, check, timeout):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=json.dumps([_sample_dict()]), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = monitor_tui.fetch_ccx(
        ssh_user="david",
        hostname="ccx.dsylla.sesio.io",
        ssh_key="/home/david/.ssh/keys/dsylla-ccx",
    )
    assert len(out) == 1
    assert out[0].source == "ccx"
    flat = " ".join(captured["cmd"])
    assert "ssh" in flat
    assert "david@ccx.dsylla.sesio.io" in flat
    # ControlPersist multiplexing — required so 5 s polls don't burn TCPs
    assert "ControlMaster=auto" in flat
    assert "ControlPersist=" in flat
    assert "ccxctl" in flat


def test_fetch_ccx_returns_empty_on_ssh_failure(monkeypatch):
    def fake_run(*a, **kw):
        return subprocess.CompletedProcess(args=a, returncode=255, stdout="", stderr="permission denied")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert monitor_tui.fetch_ccx(ssh_user="david", hostname="ccx", ssh_key="/tmp/k") == []


def test_fetch_ccx_returns_empty_on_timeout(monkeypatch):
    def fake_run(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=a, timeout=5)
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert monitor_tui.fetch_ccx(ssh_user="david", hostname="ccx", ssh_key="/tmp/k") == []


def test_fetch_ccx_returns_empty_on_garbage_stdout(monkeypatch):
    def fake_run(*a, **kw):
        return subprocess.CompletedProcess(args=a, returncode=0, stdout="not json", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert monitor_tui.fetch_ccx(ssh_user="david", hostname="ccx", ssh_key="/tmp/k") == []
```

- [ ] **Step 2: Run them; expect FAIL**

Run: `uv run pytest tests/test_monitor_tui.py -k "fetch_ccx" -v`
Expected: 4 FAIL — `fetch_ccx` not defined.

- [ ] **Step 3: Implement `fetch_ccx()`**

Append to `ccx/monitor_tui.py`:
```python
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
```

- [ ] **Step 4: Run all monitor_tui tests; expect PASS**

Run: `uv run pytest tests/test_monitor_tui.py -v`
Expected: PASS for the 6 tests added so far.

- [ ] **Step 5: Commit**

Use `/commit`: subject `feat(control-plane monitor_tui): add fetch_ccx with ssh ControlPersist`, body noting the multiplexing motivation (12 polls/min → 1 TCP).

---

### Task 4: Pure render — table + rate-limit footer

**Files:**
- Modify: `control-plane/ccx/monitor_tui.py`
- Test: `control-plane/tests/test_monitor_tui.py`

The table renders rows; a one-line footer surfaces the local 5-hour and 7-day Anthropic rate-limit windows from `~/.cache/claude_status/state.json` (written by the existing `ClaudeStatusWidget` background fetcher). Footer is local-only — the ccx box doesn't run that fetcher.

- [ ] **Step 1: Failing tests**

Append to `tests/test_monitor_tui.py`:
```python
from rich.console import Console


def _row(**over):
    base = dict(
        source="local", agent="claude", slug="demo", cwd="/home/david/demo",
        pid=1234, uptime_seconds=600.0, tokens_in=1500, tokens_out=750,
    )
    base.update(over)
    return monitor_tui.SessionRow(**base)


def _render(panel) -> str:
    console = Console(record=True, width=120)
    console.print(panel)
    return console.export_text()


def test_build_panel_includes_source_column_and_help_caption():
    out = _render(monitor_tui.build_panel([_row()]))
    assert "SOURCE" in out.upper()
    assert "local" in out
    assert "demo" in out
    # Help caption mentions all three supported keys
    assert "q" in out and "r" in out and "f" in out


def test_build_panel_renders_humanized_tokens():
    out = _render(monitor_tui.build_panel([_row(tokens_in=1500, tokens_out=750)]))
    assert "1.5k" in out
    assert "750" in out  # too small to humanize


def test_build_panel_handles_unreachable_source():
    out = _render(monitor_tui.build_panel([], unreachable_sources=["ccx"]))
    assert "ccx" in out and "unreachable" in out.lower()


def test_build_panel_empty_local_and_ccx_no_unreachable():
    out = _render(monitor_tui.build_panel([]))
    assert "no sessions" in out.lower()


def test_build_panel_includes_rate_limits_when_provided():
    out = _render(monitor_tui.build_panel(
        [_row()],
        rate_limits={"five_hour": {"used_percentage": 41.0, "resets_at": 9999999999},
                     "seven_day": {"used_percentage": 47.0, "resets_at": 9999999999}},
    ))
    assert "5h" in out and "41%" in out
    assert "7d" in out and "47%" in out


def test_build_panel_omits_rate_limits_when_none():
    out = _render(monitor_tui.build_panel([_row()], rate_limits=None))
    assert "5h" not in out
    assert "7d" not in out


def test_load_rate_limits_reads_json(tmp_path, monkeypatch):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"rate_limits": {
        "five_hour": {"used_percentage": 30, "resets_at": 1},
        "seven_day": {"used_percentage": 40, "resets_at": 2},
    }}))
    out = monitor_tui.load_rate_limits(p)
    assert out["five_hour"]["used_percentage"] == 30


def test_load_rate_limits_returns_none_on_missing_file(tmp_path):
    assert monitor_tui.load_rate_limits(tmp_path / "nope.json") is None
```

- [ ] **Step 2: Run; expect FAIL**

Run: `uv run pytest tests/test_monitor_tui.py -k "panel or rate_limits" -v`
Expected: 8 FAIL — `build_panel`, `load_rate_limits` not defined.

- [ ] **Step 3: Implement render helpers**

Append to `ccx/monitor_tui.py`:
```python
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
```

- [ ] **Step 4: Run; expect PASS**

Run: `uv run pytest tests/test_monitor_tui.py -v`
Expected: all 14 (so far) PASS.

- [ ] **Step 5: Commit**

Use `/commit`: subject `feat(control-plane monitor_tui): table + rate-limit footer`.

---

### Task 5: Loop, key handling, and TTY safety

**Files:**
- Modify: `control-plane/ccx/monitor_tui.py`
- Test: `control-plane/tests/test_monitor_tui.py`

Three guards against terminal corruption: (1) `try/finally` restores termios on the normal path; (2) `atexit.register` covers crashes that bypass `finally` (e.g. `Live.__exit__` raising); (3) SIGTERM/SIGHUP handler restores termios + writes the alt-screen-exit / cursor-show ANSI sequences before re-raising. Also: a deterministic non-TTY path that renders one frame and exits 0 — exercised by a real test, not just a smoke check.

- [ ] **Step 1: Failing tests for `collect_rows` + non-TTY `run_tui`**

Append to `tests/test_monitor_tui.py`:
```python
import io
from unittest.mock import MagicMock


def test_collect_rows_combines_local_and_ccx():
    fa = MagicMock(return_value=[_row(slug="L")])
    fb = MagicMock(return_value=[_row(slug="C", source="ccx")])
    rows, unreachable = monitor_tui.collect_rows([("local", fa), ("ccx", fb)])
    assert {r.slug for r in rows} == {"L", "C"}
    assert unreachable == []


def test_collect_rows_filters_disabled_source():
    fa = MagicMock(return_value=[_row(slug="L")])
    fb = MagicMock(return_value=[_row(slug="C", source="ccx")])
    rows, _ = monitor_tui.collect_rows(
        [("local", fa), ("ccx", fb)], filter_source="local",
    )
    assert {r.slug for r in rows} == {"L"}


def test_collect_rows_marks_failing_source_unreachable():
    bad = MagicMock(side_effect=OSError("boom"))
    rows, unreachable = monitor_tui.collect_rows([("ccx", bad)])
    assert rows == []
    assert unreachable == ["ccx"]


def test_run_tui_non_tty_renders_one_frame_and_exits_zero(monkeypatch, capsys):
    """The non-interactive path must be deterministic and CI-safe."""
    monkeypatch.setattr("sys.stdin", io.StringIO())  # not a tty
    fakes = [("local", MagicMock(return_value=[_row(slug="X")]))]
    rc = monitor_tui.run_tui(fakes, interval=99.0)
    assert rc == 0
    out = capsys.readouterr().out
    assert "X" in out


def test_cycle_filter_progresses_both_local_ccx_both():
    assert monitor_tui.cycle_filter(None) == "local"
    assert monitor_tui.cycle_filter("local") == "ccx"
    assert monitor_tui.cycle_filter("ccx") is None
```

- [ ] **Step 2: Run; expect FAIL**

Run: `uv run pytest tests/test_monitor_tui.py -k "collect_rows or non_tty or cycle_filter" -v`
Expected: 5 FAIL.

- [ ] **Step 3: Implement `collect_rows`, `cycle_filter`, and `run_tui`**

Append to `ccx/monitor_tui.py`:
```python
import atexit
import logging
import os
import select
import signal
import sys
import termios
import tty
from typing import Callable

from rich.live import Live

log = logging.getLogger(__name__)


# Each source = (name, callable returning list[SessionRow]).
SourceTuple = tuple[str, Callable[[], list[SessionRow]]]


def collect_rows(
    sources: list[SourceTuple],
    *,
    filter_source: str | None = None,
) -> tuple[list[SessionRow], list[str]]:
    """Fetch from all sources; return (rows, unreachable_source_names).

    `filter_source` restricts both the rows AND the unreachable list, so the
    user sees only what they asked for.
    """
    rows: list[SessionRow] = []
    unreachable: list[str] = []
    for name, fetch in sources:
        if filter_source is not None and name != filter_source:
            continue
        try:
            rows.extend(fetch())
        except Exception:
            log.warning("source %s failed", name, exc_info=True)
            unreachable.append(name)
    return rows, unreachable


def cycle_filter(current: str | None) -> str | None:
    """Cycle: None (both) → 'local' → 'ccx' → None."""
    return {None: "local", "local": "ccx", "ccx": None}[current]


def _key_pressed(timeout: float) -> str | None:
    if not sys.stdin.isatty():
        return None
    r, _, _ = select.select([sys.stdin], [], [], timeout)
    return sys.stdin.read(1) if r else None


def _install_termios_guard(fd: int, old_settings) -> None:
    """Belt-and-suspenders termios restore: atexit + SIGTERM/SIGHUP."""
    def restore(_signum=None, _frame=None):
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            # exit alt-screen + show cursor — Live should have done this in
            # __exit__, but if we're here from a signal we need to be sure.
            sys.stdout.write("\x1b[?1049l\x1b[?25h")
            sys.stdout.flush()
        except Exception:
            pass
        if _signum is not None:
            signal.signal(_signum, signal.SIG_DFL)
            os.kill(os.getpid(), _signum)

    atexit.register(restore)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, restore)
        except (ValueError, OSError):
            pass


def run_tui(
    sources: list[SourceTuple],
    *,
    interval: float = 5.0,
    initial_filter: str | None = None,
    debug: bool = False,
) -> int:
    """Render loop. Returns process exit code."""
    if debug:
        logging.basicConfig(level=logging.DEBUG)

    filter_source = initial_filter

    if not sys.stdin.isatty():
        rows, unreachable = collect_rows(sources, filter_source=filter_source)
        rl = load_rate_limits()
        # Plain print — the non-tty path is for redirects / harnesses, no Live.
        from ccx.ui import console
        console.print(build_panel(rows, unreachable_sources=unreachable, rate_limits=rl))
        return 0

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    _install_termios_guard(fd, old_settings)
    try:
        tty.setcbreak(fd)
        from ccx.ui import console
        with Live(build_panel([]), console=console, refresh_per_second=4, screen=True) as live:
            while True:
                rows, unreachable = collect_rows(sources, filter_source=filter_source)
                rl = load_rate_limits()
                live.update(build_panel(rows, unreachable_sources=unreachable, rate_limits=rl))
                key = _key_pressed(interval)
                if key in ("q", "\x03", "\x04"):  # q, Ctrl-C, Ctrl-D
                    return 0
                if key == "r":
                    continue
                if key == "f":
                    filter_source = cycle_filter(filter_source)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def make_default_sources() -> list[SourceTuple]:
    """Build the standard (local + ccx) source list using ccxctl's CFG."""
    from ccx.cli import CFG
    return [
        ("local", fetch_local),
        ("ccx", lambda: fetch_ccx(
            ssh_user=CFG.ssh_user,
            hostname=CFG.hostname,
            ssh_key=str(CFG.ssh_key),
        )),
    ]
```

- [ ] **Step 4: Run; expect PASS**

Run: `uv run pytest tests/test_monitor_tui.py -v`
Expected: all PASS, including the deterministic non-TTY test.

- [ ] **Step 5: Smoke-test interactively (real terminal required)**

Run (NOT through claude/codex — needs a real TTY):
```
cd /home/david/Work/sesio/sesio__ccx/control-plane
uv run python -c "from ccx.monitor_tui import run_tui, make_default_sources; run_tui(make_default_sources())"
```
Expected: a live panel appears; pressing `q` / Ctrl-C / Ctrl-D exits cleanly; pressing `f` cycles filter (both → local → ccx → both); pressing `r` triggers an immediate refresh; the terminal is restored to normal mode after exit. Send a SIGTERM (`kill -TERM <pid>` from another shell) and verify the terminal also recovers.

If the terminal is ever left in a bad state, run `stty sane`.

- [ ] **Step 6: Commit**

Use `/commit`: subject `feat(control-plane monitor_tui): polling loop with TTY guards`, body listing the keys (`q`, `r`, `f`) and the three-layer termios restore (try/finally + atexit + SIGTERM/SIGHUP).

---

### Task 6: Wire `monitor tui` into the Typer app (with `--debug`)

**Files:**
- Modify: `control-plane/ccx/monitor.py`
- Test: `control-plane/tests/test_monitor.py`

- [ ] **Step 1: Failing tests**

Append to `control-plane/tests/test_monitor.py`:
```python
def test_monitor_tui_listed_in_help():
    from typer.testing import CliRunner
    from ccx.cli import app
    result = CliRunner().invoke(app, ["monitor", "--help"])
    assert result.exit_code == 0
    assert "tui" in result.stdout.lower()


def test_monitor_tui_invokes_run_tui_with_filter_and_debug(monkeypatch):
    called: dict = {}
    def fake_run(sources, **kw):
        called["sources"] = sources
        called["kw"] = kw
        return 0
    monkeypatch.setattr("ccx.monitor_tui.run_tui", fake_run)

    from typer.testing import CliRunner
    from ccx.cli import app
    result = CliRunner().invoke(app, ["monitor", "tui", "--source", "local", "--debug"])
    assert result.exit_code == 0
    assert called["kw"]["initial_filter"] == "local"
    assert called["kw"]["debug"] is True


def test_monitor_tui_rejects_invalid_source():
    from typer.testing import CliRunner
    from ccx.cli import app
    result = CliRunner().invoke(app, ["monitor", "tui", "--source", "nope"])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run; expect FAIL**

Run: `uv run pytest tests/test_monitor.py -k tui -v`
Expected: 3 FAIL.

- [ ] **Step 3: Add the subcommand**

Edit `control-plane/ccx/monitor.py` — append (after the existing subcommands):
```python
@app.command("tui")
def cmd_tui(
    interval: float = typer.Option(5.0, "--interval", "-i", help="Poll interval seconds."),
    source: str = typer.Option(
        "both",
        "--source", "-s",
        help="Which source to show: local, ccx, or both.",
    ),
    debug: bool = typer.Option(
        False, "--debug", help="Surface fetcher exceptions to stderr (logging.DEBUG).",
    ),
):
    """Live TUI dashboard of agent sessions on local + ccx (q to quit, f to cycle filter)."""
    if source not in {"both", "local", "ccx"}:
        raise typer.BadParameter("--source must be one of: both, local, ccx")
    from ccx import monitor_tui
    rc = monitor_tui.run_tui(
        monitor_tui.make_default_sources(),
        interval=interval,
        initial_filter=None if source == "both" else source,
        debug=debug,
    )
    raise typer.Exit(code=rc)
```

- [ ] **Step 4: Run; expect PASS**

Run: `uv run pytest tests/test_monitor.py -k tui -v`
Expected: 3 PASS.

- [ ] **Step 5: Verify the full suite**

Run: `uv run pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

Use `/commit`: subject `feat(ccxctl monitor): add tui subcommand with --debug`.

---

### Task 7: Smoke-test against the live ccx box

**Files:** none.

- [ ] **Step 1: Make sure ccx is reachable**

Run: `ssh -i ~/.ssh/keys/dsylla-ccx -o ConnectTimeout=5 david@ccx.dsylla.sesio.io 'ccxctl session list --json' | python3 -m json.tool | head -20`
Expected: a JSON array (possibly empty). If the host isn't reachable, the next step will show `ccx (unreachable)` — that's fine.

- [ ] **Step 2: Launch the TUI**

Run: `cd /home/david/Work/sesio/sesio__ccx/control-plane && uv run ccxctl monitor tui`
Expected: a live panel refreshing every 5 s; both sources visible if both have sessions; rate-limit footer present iff `~/.cache/claude_status/state.json` exists locally.

- [ ] **Step 3: Verify all key bindings**

Press `f` repeatedly → cycles `both → local → ccx → both`. Press `r` → immediate refresh. Press `q` → clean exit, terminal restored.

- [ ] **Step 4: Verify `--source` option**

Run: `uv run ccxctl monitor tui --source ccx`
Expected: starts in ccx-only filter.

- [ ] **Step 5: Verify SSH multiplexing kicked in**

While the TUI is running, in another shell: `ls ~/.ssh/cm-* 2>/dev/null`
Expected: a control-master socket exists. After `q`, it persists ~120 s then disappears.

- [ ] **Step 6: Verify SIGTERM cleanup**

Start the TUI; from another shell: `kill -TERM $(pgrep -f "ccxctl monitor tui")`. The terminal should be left in a usable state (no cbreak, cursor visible, alt-screen exited). If not, the SIGTERM handler is broken — go back to Task 5.

- [ ] **Step 7: No commit** (smoke test only). If a fix is needed, write a unit test for it first, then fix, then commit.

---

## V2 Follow-Up (DO NOT do in this plan)

The one ccm feature genuinely lost by polling-only is `Notification` hook events — Claude Code fires that hook when a session is blocked on a permission prompt or sits idle 60 s. With it we could light up "ccx is waiting for you" in the TUI table. **V2:** install only this single hook (writes a one-line append to `~/.cache/ccx/notifications.jsonl`), poll that file alongside the existing jsonls, and add a `[!]` column. Architectural cost is small, UX value is big. Do not start V2 until V1 has been used for at least a week.

## Self-Review Checklist (run before declaring done)

- [ ] All seven `Task N` blocks pass `uv run pytest -q` clean
- [ ] `parse_jsonl_tokens_today()` numbers match `claude /cost` output for at least one real session within ~5%
- [ ] No new lint errors: `uv run ruff check control-plane/ccx control-plane/tests`
- [ ] `ccxctl monitor --help` lists `tui` alongside `status`/`logs`/`tunnel`/`open`/`close`
- [ ] Terminal state is correctly restored after `q`, after Ctrl-C, after Ctrl-D, **and** after SIGTERM (`stty sane` should never be needed)
- [ ] Ctrl-C while in alt-screen leaves the terminal usable on the first try
- [ ] Non-TTY path renders one frame and exits 0 (covered by a real test, not a smoke check)
- [ ] No hooks installed in `~/.claude/settings.json` — polling only (V2 deferred per the note above)
- [ ] SSH ControlPersist socket is created on the first ccx fetch and reused across polls
