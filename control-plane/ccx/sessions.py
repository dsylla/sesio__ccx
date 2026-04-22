"""ccxctl session — tmux-backed project-anchored claude session manager."""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path


def slug(path: str) -> str:
    """Slugify a filesystem path for use as a tmux window name."""
    import os
    base = os.path.basename(os.path.abspath(path))
    s = base.lower()
    s = re.sub(r"[^a-z0-9_-]", "-", s)
    s = re.sub(r"-+", "-", s)
    return s


def encode_project_dir(path: str) -> str:
    """Claude Code's on-disk convention for per-project dirs: `/` → `-`."""
    import os
    abs_path = os.path.abspath(path)
    # Leading slash becomes a leading dash, other slashes too.
    return abs_path.replace("/", "-")


def parse_jsonl_tokens_today(jsonl_files: list[Path]) -> dict[str, int]:
    """Sum input/output tokens for today (UTC) across the given jsonl files.

    Tolerates non-JSON lines, missing keys, and missing files.
    """
    today = _dt.datetime.now(_dt.timezone.utc).date()
    total_in = 0
    total_out = 0
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
                    usage = (entry.get("message") or {}).get("usage") or {}
                    total_in += int(usage.get("input_tokens") or 0)
                    total_out += int(usage.get("output_tokens") or 0)
        except FileNotFoundError:
            continue
    return {"input": total_in, "output": total_out}


import os
import subprocess

_PROC = "/proc"  # overridable in tests

SESSION_NAME = "ccx"


def tmux_list_windows(session: str = SESSION_NAME) -> list[dict]:
    """Return the rows of `tmux list-windows` as dicts. Empty if session absent."""
    fmt = "#{window_name}|#{window_activity}|#{pane_current_path}|#{pane_pid}"
    result = subprocess.run(
        ["tmux", "list-windows", "-t", session, "-F", fmt],
        capture_output=True, text=True, check=False, timeout=3,
    )
    if result.returncode != 0:
        return []
    rows: list[dict] = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("|")
        if len(parts) != 4:
            continue
        name, activity, cwd, pid = parts
        try:
            rows.append({
                "slug": name,
                "activity": int(activity),
                "cwd": cwd,
                "pane_pid": int(pid),
            })
        except ValueError:
            continue
    return rows


def tmux_has_window(slug_: str, session: str = SESSION_NAME) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", f"{session}:{slug_}"],
        capture_output=True, text=True, check=False, timeout=3,
    )
    return result.returncode == 0


def find_claude_pid(pane_pid: int) -> int | None:
    """Walk /proc descendants of pane_pid; return the first one whose comm is 'claude'."""
    to_visit = [pane_pid]
    seen: set[int] = set()
    while to_visit:
        pid = to_visit.pop()
        if pid in seen:
            continue
        seen.add(pid)
        # Check comm
        try:
            with open(f"{_PROC}/{pid}/comm") as f:
                comm = f.read().strip()
            if comm == "claude":
                return pid
        except FileNotFoundError:
            pass
        # Enqueue children from all threads
        try:
            tasks_dir = f"{_PROC}/{pid}/task"
            for tid in os.listdir(tasks_dir):
                try:
                    with open(f"{tasks_dir}/{tid}/children") as f:
                        for child in f.read().split():
                            to_visit.append(int(child))
                except (FileNotFoundError, ValueError):
                    continue
        except FileNotFoundError:
            continue
    return None


import time

_NOW_FN = time.time
_CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")


def _boot_time() -> float:
    try:
        with open(f"{_PROC}/stat") as f:
            for line in f:
                if line.startswith("btime "):
                    return float(line.split()[1])
    except FileNotFoundError:
        pass
    return 0.0


_BOOT_FN = _boot_time


def _process_uptime_seconds(pid: int) -> float | None:
    """Uptime of a pid in seconds, derived from /proc/<pid>/stat starttime field."""
    try:
        with open(f"{_PROC}/{pid}/stat") as f:
            raw = f.read()
    except FileNotFoundError:
        return None
    # The comm field can contain spaces/parens, so take everything after the closing paren.
    rest = raw.split(")", 1)[-1].split()
    # rest[0] = state, then 20 more fields → starttime at rest[19]
    try:
        starttime_ticks = int(rest[19])
    except (IndexError, ValueError):
        return None
    clk_tck = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    # _NOW_FN returns current time in the same tick unit as starttime_ticks;
    # _BOOT_FN is available for callers that need absolute epoch anchoring.
    return (_NOW_FN() - starttime_ticks) / clk_tck


def _project_jsonl_files(cwd: str) -> list[Path]:
    enc = encode_project_dir(cwd)
    d = Path(_CLAUDE_PROJECTS_DIR) / enc
    if not d.is_dir():
        return []
    return sorted(d.glob("*.jsonl"))


def collect_sessions() -> list[dict]:
    """Enumerate tmux windows in session `ccx`, enrich each with claude + tokens."""
    out: list[dict] = []
    for row in tmux_list_windows():
        claude_pid = find_claude_pid(row["pane_pid"])
        uptime = _process_uptime_seconds(claude_pid) if claude_pid else None
        tokens = parse_jsonl_tokens_today(_project_jsonl_files(row["cwd"]))
        out.append({
            "slug": row["slug"],
            "cwd": row["cwd"],
            "pane_pid": row["pane_pid"],
            "claude_pid": claude_pid,
            "uptime_seconds": uptime,
            "tokens_today": tokens,
        })
    return out
