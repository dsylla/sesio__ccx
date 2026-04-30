"""Process discovery — /proc walk + PID-to-session linkage.

On startup (and on inotify overflow), discovers all running `claude`
processes and links each to its active session via /proc/<pid>/fd/*
symlinks resolving to top-level jsonl files.

The /proc/<pid>/fd approach is canonical because:
- mtime ordering on project dirs is unreliable (idle sessions, multiple instances)
- The open fd IS the active session — no ambiguity
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from ccx.ccxd.jsonl import JsonlTailer, parse_deltas
from ccx.ccxd.state import Session
from ccx.sessions import process_uptime_seconds

_PROC = "/proc"
_CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
_CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
_BOOT_TIME: float = 0.0  # set at module load or overridden in tests
_NOW_FN = time.time

# Match top-level jsonl: <projects_dir>/<encoded_cwd>/<session_id>.jsonl
# Must NOT be under a subagents/ subdirectory.
_JSONL_RE = re.compile(r".*/([^/]+)\.jsonl$")


def _init_boot_time() -> float:
    try:
        with open(f"{_PROC}/stat") as f:
            for line in f:
                if line.startswith("btime "):
                    return float(line.split()[1])
    except (FileNotFoundError, PermissionError):
        pass
    return 0.0


def _is_top_level_jsonl(path: str, projects_dir: str) -> bool:
    """True if path is a top-level session jsonl (not under subagents/)."""
    if not path.startswith(projects_dir):
        return False
    rel = path[len(projects_dir):]
    parts = rel.strip("/").split("/")
    # Expected: <encoded_cwd>/<session_id>.jsonl — exactly 2 parts
    return len(parts) == 2 and parts[1].endswith(".jsonl")


def _process_start_epoch(pid: int) -> float:
    """Calculate process start time as epoch seconds."""
    try:
        with open(f"{_PROC}/{pid}/stat") as f:
            raw = f.read()
    except (FileNotFoundError, PermissionError):
        return _NOW_FN()
    rest = raw.split(")", 1)[-1].split()
    try:
        starttime_ticks = int(rest[19])
    except (IndexError, ValueError):
        return _NOW_FN()
    return _BOOT_TIME + starttime_ticks / _CLK_TCK


def discover_sessions() -> list[Session]:
    """Walk /proc for claude processes; link each to its session jsonl via fd."""
    global _BOOT_TIME
    if _BOOT_TIME == 0.0:
        _BOOT_TIME = _init_boot_time()

    sessions: list[Session] = []
    proc_root = _PROC
    projects_dir = _CLAUDE_PROJECTS_DIR

    try:
        entries = os.listdir(proc_root)
    except OSError:
        return sessions

    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(f"{proc_root}/{pid}/comm") as f:
                if f.read().strip() != "claude":
                    continue
        except (FileNotFoundError, PermissionError):
            continue

        # Read cwd
        try:
            cwd = os.readlink(f"{proc_root}/{pid}/cwd")
        except (FileNotFoundError, PermissionError, OSError):
            continue

        # Walk fd to find the active jsonl
        session_jsonl: str | None = None
        try:
            fd_dir = f"{proc_root}/{pid}/fd"
            for fd_entry in os.listdir(fd_dir):
                try:
                    target = os.readlink(f"{fd_dir}/{fd_entry}")
                except (FileNotFoundError, PermissionError, OSError):
                    continue
                if _is_top_level_jsonl(target, projects_dir):
                    session_jsonl = target
                    break
        except (FileNotFoundError, PermissionError):
            continue

        if not session_jsonl:
            continue

        # Extract session_id from filename
        session_id = Path(session_jsonl).stem

        # Bootstrap session state from a full jsonl read
        tailer = JsonlTailer(Path(session_jsonl))
        all_entries = tailer.read_new()

        tokens_in = 0
        tokens_out = 0
        model: str | None = None
        summary: str | None = None
        last_subagent: dict | None = None

        for e in all_entries:
            deltas = parse_deltas(e)
            if "tokens_in" in deltas:
                tokens_in += deltas["tokens_in"]
            if "tokens_out" in deltas:
                tokens_out += deltas["tokens_out"]
            if "model" in deltas:
                model = deltas["model"]
            if "summary" in deltas:
                summary = deltas["summary"]
            if "last_subagent" in deltas:
                last_subagent = deltas["last_subagent"]

        # Fallback summary: first user message truncated to 80 chars
        if not summary:
            for e in all_entries:
                if e.get("type") == "human" or (
                    e.get("message", {}).get("role") == "user"
                ):
                    content = e.get("message", {}).get("content", "")
                    if isinstance(content, str):
                        summary = content[:80]
                        break

        started_at = _process_start_epoch(pid)
        sessions.append(Session(
            session_id=session_id,
            cwd=cwd,
            pid=pid,
            model=model,
            summary=summary,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            last_subagent=last_subagent,
            subagent_in_flight=None,
            attention=None,
            last_activity_at=_NOW_FN(),
            started_at=started_at,
        ))

    return sessions
