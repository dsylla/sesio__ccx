"""ccxctl session — tmux-backed project-anchored coding-agent session manager."""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import time
import typer
from pathlib import Path

from ccx.agents import AgentSpec, get_agent, split_window_name


def slug(path: str) -> str:
    """Slugify a filesystem path for use as a tmux window name."""
    base = os.path.basename(os.path.abspath(path))
    s = base.lower()
    s = re.sub(r"[^a-z0-9_-]", "-", s)
    s = re.sub(r"-+", "-", s)
    return s


def encode_project_dir(path: str) -> str:
    """Claude Code's on-disk convention for per-project dirs: `/` → `-`."""
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


_PROC = "/proc"  # overridable in tests

SESSION_NAME = "ccx"

_CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


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


def find_agent_pid(pane_pid: int, agent: AgentSpec) -> int | None:
    """Walk /proc descendants of pane_pid; return the first one whose comm matches the agent."""
    to_visit = [pane_pid]
    seen: set[int] = set()
    while to_visit:
        pid = to_visit.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            with open(f"{_PROC}/{pid}/comm") as f:
                comm = f.read().strip()
            if comm in agent.process_names:
                return pid
        except (FileNotFoundError, PermissionError):
            pass
        try:
            tasks_dir = f"{_PROC}/{pid}/task"
            for tid in os.listdir(tasks_dir):
                try:
                    with open(f"{tasks_dir}/{tid}/children") as f:
                        for child in f.read().split():
                            to_visit.append(int(child))
                except (FileNotFoundError, PermissionError, ValueError):
                    continue
        except (FileNotFoundError, PermissionError):
            continue
    return None


def find_claude_pid(pane_pid: int) -> int | None:
    """Back-compat wrapper around find_agent_pid for the claude agent."""
    return find_agent_pid(pane_pid, get_agent("claude"))


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
    start_epoch = _BOOT_FN() + starttime_ticks / _CLK_TCK
    return _NOW_FN() - start_epoch


def _project_jsonl_files(cwd: str) -> list[Path]:
    enc = encode_project_dir(cwd)
    d = Path(_CLAUDE_PROJECTS_DIR) / enc
    if not d.is_dir():
        return []
    return sorted(d.glob("*.jsonl"))


def _usage_for_agent(agent_name: str, cwd: str) -> dict:
    """Per-agent usage stats. Only Claude has a local jsonl source today."""
    if agent_name != "claude":
        return {"input": 0, "output": 0, "available": False}
    tk = parse_jsonl_tokens_today(_project_jsonl_files(cwd))
    return {**tk, "available": True}


def collect_sessions() -> list[dict]:
    """Enumerate tmux windows in session `ccx`, enrich each with agent + uptime + usage."""
    out: list[dict] = []
    for row in tmux_list_windows():
        try:
            agent_name, bare_slug = split_window_name(row["slug"])
            agent = get_agent(agent_name)
            agent_pid = find_agent_pid(row["pane_pid"], agent)
            uptime = _process_uptime_seconds(agent_pid) if agent_pid else None
            usage = _usage_for_agent(agent.name, row["cwd"])
        except Exception:
            agent_name = "claude"
            bare_slug = row["slug"]
            agent_pid, uptime = None, None
            usage = {"input": 0, "output": 0, "available": False}
        out.append({
            "agent": agent_name,
            "slug": bare_slug,
            "window": row["slug"],
            "cwd": row["cwd"],
            "pane_pid": row["pane_pid"],
            "agent_pid": agent_pid,
            "claude_pid": agent_pid if agent_name == "claude" else None,
            "uptime_seconds": uptime,
            "usage_today": usage,
            "tokens_today": {"input": int(usage["input"]), "output": int(usage["output"])},
        })
    return out


app = typer.Typer(help="Manage project-anchored claude sessions on ccx.")


def _ensure_session() -> None:
    """Create the shared tmux session if it doesn't exist."""
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", SESSION_NAME],
        capture_output=True, check=False, timeout=3,
    )


def _tmux_new_window(slug_: str, cwd: str) -> None:
    subprocess.run(
        ["tmux", "new-window", "-t", SESSION_NAME, "-n", slug_, "-c", cwd, "--", "claude"],
        capture_output=True, check=False, timeout=5,
    )


def _tmux_kill_window(slug_: str) -> None:
    subprocess.run(
        ["tmux", "kill-window", "-t", f"{SESSION_NAME}:{slug_}"],
        capture_output=True, check=False, timeout=3,
    )


@app.command("launch")
def cmd_launch(
    dir: str = typer.Option(".", "--dir", "-d", help="Project directory."),
):
    """Create (or attach) a tmux window for DIR running claude."""
    path = os.path.abspath(os.path.expanduser(dir))
    s = slug(path)
    _ensure_session()
    if tmux_has_window(s):
        typer.echo(f"window {SESSION_NAME}:{s} already open")
        return
    _tmux_new_window(s, path)
    typer.echo(f"launched {SESSION_NAME}:{s} (cwd={path})")


@app.command("list")
def cmd_list(
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
):
    """List sessions with claude pid, uptime, today's tokens."""
    rows = collect_sessions()
    if as_json:
        typer.echo(json.dumps(rows, default=str))
        return
    if not rows:
        typer.echo("(no sessions)")
        return
    # Simple aligned table
    typer.echo(f"{'SLUG':<20} {'PID':>8} {'UPTIME':>10} {'IN':>10} {'OUT':>10}  CWD")
    for r in rows:
        uptime = f"{int(r['uptime_seconds'] // 60)}m" if r.get("uptime_seconds") else "-"
        pid = r["claude_pid"] or "-"
        toks = r["tokens_today"]
        typer.echo(
            f"{r['slug']:<20} {str(pid):>8} {uptime:>10} {toks['input']:>10} {toks['output']:>10}  {r['cwd']}"
        )


@app.command("attach")
def cmd_attach(slug_: str = typer.Argument(None, help="Window slug. Default: MRU.")):
    """Attach to the shared ccx tmux session, optionally selecting a window."""
    target = f"{SESSION_NAME}:{slug_}" if slug_ else SESSION_NAME
    os.execvp("tmux", ["tmux", "attach-session", "-t", target])


@app.command("kill")
def cmd_kill(slug_: str = typer.Argument(..., help="Window slug.")):
    """Kill a session window."""
    _tmux_kill_window(slug_)
    typer.echo(f"killed {SESSION_NAME}:{slug_}")


@app.command("menu")
def cmd_menu():
    """rofi-backed picker over existing sessions; attaches the selection."""
    rows = collect_sessions()
    if not rows:
        typer.echo("(no sessions — use `ccxctl session launch --dir ...`)")
        raise typer.Exit(code=0)
    items = [f"{r['slug']}  ({r['cwd']})" for r in rows]
    # Reuse the same pick_menu helper from cli.py to stay DRY.
    from ccx.cli import pick_menu
    choice = pick_menu("ccx session:", items)
    if not choice:
        return
    picked_slug = choice.split("  ")[0]
    cmd_attach(picked_slug)
