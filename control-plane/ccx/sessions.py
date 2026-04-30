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

from ccx.agents import AgentSpec, DEFAULT_AGENT, get_agent, split_window_name, window_name


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


def _tmux_new_window(agent: AgentSpec, tmux_window: str, cwd: str) -> None:
    subprocess.run(
        ["tmux", "new-window", "-t", SESSION_NAME, "-n", tmux_window, "-c", cwd, "--", agent.command],
        capture_output=True, check=False, timeout=5,
    )


def _tmux_kill_window(tmux_window: str) -> None:
    subprocess.run(
        ["tmux", "kill-window", "-t", f"{SESSION_NAME}:{tmux_window}"],
        capture_output=True, check=False, timeout=3,
    )


def _resolve_window_target(slug_: str, agent_name: str | None = None) -> str:
    """Resolve a user-supplied slug to a tmux window name.

    - `agent:slug` is taken as-is.
    - With explicit `--agent`, prefix the slug.
    - Without one, prefer an existing bare-slug window (legacy claude), else default agent.
    """
    if ":" in slug_:
        return slug_
    if agent_name:
        return window_name(agent_name, slug_)
    if tmux_has_window(slug_):
        return slug_
    return window_name(DEFAULT_AGENT, slug_)


@app.command("launch")
def cmd_launch(
    dir: str = typer.Option(".", "--dir", "-d", help="Project directory."),
    agent_name: str = typer.Option(DEFAULT_AGENT, "--agent", "-a", help="Agent to launch."),
):
    """Create (or attach) a tmux window for DIR running the given agent."""
    try:
        agent = get_agent(agent_name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    path = os.path.abspath(os.path.expanduser(dir))
    tmux_window = window_name(agent.name, slug(path))
    _ensure_session()
    if tmux_has_window(tmux_window):
        typer.echo(f"window {SESSION_NAME}:{tmux_window} already open")
        return
    _tmux_new_window(agent, tmux_window, path)
    typer.echo(f"launched {SESSION_NAME}:{tmux_window} (cwd={path})")


@app.command("list")
def cmd_list(
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
):
    """List sessions with agent, pid, uptime, today's usage."""
    rows = collect_sessions()
    if as_json:
        typer.echo(json.dumps(rows, default=str))
        return
    if not rows:
        typer.echo("(no sessions)")
        return
    typer.echo(
        f"{'AGENT':<8} {'SLUG':<20} {'PID':>8} {'UPTIME':>10} {'IN':>10} {'OUT':>10}  CWD"
    )
    for r in rows:
        uptime = f"{int(r['uptime_seconds'] // 60)}m" if r.get("uptime_seconds") else "-"
        pid = r.get("agent_pid") or r.get("claude_pid") or "-"
        usage = r.get("usage_today")
        if usage is None:
            toks = r.get("tokens_today") or {"input": 0, "output": 0}
            usage = {"input": toks["input"], "output": toks["output"], "available": True}
        if usage.get("available", True):
            in_s = f"{usage.get('input', 0):>10}"
            out_s = f"{usage.get('output', 0):>10}"
        else:
            in_s = out_s = f"{'-':>10}"
        typer.echo(
            f"{r.get('agent', 'claude'):<8} {r['slug']:<20} {str(pid):>8} {uptime:>10} {in_s} {out_s}  {r['cwd']}"
        )


@app.command("attach")
def cmd_attach(
    slug_: str = typer.Argument(None, help="Window slug or agent:slug. Default: MRU."),
    agent_name: str | None = typer.Option(None, "--agent", "-a", help="Agent prefix for bare slug."),
):
    """Attach to the shared ccx tmux session, optionally selecting a window."""
    if slug_:
        target = f"{SESSION_NAME}:{_resolve_window_target(slug_, agent_name)}"
    else:
        target = SESSION_NAME
    os.execvp("tmux", ["tmux", "attach-session", "-t", target])


@app.command("kill")
def cmd_kill(
    slug_: str = typer.Argument(..., help="Window slug or agent:slug."),
    agent_name: str | None = typer.Option(None, "--agent", "-a", help="Agent prefix for bare slug."),
):
    """Kill a session window."""
    target = _resolve_window_target(slug_, agent_name)
    _tmux_kill_window(target)
    typer.echo(f"killed {SESSION_NAME}:{target}")


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
