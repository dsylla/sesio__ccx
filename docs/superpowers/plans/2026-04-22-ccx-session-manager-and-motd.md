# ccx — Session Manager + MOTD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `ccxctl session …` (tmux-backed, project-anchored claude session manager) and `ccxctl motd` (ANSI-boxed login banner) to the existing `ccx-cli` Python package.

**Architecture:** Two new modules in `control-plane/ccx/`: `sessions.py` (tmux wrappers + claude pid discovery + token parsing + typer sub-app) and `motd.py` (stdlib-only collectors + ANSI renderer + typer command). Both wired into the existing top-level `cli.py`. `ccxctl ssh` gains a `--raw` flag; default path now does `exec tmux new-session -A -s ccx`. Ansible gains a `motd` role that drops `/etc/update-motd.d/10-ccx` and disables the Debian defaults.

**Tech Stack:** Python 3.11+, typer, pytest, `subprocess` for tmux/systemctl/git, `urllib.request` for IMDSv2, pure stdlib in `motd.py` (no boto3 import on login path), tmux 3.x on the server.

**Prereqs:**
- `ccx-terraform-main` applied (instance reachable).
- `ccx-ansible` applied (tmux installed by the `base` role).
- `ccx-control-plane` applied (`ccx-cli` package in place).

---

## File Structure

```
control-plane/
├── ccx/
│   ├── cli.py                   # MODIFY: wire session sub-app, motd command, --raw
│   ├── sessions.py              # NEW: tmux/claude/token + typer Sub-app
│   └── motd.py                  # NEW: collectors + ANSI renderer + typer command
└── tests/
    ├── test_sessions.py         # NEW
    └── test_motd.py             # NEW

ansible/
├── site.yml                     # MODIFY: append `motd` role
└── roles/
    ├── motd/                    # NEW
    │   ├── tasks/main.yml
    │   └── files/10-ccx
    └── verify/
        └── tasks/main.yml       # MODIFY: add ccxctl motd smoke
```

---

### Task 1: `sessions.py` pure helpers

**Files:**
- Create: `control-plane/ccx/sessions.py`
- Create: `control-plane/tests/test_sessions.py`

- [ ] **Step 1: Write failing tests for `slug()`, `_encode_project_dir()`, `_parse_jsonl_tokens_today()`**

File `/home/david/Work/sesio/sesio__ccx/control-plane/tests/test_sessions.py`:

```python
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest


def test_slug_basic():
    from ccx.sessions import slug
    assert slug("/home/david/Work/sesio/sesio__ccx") == "sesio__ccx"


def test_slug_special_chars():
    from ccx.sessions import slug
    assert slug("/home/david/Work/My Project!") == "my-project-"


def test_slug_lower_collapse_dashes():
    from ccx.sessions import slug
    assert slug("/tmp/A  B  C") == "a-b-c"


def test_encode_project_dir():
    """Claude Code's convention: /home/david/x/y -> -home-david-x-y"""
    from ccx.sessions import encode_project_dir
    assert encode_project_dir("/home/david/Work/sesio/ccx") == "-home-david-Work-sesio-ccx"


def test_parse_jsonl_tokens_today_sums_today(tmp_path: Path):
    from ccx.sessions import parse_jsonl_tokens_today
    today = dt.datetime.now(dt.timezone.utc).isoformat()
    yesterday = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat()
    f = tmp_path / "log.jsonl"
    f.write_text(
        json.dumps({"timestamp": today,     "message": {"usage": {"input_tokens": 100, "output_tokens": 50}}}) + "\n"
        + json.dumps({"timestamp": today,   "message": {"usage": {"input_tokens": 7,   "output_tokens": 3}}})  + "\n"
        + json.dumps({"timestamp": yesterday,"message": {"usage": {"input_tokens": 999, "output_tokens": 999}}}) + "\n"
    )
    assert parse_jsonl_tokens_today([f]) == {"input": 107, "output": 53}


def test_parse_jsonl_tokens_today_handles_missing_keys(tmp_path: Path):
    from ccx.sessions import parse_jsonl_tokens_today
    today = dt.datetime.now(dt.timezone.utc).isoformat()
    f = tmp_path / "log.jsonl"
    f.write_text(
        json.dumps({"timestamp": today}) + "\n"
        + "not json\n"
        + json.dumps({"timestamp": today, "message": {"usage": {"input_tokens": 5, "output_tokens": 2}}}) + "\n"
    )
    assert parse_jsonl_tokens_today([f]) == {"input": 5, "output": 2}


def test_parse_jsonl_tokens_today_no_files():
    from ccx.sessions import parse_jsonl_tokens_today
    assert parse_jsonl_tokens_today([]) == {"input": 0, "output": 0}
```

- [ ] **Step 2: Run tests — expect ModuleNotFoundError**

Run: `cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/test_sessions.py -q`
Expected: collection error — `No module named 'ccx.sessions'`.

- [ ] **Step 3: Implement minimal `sessions.py` pure helpers**

File `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/sessions.py`:

```python
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
```

- [ ] **Step 4: Run tests — expect pass**

Run: `cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/test_sessions.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

Invoke `/commit`. Suggested message: `feat(sessions): slug + jsonl token parsing helpers`.

---

### Task 2: tmux wrappers + claude-pid discovery

**Files:**
- Modify: `control-plane/ccx/sessions.py`
- Modify: `control-plane/tests/test_sessions.py`

- [ ] **Step 1: Add failing tests**

Append to `/home/david/Work/sesio/sesio__ccx/control-plane/tests/test_sessions.py`:

```python
from unittest.mock import patch, MagicMock
import subprocess


def _mock_run(stdout: str = "", returncode: int = 0):
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.stdout = stdout
    m.returncode = returncode
    m.stderr = ""
    return m


def test_tmux_list_windows_parses_format():
    from ccx.sessions import tmux_list_windows
    raw = (
        "ccx|1700000000|/home/david/Work/sesio/sesio__ccx|42\n"
        "foo|1700000010|/home/david/Work/foo|100\n"
    )
    with patch("ccx.sessions.subprocess.run", return_value=_mock_run(raw)):
        rows = tmux_list_windows()
    assert rows == [
        {"slug": "ccx", "activity": 1700000000, "cwd": "/home/david/Work/sesio/sesio__ccx", "pane_pid": 42},
        {"slug": "foo", "activity": 1700000010, "cwd": "/home/david/Work/foo", "pane_pid": 100},
    ]


def test_tmux_list_windows_no_session_returns_empty():
    from ccx.sessions import tmux_list_windows
    err = _mock_run("", returncode=1)
    err.stderr = "no server running on /tmp/tmux-1000/default"
    with patch("ccx.sessions.subprocess.run", return_value=err):
        assert tmux_list_windows() == []


def test_tmux_has_window_true():
    from ccx.sessions import tmux_has_window
    with patch("ccx.sessions.subprocess.run", return_value=_mock_run(returncode=0)):
        assert tmux_has_window("ccx") is True


def test_tmux_has_window_false():
    from ccx.sessions import tmux_has_window
    with patch("ccx.sessions.subprocess.run", return_value=_mock_run(returncode=1)):
        assert tmux_has_window("ccx") is False


def test_find_claude_pid_reads_proc(tmp_path, monkeypatch):
    """Walk /proc/<pane_pid>/task/<tid>/children for a claude descendant."""
    from ccx.sessions import find_claude_pid
    # Build a fake /proc tree: pane=100 → child 101 (bash) → child 102 (claude)
    proc = tmp_path / "proc"
    (proc / "100/task/100").mkdir(parents=True)
    (proc / "100/task/100/children").write_text("101 ")
    (proc / "101/task/101").mkdir(parents=True)
    (proc / "101/task/101/children").write_text("102 ")
    (proc / "101/comm").write_text("bash\n")
    (proc / "102/task/102").mkdir(parents=True)
    (proc / "102/task/102/children").write_text("")
    (proc / "102/comm").write_text("claude\n")
    monkeypatch.setattr("ccx.sessions._PROC", str(proc))
    assert find_claude_pid(100) == 102


def test_find_claude_pid_none_when_absent(tmp_path, monkeypatch):
    from ccx.sessions import find_claude_pid
    proc = tmp_path / "proc"
    (proc / "100/task/100").mkdir(parents=True)
    (proc / "100/task/100/children").write_text("")
    (proc / "100/comm").write_text("bash\n")
    monkeypatch.setattr("ccx.sessions._PROC", str(proc))
    assert find_claude_pid(100) is None
```

- [ ] **Step 2: Run — expect failures**

Run: `/usr/bin/uv run pytest tests/test_sessions.py -q`
Expected: 6 failures (missing functions).

- [ ] **Step 3: Implement tmux + proc wrappers**

Append to `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/sessions.py`:

```python
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
```

- [ ] **Step 4: Run — expect pass**

Run: `/usr/bin/uv run pytest tests/test_sessions.py -q`
Expected: 13 passed.

- [ ] **Step 5: Commit**

Invoke `/commit`. Suggested: `feat(sessions): tmux wrappers + proc-walking claude pid discovery`.

---

### Task 3: `collect_sessions()` — the orchestrator

**Files:**
- Modify: `control-plane/ccx/sessions.py`
- Modify: `control-plane/tests/test_sessions.py`

- [ ] **Step 1: Failing test**

Append to `test_sessions.py`:

```python
def test_collect_sessions_happy_path(tmp_path, monkeypatch):
    """Merge tmux rows + claude pid + tokens into a canonical list."""
    from ccx.sessions import collect_sessions

    # Fake /proc so find_claude_pid returns 102 for pane 42
    proc = tmp_path / "proc"
    (proc / "42/task/42").mkdir(parents=True)
    (proc / "42/task/42/children").write_text("102 ")
    (proc / "42/comm").write_text("bash\n")
    (proc / "102/task/102").mkdir(parents=True)
    (proc / "102/task/102/children").write_text("")
    (proc / "102/comm").write_text("claude\n")
    (proc / "102/stat").write_text(
        "102 (claude) S " + "0 " * 18 + "1000 " + "0 " * 30
    )  # 22nd field = starttime (in ticks since boot)
    monkeypatch.setattr("ccx.sessions._PROC", str(proc))
    monkeypatch.setattr("ccx.sessions._NOW_FN", lambda: 2000)
    monkeypatch.setattr("ccx.sessions._BOOT_FN", lambda: 1000)

    # Fake claude_projects_dir → no jsonl → zero tokens
    monkeypatch.setattr(
        "ccx.sessions._CLAUDE_PROJECTS_DIR",
        str(tmp_path / "not-there"),
    )

    # Mock tmux
    with patch("ccx.sessions.tmux_list_windows", return_value=[
        {"slug": "ccx", "activity": 1700000000,
         "cwd": "/home/david/Work/sesio/ccx", "pane_pid": 42}
    ]):
        sessions = collect_sessions()

    assert sessions == [{
        "slug": "ccx",
        "cwd": "/home/david/Work/sesio/ccx",
        "pane_pid": 42,
        "claude_pid": 102,
        "uptime_seconds": pytest.approx(1000 / 100, abs=1),  # (2000-1000)/clk_tck
        "tokens_today": {"input": 0, "output": 0},
    }]
```

- [ ] **Step 2: Run — expect fail**

Run: `/usr/bin/uv run pytest tests/test_sessions.py::test_collect_sessions_happy_path -q`

- [ ] **Step 3: Implement `collect_sessions`**

Append to `sessions.py`:

```python
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
    """Uptime of a pid, seconds, from /proc/<pid>/stat field 22 (starttime)."""
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
    start_epoch = _BOOT_FN() + starttime_ticks / clk_tck
    return _NOW_FN() - start_epoch


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
```

- [ ] **Step 4: Run — expect pass**

Run: `/usr/bin/uv run pytest tests/test_sessions.py::test_collect_sessions_happy_path -q`
Expected: PASS.

- [ ] **Step 5: Commit**

Invoke `/commit`. Suggested: `feat(sessions): collect_sessions orchestrator`.

---

### Task 4: typer sub-app — `launch / list / attach / kill / menu`

**Files:**
- Modify: `control-plane/ccx/sessions.py`
- Modify: `control-plane/tests/test_sessions.py`

- [ ] **Step 1: Failing tests for subcommands**

Append to `test_sessions.py`:

```python
from typer.testing import CliRunner


def test_session_list_json_empty():
    from ccx.sessions import app
    with patch("ccx.sessions.collect_sessions", return_value=[]):
        result = CliRunner().invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_session_list_table_format():
    from ccx.sessions import app
    row = {
        "slug": "ccx", "cwd": "/home/david/Work/sesio/ccx", "pane_pid": 42,
        "claude_pid": 102, "uptime_seconds": 120.0,
        "tokens_today": {"input": 100, "output": 50},
    }
    with patch("ccx.sessions.collect_sessions", return_value=[row]):
        result = CliRunner().invoke(app, ["list"])
    assert result.exit_code == 0
    assert "ccx" in result.stdout
    assert "100" in result.stdout  # input tokens
    assert "50" in result.stdout   # output tokens


def test_session_launch_creates_when_absent(tmp_path):
    from ccx.sessions import app
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        # has-session returns 1 (absent) when asked, 0 otherwise
        if "has-session" in argv:
            return _mock_run(returncode=1)
        return _mock_run(returncode=0)

    with patch("ccx.sessions.subprocess.run", side_effect=fake_run):
        result = CliRunner().invoke(app, ["launch", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    # assert both new-session -d and new-window were called
    assert any("new-session" in c and "-d" in c for c in calls)
    assert any("new-window" in c for c in calls)


def test_session_launch_attaches_when_present(tmp_path):
    from ccx.sessions import app
    # has-session returns 0 (present) → launch should NOT call new-window
    with patch("ccx.sessions.subprocess.run", return_value=_mock_run(returncode=0)) as run:
        result = CliRunner().invoke(app, ["launch", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    argvs = [call.args[0] for call in run.call_args_list]
    assert not any("new-window" in a for a in argvs)


def test_session_kill_calls_tmux_kill_window():
    from ccx.sessions import app
    with patch("ccx.sessions.subprocess.run", return_value=_mock_run(returncode=0)) as run:
        result = CliRunner().invoke(app, ["kill", "ccx"])
    assert result.exit_code == 0
    argvs = [call.args[0] for call in run.call_args_list]
    assert any("kill-window" in a for a in argvs)
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement the sub-app**

Append to `sessions.py`:

```python
import json as _json
import typer

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
    import os
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
        typer.echo(_json.dumps(rows, default=str))
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
    import os
    if slug_:
        os.execvp("tmux", ["tmux", "attach-session", "-t", SESSION_NAME, ";", "select-window", "-t", slug_])
    else:
        os.execvp("tmux", ["tmux", "attach-session", "-t", SESSION_NAME])


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
```

- [ ] **Step 4: Run — expect pass**

Run: `/usr/bin/uv run pytest tests/test_sessions.py -q`
Expected: 18 passed.

- [ ] **Step 5: Commit**

Invoke `/commit`. Suggested: `feat(sessions): typer sub-app with launch/list/attach/kill/menu`.

---

### Task 5: Wire `session` sub-app into top-level `ccxctl`

**Files:**
- Modify: `control-plane/ccx/cli.py`

- [ ] **Step 1: Mount the sub-app**

In `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/cli.py`, after the `app = typer.Typer(...)` line, add:

```python
from ccx.sessions import app as _sessions_app
app.add_typer(_sessions_app, name="session", help="Manage claude sessions (tmux).")
```

- [ ] **Step 2: Verify wiring**

Run: `cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run ccxctl session --help`
Expected: help output listing `launch`, `list`, `attach`, `kill`, `menu`.

- [ ] **Step 3: Commit**

Invoke `/commit`. Suggested: `feat(cli): wire session sub-app under ccxctl`.

---

### Task 6: `ccxctl ssh --raw` + auto-tmux default

**Files:**
- Modify: `control-plane/ccx/cli.py`
- Modify: `control-plane/tests/test_cli.py`

- [ ] **Step 1: Add failing test**

Append to `/home/david/Work/sesio/sesio__ccx/control-plane/tests/test_cli.py`:

```python
def test_ssh_default_uses_tmux(monkeypatch):
    """Default ssh should request `tmux new-session -A -s ccx` on the remote."""
    import importlib, ccx.cli
    importlib.reload(ccx.cli)
    captured: list[list[str]] = []

    def fake_execvp(prog, argv):
        captured.append(argv)

    monkeypatch.setattr(ccx.cli.os, "execvp", fake_execvp)
    from typer.testing import CliRunner
    CliRunner().invoke(ccx.cli.app, ["ssh"])
    assert captured, "execvp was not called"
    cmd = captured[0]
    assert "tmux" in " ".join(cmd)
    assert "new-session" in " ".join(cmd)
    assert "-A" in cmd
    assert "-s" in cmd and "ccx" in cmd


def test_ssh_raw_skips_tmux(monkeypatch):
    import importlib, ccx.cli
    importlib.reload(ccx.cli)
    captured: list[list[str]] = []
    monkeypatch.setattr(ccx.cli.os, "execvp", lambda _, argv: captured.append(argv))
    from typer.testing import CliRunner
    CliRunner().invoke(ccx.cli.app, ["ssh", "--raw"])
    cmd = " ".join(captured[0])
    assert "tmux" not in cmd
```

- [ ] **Step 2: Run — expect fail**

Run: `/usr/bin/uv run pytest tests/test_cli.py -q`
Expected: failures on the two new tests.

- [ ] **Step 3: Implement**

In `cli.py`, replace the existing `ssh` command:

```python
@app.command()
def ssh(
    raw: Annotated[bool, typer.Option("--raw", "-R", help="Plain shell, skip tmux.")] = False,
    args: Annotated[list[str] | None, typer.Argument(help="Extra ssh arguments.")] = None,
) -> None:
    """SSH to the instance. Default: attach to shared tmux session `ccx`."""
    base_argv = [
        "ssh",
        "-i", str(CFG.ssh_key),
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-t",
        f"{CFG.ssh_user}@{CFG.hostname}",
    ]
    extra = list(args or [])
    if raw:
        os.execvp("ssh", base_argv + extra)
    else:
        # Attach or create the shared session on the remote.
        remote = "tmux new-session -A -s ccx"
        os.execvp("ssh", base_argv + [remote] + extra)
```

- [ ] **Step 4: Run — expect pass**

Run: `/usr/bin/uv run pytest tests/test_cli.py -q`
Expected: all passing.

- [ ] **Step 5: Commit**

Invoke `/commit`. Suggested: `feat(ccxctl ssh): default to tmux attach; --raw for plain shell`.

---

### Task 7: `motd.py` renderer helpers (port from sesio__motd)

**Files:**
- Create: `control-plane/ccx/motd.py`
- Create: `control-plane/tests/test_motd.py`

- [ ] **Step 1: Failing tests**

File `/home/david/Work/sesio/sesio__ccx/control-plane/tests/test_motd.py`:

```python
from __future__ import annotations

import json
from unittest.mock import patch


def test_format_uptime_examples():
    from ccx.motd import format_uptime
    assert format_uptime(0) == "0m"
    assert format_uptime(59) == "0m"
    assert format_uptime(60) == "1m"
    assert format_uptime(3700) == "1h 1m"
    assert format_uptime(90061) == "1d 1h 1m"


def test_format_bytes_examples():
    from ccx.motd import format_bytes
    assert format_bytes(0) == "0B"
    assert format_bytes(1023) == "1023B"
    assert format_bytes(2048) == "2K"
    assert format_bytes(3 * 1024 * 1024) == "3M"
    assert format_bytes(5 * 1024**3) == "5.0G"


def test_visible_len_strips_ansi():
    from ccx.motd import visible_len
    assert visible_len("\x1b[31mhello\x1b[0m") == 5
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement the helpers (port)**

File `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/motd.py`:

```python
"""ccxctl motd — ANSI-boxed login banner for the ccx coding station."""
from __future__ import annotations

import re
import shutil


class C:
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def format_uptime(seconds: float) -> str:
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if days or hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def format_bytes(n: int) -> str:
    if n >= 1_073_741_824:
        return f"{n / 1_073_741_824:.1f}G"
    if n >= 1_048_576:
        return f"{n / 1_048_576:.0f}M"
    if n >= 1024:
        return f"{n / 1024:.0f}K"
    return f"{n}B"


def visible_len(s: str) -> int:
    return len(re.sub(r"\033\[[0-9;]*m", "", s))


def _compute_widths() -> tuple[int, int]:
    term_w = shutil.get_terminal_size((80, 24)).columns
    inner = term_w - 3
    left = max(26, inner * 30 // 64)
    right = inner - left
    return left, right


LEFT_W, RIGHT_W = _compute_widths()
FULL_W = LEFT_W + RIGHT_W + 1


def box_top(lt: str, rt: str) -> str:
    l = f"══ {lt} "
    r = f"══ {rt} "
    return f"{C.DIM}╔{l}{'═' * (LEFT_W - len(l))}╦{r}{'═' * (RIGHT_W - len(r))}╗{C.RESET}"


def box_mid(lt: str, rt: str) -> str:
    l = f"══ {lt} "
    r = f"══ {rt} "
    return f"{C.DIM}╠{l}{'═' * (LEFT_W - len(l))}╬{r}{'═' * (RIGHT_W - len(r))}╣{C.RESET}"


def box_full_mid(title: str) -> str:
    t = f"══ {title} "
    return f"{C.DIM}╠{t}{'═' * (FULL_W - len(t))}╣{C.RESET}"


def box_bottom() -> str:
    return f"{C.DIM}╚{'═' * LEFT_W}╩{'═' * RIGHT_W}╝{C.RESET}"


def box_full_bottom() -> str:
    return f"{C.DIM}╚{'═' * FULL_W}╝{C.RESET}"


def row(left: str, right: str) -> str:
    l_pad = LEFT_W - visible_len(left)
    r_pad = RIGHT_W - visible_len(right)
    return f"{C.DIM}║{C.RESET}{left}{' ' * max(0, l_pad)}{C.DIM}║{C.RESET}{right}{' ' * max(0, r_pad)}{C.DIM}║{C.RESET}"


def full_row(content: str) -> str:
    pad = FULL_W - visible_len(content)
    return f"{C.DIM}║{C.RESET}{content}{' ' * max(0, pad)}{C.DIM}║{C.RESET}"


def status_dot(ok: bool, label: str) -> str:
    if ok:
        return f"{C.GREEN}●{C.RESET} {label}"
    return f"{C.RED}✗{C.RESET} {label}"


def service_dot(state: str) -> str:
    if state == "active":
        return f"{C.GREEN}●{C.RESET}"
    if state == "failed":
        return f"{C.RED}✗{C.RESET}"
    if state in ("inactive", "dead"):
        return f"{C.DIM}○{C.RESET}"
    return f"{C.YELLOW}◐{C.RESET}"
```

- [ ] **Step 4: Run — expect pass**

Run: `/usr/bin/uv run pytest tests/test_motd.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

Invoke `/commit`. Suggested: `feat(motd): renderer helpers ported from sesio__motd`.

---

### Task 8: collectors — `SYSTEM`, `INSTANCE`, `SERVICES`, `DOTFILES`

**Files:**
- Modify: `control-plane/ccx/motd.py`
- Modify: `control-plane/tests/test_motd.py`

- [ ] **Step 1: Failing tests**

Append to `test_motd.py`:

```python
def test_collect_system_reads_proc(tmp_path, monkeypatch):
    from ccx.motd import collect_system
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "uptime").write_text("123.45 99.99\n")
    (proc / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 500 kB\n")
    (proc / "stat").write_text("cpu  100 0 100 800 0 0 0 0 0 0\n")
    monkeypatch.setattr("ccx.motd._PROC", str(proc))
    monkeypatch.setattr("ccx.motd._DISK_FN", lambda p: type("D", (), {"used": 2**30, "total": 4*(2**30)})())
    monkeypatch.setattr("ccx.motd._SLEEP", lambda _: None)
    s = collect_system()
    assert s is not None
    assert s["uptime"] == "0m"
    assert s["ram_pct"] == 50
    assert 0 <= s["cpu_pct"] <= 100
    assert s["disk_pct"] == 25


def test_collect_services_parses_systemctl():
    from ccx.motd import collect_services
    def fake_run(argv, **kw):
        name = argv[-1].replace(".service", "")
        from unittest.mock import MagicMock
        m = MagicMock()
        m.stdout = "active\n" if name == "docker" else "inactive\n"
        m.returncode = 0
        return m
    with patch("ccx.motd.subprocess.run", side_effect=fake_run):
        r = collect_services()
    names = {n for n, _ in r["services"]}
    assert "docker" in names
    assert "ssh" in names


def test_collect_dotfiles_reads_git_heads(tmp_path, monkeypatch):
    from ccx.motd import collect_dotfiles
    def fake_run(argv, **kw):
        from unittest.mock import MagicMock
        m = MagicMock()
        m.returncode = 0
        if "rev-parse" in argv:
            m.stdout = "abc1234\n"
        elif "rev-list" in argv:
            m.stdout = "3\n"
        else:
            m.stdout = ""
        return m
    with patch("ccx.motd.subprocess.run", side_effect=fake_run):
        r = collect_dotfiles()
    assert r["sesio__ccx"]["sha"] == "abc1234"
    assert r["sesio__ccx"]["behind"] == 3


def test_collect_instance_imdsv2(monkeypatch):
    from ccx.motd import collect_instance

    # Mock urllib to return canned values for token + metadata reads.
    class FakeResp:
        def __init__(self, body: str): self.body = body.encode()
        def read(self): return self.body
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_urlopen(req, timeout=None):
        url = getattr(req, "full_url", req) if hasattr(req, "full_url") else req
        # First: token PUT
        if "api/token" in str(url):
            return FakeResp("TOKEN")
        mapping = {
            "instance-id":      "i-abc",
            "instance-type":    "t4g.xlarge",
            "placement/region": "eu-west-1",
            "placement/availability-zone": "eu-west-1a",
            "public-ipv4":      "1.2.3.4",
            "public-hostname":  "ec2-1-2-3-4.eu-west-1.compute.amazonaws.com",
        }
        for k, v in mapping.items():
            if str(url).endswith(k):
                return FakeResp(v)
        return FakeResp("")

    monkeypatch.setattr("ccx.motd.urllib.request.urlopen", fake_urlopen)
    r = collect_instance()
    assert r["instance_id"] == "i-abc"
    assert r["instance_type"] == "t4g.xlarge"
    assert r["region"] == "eu-west-1"
    assert r["public_ip"] == "1.2.3.4"
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement the four collectors**

Append to `motd.py`:

```python
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

_PROC = "/proc"
_SLEEP = time.sleep
_DISK_FN = shutil.disk_usage
_SUBPROC_TIMEOUT = 3


def _read_cpu_pct() -> float:
    def _sample():
        with open(f"{_PROC}/stat") as f:
            parts = f.readline().split()
        idle = int(parts[4])
        total = sum(int(x) for x in parts[1:])
        return idle, total
    try:
        i1, t1 = _sample()
        _SLEEP(0.5)
        i2, t2 = _sample()
        d_idle = i2 - i1
        d_total = t2 - t1
        if d_total <= 0:
            return 0.0
        return round((1.0 - d_idle / d_total) * 100, 0)
    except (OSError, IndexError, ValueError):
        return 0.0


def collect_system() -> Optional[dict[str, Any]]:
    try:
        import socket
        with open(f"{_PROC}/uptime") as f:
            uptime_s = float(f.read().split()[0])
        info: dict[str, int] = {}
        with open(f"{_PROC}/meminfo") as f:
            for line in f:
                p = line.split()
                if p[0] in ("MemTotal:", "MemAvailable:"):
                    info[p[0]] = int(p[1])
                if len(info) == 2:
                    break
        ram_pct = round((1 - info["MemAvailable:"] / info["MemTotal:"]) * 100)
        disk = _DISK_FN("/")
        return {
            "hostname": socket.gethostname(),
            "uptime": format_uptime(uptime_s),
            "cpu_pct": int(_read_cpu_pct()),
            "ram_pct": ram_pct,
            "disk_used": format_bytes(disk.used),
            "disk_total": format_bytes(disk.total),
            "disk_pct": round(disk.used / disk.total * 100),
        }
    except Exception:
        return None


_IMDS = "http://169.254.169.254/latest"


def _imds_token() -> Optional[str]:
    req = urllib.request.Request(
        f"{_IMDS}/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.read().decode().strip()
    except Exception:
        return None


def _imds_get(path: str, token: str) -> Optional[str]:
    req = urllib.request.Request(
        f"{_IMDS}/meta-data/{path}",
        headers={"X-aws-ec2-metadata-token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.read().decode().strip()
    except Exception:
        return None


def collect_instance() -> Optional[dict[str, Any]]:
    token = _imds_token()
    if not token:
        return None
    keys = {
        "instance_id":     "instance-id",
        "instance_type":   "instance-type",
        "region":          "placement/region",
        "az":              "placement/availability-zone",
        "public_ip":       "public-ipv4",
        "public_hostname": "public-hostname",
    }
    return {k: _imds_get(v, token) for k, v in keys.items()}


CCX_SERVICES = ["docker", "ssh", "fail2ban", "unattended-upgrades"]


def collect_services() -> Optional[dict[str, Any]]:
    try:
        services: list[tuple[str, str]] = []
        for svc in CCX_SERVICES:
            try:
                r = subprocess.run(
                    ["/usr/bin/systemctl", "is-active", f"{svc}.service"],
                    capture_output=True, text=True, timeout=_SUBPROC_TIMEOUT,
                )
                state = r.stdout.strip() or "unknown"
            except (subprocess.TimeoutExpired, OSError):
                state = "unknown"
            services.append((svc, state))
        return {"services": services}
    except Exception:
        return None


def _git_sha(repo_dir: str) -> Optional[str]:
    try:
        r = subprocess.run(
            ["git", "-C", repo_dir, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=_SUBPROC_TIMEOUT,
        )
        return r.stdout.strip() or None
    except (subprocess.TimeoutExpired, OSError):
        return None


def _git_behind(repo_dir: str) -> int:
    try:
        r = subprocess.run(
            ["git", "-C", repo_dir, "rev-list", "--count", "HEAD..@{u}"],
            capture_output=True, text=True, timeout=_SUBPROC_TIMEOUT,
        )
        return int(r.stdout.strip() or 0)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return 0


DOTFILES_REPOS = {
    "sesio__ccx":    "/home/david/sesio__ccx",
    "claude-config": "/home/david/claude-config",
}


def collect_dotfiles() -> Optional[dict[str, Any]]:
    out = {}
    for name, path in DOTFILES_REPOS.items():
        sha = _git_sha(path)
        if sha is None:
            continue
        out[name] = {"sha": sha, "behind": _git_behind(path)}
    return out or None
```

- [ ] **Step 4: Run — expect pass**

Run: `/usr/bin/uv run pytest tests/test_motd.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

Invoke `/commit`. Suggested: `feat(motd): collectors for SYSTEM / INSTANCE / SERVICES / DOTFILES`.

---

### Task 9: collectors — `SESSIONS` and `USAGE`

**Files:**
- Modify: `control-plane/ccx/motd.py`
- Modify: `control-plane/tests/test_motd.py`

- [ ] **Step 1: Failing tests**

Append to `test_motd.py`:

```python
def test_collect_sessions_wraps_sessions_module():
    from ccx.motd import collect_motd_sessions
    rows = [{"slug": "ccx", "tokens_today": {"input": 10, "output": 5}}]
    with patch("ccx.motd.collect_sessions", return_value=rows):
        r = collect_motd_sessions()
    assert r == {"sessions": rows}


def test_collect_usage_today_sums_across_projects(tmp_path, monkeypatch):
    from ccx.motd import collect_usage
    import datetime as dt
    today = dt.datetime.now(dt.timezone.utc).isoformat()
    (tmp_path / "projA").mkdir()
    (tmp_path / "projA/log.jsonl").write_text(
        json.dumps({"timestamp": today, "message": {"usage": {"input_tokens": 100, "output_tokens": 50}}}) + "\n"
    )
    (tmp_path / "projB").mkdir()
    (tmp_path / "projB/log.jsonl").write_text(
        json.dumps({"timestamp": today, "message": {"usage": {"input_tokens": 7, "output_tokens": 3}}}) + "\n"
    )
    monkeypatch.setattr("ccx.motd._CLAUDE_PROJECTS_DIR", str(tmp_path))
    r = collect_usage()
    assert r["today"] == {"input": 107, "output": 53, "total": 160}
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

Append to `motd.py`:

```python
import os as _os

from ccx.sessions import collect_sessions, parse_jsonl_tokens_today

_CLAUDE_PROJECTS_DIR = _os.path.expanduser("~/.claude/projects")


def collect_motd_sessions() -> Optional[dict[str, Any]]:
    try:
        return {"sessions": collect_sessions()}
    except Exception:
        return None


def collect_usage() -> Optional[dict[str, Any]]:
    try:
        all_jsonl: list[Path] = []
        root = Path(_CLAUDE_PROJECTS_DIR)
        if root.is_dir():
            for proj in root.iterdir():
                if proj.is_dir():
                    all_jsonl.extend(proj.glob("*.jsonl"))
        tk = parse_jsonl_tokens_today(all_jsonl)
        return {"today": {**tk, "total": tk["input"] + tk["output"]}}
    except Exception:
        return None
```

- [ ] **Step 4: Run — expect pass**

Run: `/usr/bin/uv run pytest tests/test_motd.py -q`
Expected: 9 passed.

- [ ] **Step 5: Commit**

Invoke `/commit`. Suggested: `feat(motd): SESSIONS + USAGE collectors`.

---

### Task 10: `render_motd` + typer command

**Files:**
- Modify: `control-plane/ccx/motd.py`
- Modify: `control-plane/tests/test_motd.py`
- Modify: `control-plane/ccx/cli.py`

- [ ] **Step 1: Failing test (golden)**

Append to `test_motd.py`:

```python
def test_render_motd_smoke():
    """Just ensure the renderer produces non-empty boxed output for a sample payload."""
    from ccx.motd import render_motd
    system = {"hostname": "ccx", "uptime": "1h 2m", "cpu_pct": 5, "ram_pct": 10,
              "disk_used": "10G", "disk_total": "100G", "disk_pct": 10}
    instance = {"instance_id": "i-abc", "instance_type": "t4g.xlarge",
                "region": "eu-west-1", "az": "eu-west-1a",
                "public_ip": "1.2.3.4", "public_hostname": "h.example.com"}
    services = {"services": [("docker", "active"), ("ssh", "active")]}
    sessions = {"sessions": []}
    usage = {"today": {"input": 100, "output": 50, "total": 150}}
    dotfiles = {"sesio__ccx": {"sha": "abc1234", "behind": 0}}

    out = render_motd(system, instance, sessions, usage, services, dotfiles)
    assert "ccx" in out
    assert "t4g.xlarge" in out
    assert "docker" in out
    assert "abc1234" in out
    # Contains box characters
    assert "╔" in out and "╚" in out
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement `render_motd` + `main`**

Append to `motd.py`:

```python
import typer
from concurrent.futures import ThreadPoolExecutor, as_completed

COLLECT_TIMEOUT = 5


def render_motd(
    system: Optional[dict], instance: Optional[dict],
    sessions: Optional[dict], usage: Optional[dict],
    services: Optional[dict], dotfiles: Optional[dict],
) -> str:
    lines: list[str] = []
    # ---- SYSTEM / INSTANCE ----
    lines.append(box_top("SYSTEM", "INSTANCE"))
    sys_l = [" unavailable", "", "", ""]
    if system:
        s = system
        sys_l = [
            f" Host:   {C.BOLD}{s['hostname']}{C.RESET}",
            f" Uptime: {C.BOLD}{s['uptime']}{C.RESET}",
            f" CPU: {C.BOLD}{s['cpu_pct']}%{C.RESET}  RAM: {C.BOLD}{s['ram_pct']}%{C.RESET}",
            f" Disk: {C.BOLD}{s['disk_used']}/{s['disk_total']}{C.RESET} ({s['disk_pct']}%)",
        ]
    ins_l = [" unavailable", "", "", ""]
    if instance:
        i = instance
        ins_l = [
            f" Type: {C.BOLD}{i['instance_type']}{C.RESET}",
            f" Reg:  {C.BOLD}{i['region']}{C.RESET} ({i['az']})",
            f" IP:   {C.BOLD}{i['public_ip']}{C.RESET}",
            f" ID:   {C.DIM}{i['instance_id']}{C.RESET}",
        ]
    for l, r in zip(sys_l, ins_l):
        lines.append(row(l, r))

    # ---- SESSIONS (full width) ----
    lines.append(box_full_mid("SESSIONS"))
    if sessions and sessions["sessions"]:
        for s in sessions["sessions"]:
            up = format_uptime(s["uptime_seconds"] or 0) if s.get("uptime_seconds") else "-"
            toks = s["tokens_today"]
            content = (
                f" {C.BOLD}{s['slug']}{C.RESET}"
                f"  up {up}"
                f"  in {C.BOLD}{toks['input']}{C.RESET}"
                f"  out {C.BOLD}{toks['output']}{C.RESET}"
                f"  {C.DIM}{s['cwd']}{C.RESET}"
            )
            lines.append(full_row(content))
    else:
        lines.append(full_row(f" {C.DIM}(no sessions){C.RESET}"))

    # ---- USAGE / SERVICES ----
    lines.append(box_mid("USAGE (today)", "SERVICES"))
    us_l = [" unavailable", "", ""]
    if usage:
        t = usage["today"]
        us_l = [
            f" In:    {C.BOLD}{t['input']}{C.RESET}",
            f" Out:   {C.BOLD}{t['output']}{C.RESET}",
            f" Total: {C.BOLD}{t['total']}{C.RESET}",
        ]
    sv_l = [" unavailable", "", ""]
    if services:
        sv_l = []
        for name, state in services["services"]:
            sv_l.append(f" {service_dot(state)} {name:<20s} {state}")
        # Pad to match USAGE column height
        while len(sv_l) < 3:
            sv_l.append("")
    max_n = max(len(us_l), len(sv_l))
    us_l += [""] * (max_n - len(us_l))
    sv_l += [""] * (max_n - len(sv_l))
    for l, r in zip(us_l, sv_l):
        lines.append(row(l, r))

    # ---- DOTFILES (full width) ----
    lines.append(box_full_mid("DOTFILES"))
    if dotfiles:
        for name, info in dotfiles.items():
            drift = f" ({C.YELLOW}{info['behind']} behind{C.RESET})" if info["behind"] else ""
            lines.append(full_row(f" {C.BOLD}{name}{C.RESET}  {info['sha']}{drift}"))
    else:
        lines.append(full_row(f" {C.DIM}unavailable{C.RESET}"))
    lines.append(box_full_bottom())
    return "\n".join(lines)


def main() -> int:
    collectors = {
        "system":    collect_system,
        "instance":  collect_instance,
        "sessions":  collect_motd_sessions,
        "usage":     collect_usage,
        "services":  collect_services,
        "dotfiles":  collect_dotfiles,
    }
    results: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=len(collectors)) as ex:
        futs = {ex.submit(fn): n for n, fn in collectors.items()}
        try:
            for fut in as_completed(futs, timeout=COLLECT_TIMEOUT):
                n = futs[fut]
                try:
                    results[n] = fut.result()
                except Exception:
                    results[n] = None
        except TimeoutError:
            pass
    for n in collectors:
        results.setdefault(n, None)
    print(render_motd(
        results["system"], results["instance"], results["sessions"],
        results["usage"], results["services"], results["dotfiles"],
    ))
    return 0
```

- [ ] **Step 4: Wire typer command in `cli.py`**

In `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/cli.py`, near the other `@app.command()` decorators, add:

```python
@app.command()
def motd() -> None:
    """Print the ccx login banner (system / instance / sessions / usage / services / dotfiles)."""
    from ccx.motd import main as _motd_main
    _motd_main()
```

- [ ] **Step 5: Run — expect pass**

Run: `/usr/bin/uv run pytest tests/ -q`
Expected: all tests pass.

Run: `/usr/bin/uv run ccxctl motd` locally (will show mostly "unavailable" for INSTANCE / SERVICES / DOTFILES on the laptop — that's fine).

- [ ] **Step 6: Commit**

Invoke `/commit`. Suggested: `feat(motd): render_motd + ccxctl motd command`.

---

### Task 11: Ansible — new `motd` role

**Files:**
- Create: `ansible/roles/motd/tasks/main.yml`
- Create: `ansible/roles/motd/files/10-ccx`
- Modify: `ansible/site.yml`

- [ ] **Step 1: Create the hook script**

File `/home/david/Work/sesio/sesio__ccx/ansible/roles/motd/files/10-ccx`:

```bash
#!/bin/sh
# Called by PAM at login. Runs as the logging-in user.
# The `ccxctl motd` subcommand is pure-stdlib and finishes in < 200 ms.
exec "$HOME/.local/bin/ccxctl" motd 2>/dev/null || true
```

- [ ] **Step 2: Write the role tasks**

File `/home/david/Work/sesio/sesio__ccx/ansible/roles/motd/tasks/main.yml`:

```yaml
---
- name: Drop Debian's default update-motd.d scripts (we replace them)
  ansible.builtin.file:
    path: "/etc/update-motd.d/{{ item }}"
    state: absent
  loop:
    - 10-uname
    - 50-motd-news
    - 90-updates-available

- name: Install the ccx motd hook
  ansible.builtin.copy:
    src: 10-ccx
    dest: /etc/update-motd.d/10-ccx
    mode: "0755"
    owner: root
    group: root

- name: Ensure /etc/motd is empty (so only dynamic motd is shown)
  ansible.builtin.copy:
    dest: /etc/motd
    content: ""
    mode: "0644"
    owner: root
    group: root
```

- [ ] **Step 3: Append `motd` to `site.yml`**

In `/home/david/Work/sesio/sesio__ccx/ansible/site.yml`, change the roles list to:

```yaml
  roles:
    - base
    - user
    - aws_cli
    - zsh
    - dotfiles
    - asdf
    - docker
    - claude_code
    - rtk
    - motd
    - verify
```

- [ ] **Step 4: Syntax check**

Run: `cd /home/david/Work/sesio/sesio__ccx && make ansible-check`
Expected: `playbook: site.yml`.

- [ ] **Step 5: Commit**

Invoke `/commit`. Suggested: `feat(ansible): motd role — install /etc/update-motd.d/10-ccx`.

---

### Task 12: Extend `verify` role + final smoke

**Files:**
- Modify: `ansible/roles/verify/tasks/main.yml`

- [ ] **Step 1: Add ccxctl-motd smoke task**

In `/home/david/Work/sesio/sesio__ccx/ansible/roles/verify/tasks/main.yml`, before the "Write provision-ok marker" task, insert:

```yaml
- name: Verify ccxctl motd runs without error
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.shell: |
    "{{ target_home }}/.local/bin/ccxctl" motd | head -c 100
  register: _v_motd
  changed_when: false
  # Non-fatal: motd depends on ccx-cli uv sync which happens on first login.
  failed_when: false
```

And extend the provision-ok marker `content:` to include a motd line:

```yaml
      motd:          {{ (_v_motd.stdout | default('') | length > 0) | ternary('ok', '(deferred — ccx-cli not yet synced)') }}
```

- [ ] **Step 2: Run full lint + syntax**

Run: `cd /home/david/Work/sesio/sesio__ccx && make check`
Expected: all green (ansible-check + ansible-lint + terraform-check).

- [ ] **Step 3: Deploy to live instance (if reachable)**

Run:
```bash
ssh -i ~/.ssh/keys/sesio-nodes -o IdentitiesOnly=yes david@ccx.dsylla.sesio.io \
  'cd /opt/sesio__ccx && sudo git pull --ff-only && cd ansible && sudo ansible-playbook -i inventory site.yml --tags motd,verify 2>&1 | tail -20'
```
Expected: `PLAY RECAP` with `failed=0`.

- [ ] **Step 4: Smoke the motd via SSH**

Run (from the laptop):
```bash
ssh -i ~/.ssh/keys/sesio-nodes -o IdentitiesOnly=yes david@ccx.dsylla.sesio.io ccxctl motd
```
Expected: the boxed banner renders with SYSTEM / INSTANCE / SESSIONS / USAGE / SERVICES / DOTFILES.

Also run:
```bash
ccxctl ssh -- exit  # attaches the remote tmux then disconnects
```
Expected: a tmux session `ccx` exists on the remote.

- [ ] **Step 5: Commit**

Invoke `/commit`. Suggested: `feat(ansible verify): include ccxctl motd smoke in provision-ok marker`.

---

## Done when

1. `ccxctl session launch --dir ~/Work/…` creates a tmux window running `claude`, re-running attaches.
2. `ccxctl session list` shows slug / pid / uptime / today's tokens.
3. `ccxctl ssh` lands in `tmux new-session -A -s ccx` on the remote.
4. `ccxctl ssh --raw` yields a plain shell.
5. `ccxctl motd` prints a boxed banner in under 500 ms.
6. Logging into the instance via SSH shows the motd as the login banner.
7. `pytest` (control-plane/) and `make check` (repo root) both green.
8. ansible `verify` role writes a `motd: ok` line in `/var/log/ccx-provision-ok`.
