# `ccxd` Deployment Implementation Plan (Plan 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ccxd` actually usable on the laptop end-to-end. Ship a hook emitter (claude-code → daemon DGRAM), idempotent `ccxctl ccxd install-hooks` / `uninstall-hooks` that edits `~/.claude/settings.json`, a systemd user unit + thin status/logs/restart wrappers, and `ccxctl ccxd query` so the user can prove it all works without writing Python in a REPL.

**Architecture:** Three new pieces in the existing `control-plane` package. (1) `ccx.ccxd.hook_emitter`: tiny `python -m` entrypoint that reads stdin JSON, prepends `{"event": ..., "payload": ...}`, and DGRAM-sends to `$XDG_RUNTIME_DIR/ccxd-hooks.sock` with a 50 ms timeout — drops on any error so Claude Code never blocks on a missing daemon. (2) New `ccx.ccxd_cli` typer-app exposing `install-hooks`, `uninstall-hooks`, `install-service`, `status`, `logs`, `restart`, `stop`, `query`; mounted as `ccxctl ccxd ...`. (3) A systemd user unit at `etc/ccxd.service` (in-repo template) that `install-service` materializes into `~/.config/systemd/user/`. Storage abstractions, daemon internals, and clients stay untouched.

**Tech Stack:** Python stdlib only (`json`, `socket`, `subprocess`, `pathlib`); existing `typer`. No new deps. Tests use `tmp_path` fixtures + `monkeypatch` for `HOME` / `XDG_RUNTIME_DIR`.

**Working directory:** `/home/david/Work/sesio/sesio__ccx`

---

## File Structure

```
sesio__ccx/
├── control-plane/
│   ├── pyproject.toml                    # MODIFY: add `ccxd-emit-hook` console script (optional)
│   ├── etc/
│   │   └── ccxd.service                  # CREATE: systemd user unit template
│   ├── ccx/
│   │   ├── ccxd/
│   │   │   └── hook_emitter.py           # CREATE: stdin → DGRAM emitter
│   │   ├── ccxd_cli.py                   # CREATE: ccxctl ccxd ... typer subcommands
│   │   └── cli.py                        # MODIFY: mount ccxd_cli as `ccxd` typer subcommand
│   └── tests/
│       └── ccxd/
│           ├── test_hook_emitter.py      # CREATE
│           └── test_ccxd_cli.py          # CREATE
└── docs/superpowers/plans/2026-04-30-ccxd-deployment.md   # this file
```

**Boundaries:**
- `hook_emitter.py` is a separate process that runs once per Claude Code hook fire. It must NEVER raise to its caller — the spec gives it a 50 ms budget and silent-drop semantics.
- `ccxd_cli.py` only manipulates `~/.claude/settings.json`, `~/.config/systemd/user/ccxd.service`, and shells out to `systemctl --user`. It does not import daemon internals (importing `ccxd.__main__` runs argparse on import-time `sys.argv` — keep them decoupled).
- `etc/ccxd.service` is a string template; never a hand-edited file under `~/.config/systemd/user/` is the source of truth.

---

## Prerequisites

- Plans 1 + 2 are landed (`main` has the V1+V2 ccxd commits, currently 7 unpushed but verified).
- Laptop has systemd in user mode (`systemctl --user` works). Verify: `systemctl --user status` exits 0.
- `~/.claude/settings.json` already exists.
- `claude-bedrock` / `claude-sub` / `claude-provider` already define the pattern of refusing inside `CLAUDECODE=1` — borrow the same guard for `install-hooks` / `uninstall-hooks` (live-reload of settings.json corrupts the running session).

---

### Task 1: Hook emitter (`ccx.ccxd.hook_emitter`)

**Why first:** Without this, `install-hooks` has nothing to point at.

**Files:**
- Create: `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/ccxd/hook_emitter.py`
- Create: `/home/david/Work/sesio/sesio__ccx/control-plane/tests/ccxd/test_hook_emitter.py`

- [x] **Step 1: Write the failing test**

Create `control-plane/tests/ccxd/test_hook_emitter.py`:

```python
"""Tests for ccx.ccxd.hook_emitter — claude-code -> ccxd DGRAM bridge."""
from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest


def _spawn_emitter(event: str, payload: dict, runtime_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ccx.ccxd.hook_emitter", event],
        input=json.dumps(payload),
        text=True,
        env={"XDG_RUNTIME_DIR": str(runtime_dir), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        timeout=2,
    )


def test_emitter_sends_dgram_envelope(tmp_path: Path):
    sock_path = tmp_path / "ccxd-hooks.sock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    s.bind(str(sock_path))
    try:
        proc = _spawn_emitter("SessionStart",
                              {"session_id": "abc", "cwd": "/x"}, tmp_path)
        assert proc.returncode == 0, proc.stderr
        s.settimeout(1.0)
        data, _ = s.recvfrom(1024)
        msg = json.loads(data.decode())
        assert msg == {
            "event": "SessionStart",
            "payload": {"session_id": "abc", "cwd": "/x"},
        }
    finally:
        s.close()


def test_emitter_silent_drops_when_socket_missing(tmp_path: Path):
    """No daemon? Emitter must exit 0 silently — Claude Code can't be blocked."""
    proc = _spawn_emitter("SessionStart", {"session_id": "abc"}, tmp_path)
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == ""


def test_emitter_silent_drops_on_garbage_stdin(tmp_path: Path):
    sock_path = tmp_path / "ccxd-hooks.sock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    s.bind(str(sock_path))
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "ccx.ccxd.hook_emitter", "Notification"],
            input="not json {",
            text=True,
            env={"XDG_RUNTIME_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
            capture_output=True, timeout=2,
        )
        assert proc.returncode == 0
        assert proc.stdout == ""
    finally:
        s.close()


def test_emitter_requires_event_argv(tmp_path: Path):
    proc = subprocess.run(
        [sys.executable, "-m", "ccx.ccxd.hook_emitter"],
        input='{"x": 1}', text=True,
        env={"XDG_RUNTIME_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True, timeout=2,
    )
    assert proc.returncode != 0
```

- [x] **Step 2: Run — expect ImportError / FileNotFoundError on hook_emitter**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/ccxd/test_hook_emitter.py -v
```

Expected: 4 failures (module doesn't exist).

- [x] **Step 3: Implement hook_emitter.py**

Create `control-plane/ccx/ccxd/hook_emitter.py`:

```python
"""DGRAM bridge: claude-code hook -> ccxd-hooks.sock.

Invoked by Claude Code as `python -m ccx.ccxd.hook_emitter <EventName>`.
Reads stdin (Claude's hook payload JSON), wraps it in
`{"event": <EventName>, "payload": <stdin>}`, and sendto's the daemon's
DGRAM socket at $XDG_RUNTIME_DIR/ccxd-hooks.sock with a 50ms send
timeout. Any failure (no socket, no daemon, garbage JSON, OS error)
exits 0 silently — Claude Code must never be blocked or surfaced an
error from a hook the user didn't configure themselves.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

_TIMEOUT = 0.05  # 50 ms — spec'd hot-path budget


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python -m ccx.ccxd.hook_emitter <EventName>", file=sys.stderr)
        return 2
    event = sys.argv[1]

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0  # silent drop — claude-code must never surface this

    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    sock_path = runtime_dir / "ccxd-hooks.sock"

    msg = json.dumps({"event": event, "payload": payload}).encode()
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.settimeout(_TIMEOUT)
        s.sendto(msg, str(sock_path))
        s.close()
    except OSError:
        return 0  # silent drop
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [x] **Step 4: Run — expect PASS**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/ccxd/test_hook_emitter.py -v
```

Expected: 4 passed.

- [x] **Step 5: Commit**

Use `/commit`. Message: `feat(ccxd): hook emitter — claude-code stdin -> DGRAM bridge`

---

### Task 2: `ccxctl ccxd install-hooks` / `uninstall-hooks`

**Files:**
- Create: `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/ccxd_cli.py`
- Modify: `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/cli.py`
- Create: `/home/david/Work/sesio/sesio__ccx/control-plane/tests/ccxd/test_ccxd_cli.py`

- [x] **Step 1: Audit which hook event names ccxd actually consumes**

```bash
grep -E '"(SessionStart|PreToolUse|PostToolUse|Notification|Stop|UserPromptSubmit|SubagentStop)"' \
  /home/david/Work/sesio/sesio__ccx/control-plane/ccx/ccxd/hooks.py | head
```

Expected: all 7 event names are referenced in `handle_hook`'s `if/elif` cascade. They become the source-of-truth list for `install-hooks`.

- [x] **Step 2: Write failing test**

Create `control-plane/tests/ccxd/test_ccxd_cli.py`:

```python
"""Tests for ccx.ccxd_cli — install-hooks / uninstall-hooks / status etc."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner


_HOOK_EVENTS = (
    "SessionStart", "PreToolUse", "PostToolUse",
    "Notification", "Stop", "UserPromptSubmit", "SubagentStop",
)


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    p = tmp_path / ".claude" / "settings.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"env": {}, "hooks": {}}, indent=2) + "\n")
    return p


def _run(*args, env=None) -> tuple:
    from ccx.ccxd_cli import app
    res = CliRunner().invoke(app, list(args), env=env or {})
    return res.exit_code, res.stdout, res.stderr if hasattr(res, "stderr") else ""


def test_install_hooks_writes_seven_event_entries(settings: Path):
    code, out, _ = _run("install-hooks")
    assert code == 0, out
    data = json.loads(settings.read_text())
    hooks = data["hooks"]
    for ev in _HOOK_EVENTS:
        assert ev in hooks, f"missing hook event: {ev}"
        # Each event has a list with at least one matcher containing a command
        # that runs the emitter.
        assert any("ccx.ccxd.hook_emitter" in (h.get("command") or "")
                   for matchers in hooks[ev]
                   for h in matchers.get("hooks", [])), f"no emitter wired for {ev}"


def test_install_hooks_is_idempotent(settings: Path):
    _run("install-hooks")
    _run("install-hooks")
    data = json.loads(settings.read_text())
    # Each event should have exactly one ccxd entry, not two
    for ev in _HOOK_EVENTS:
        ccxd_entries = [
            h for matchers in data["hooks"][ev]
            for h in matchers.get("hooks", [])
            if "ccx.ccxd.hook_emitter" in (h.get("command") or "")
        ]
        assert len(ccxd_entries) == 1, f"{ev} has {len(ccxd_entries)} ccxd entries"


def test_install_hooks_preserves_existing_non_ccxd_hooks(settings: Path):
    data = json.loads(settings.read_text())
    data["hooks"] = {"SessionStart": [
        {"hooks": [{"type": "command", "command": "/some/other/hook.sh"}]}
    ]}
    settings.write_text(json.dumps(data) + "\n")
    _run("install-hooks")
    data = json.loads(settings.read_text())
    cmds = [h.get("command")
            for m in data["hooks"]["SessionStart"]
            for h in m.get("hooks", [])]
    assert "/some/other/hook.sh" in cmds


def test_uninstall_hooks_removes_ccxd_entries(settings: Path):
    _run("install-hooks")
    _run("uninstall-hooks")
    data = json.loads(settings.read_text())
    for ev, matchers in data.get("hooks", {}).items():
        for m in matchers:
            for h in m.get("hooks", []):
                assert "ccx.ccxd.hook_emitter" not in (h.get("command") or "")


def test_install_hooks_refuses_inside_live_session(settings: Path):
    code, _, _ = _run("install-hooks", env={"CLAUDECODE": "1"})
    assert code == 2  # mirror claude-bedrock guard
```

- [x] **Step 3: Run — expect ImportError on ccxd_cli**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/ccxd/test_ccxd_cli.py -v
```

Expected: collection error.

- [x] **Step 4: Implement ccxd_cli.py — `install-hooks` + `uninstall-hooks`**

Create `control-plane/ccx/ccxd_cli.py`:

```python
"""ccxctl ccxd ... — install/manage the ccxd daemon and its claude-code hooks."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer

app = typer.Typer(help="Manage the ccxd Claude Code session daemon.")

_HOOK_EVENTS = (
    "SessionStart", "PreToolUse", "PostToolUse",
    "Notification", "Stop", "UserPromptSubmit", "SubagentStop",
)
_EMITTER_CMD = f"{sys.executable} -m ccx.ccxd.hook_emitter"
_MARKER = "ccx.ccxd.hook_emitter"  # substring identifying our entries


def _settings_path() -> Path:
    return Path(os.environ.get("HOME", "~")).expanduser() / ".claude" / "settings.json"


def _refuse_in_live_session() -> None:
    if os.environ.get("CLAUDECODE") == "1" and "--force" not in sys.argv:
        typer.echo(
            "refusing: CLAUDECODE=1 detected — modifying settings.json from inside\n"
            "a live claude session can hot-reload broken state into the running\n"
            "process. /exit first and re-run from a normal shell. Pass --force to override.",
            err=True,
        )
        raise typer.Exit(code=2)


def _load() -> dict:
    return json.loads(_settings_path().read_text())


def _save(data: dict) -> None:
    _settings_path().write_text(json.dumps(data, indent=2) + "\n")


def _entry_for(event: str) -> dict:
    return {
        "hooks": [{"type": "command",
                   "command": f"{_EMITTER_CMD} {event}",
                   "timeout": 2}],
    }


@app.command("install-hooks")
def install_hooks() -> None:
    """Wire ccxd into ~/.claude/settings.json for the 7 supported events."""
    _refuse_in_live_session()
    data = _load()
    hooks = data.setdefault("hooks", {})
    for event in _HOOK_EVENTS:
        existing = hooks.setdefault(event, [])
        # Drop any prior ccxd entries to keep idempotency
        cleaned = []
        for matcher in existing:
            kept_hooks = [
                h for h in matcher.get("hooks", [])
                if _MARKER not in (h.get("command") or "")
            ]
            if kept_hooks:
                m = dict(matcher)
                m["hooks"] = kept_hooks
                cleaned.append(m)
        cleaned.append(_entry_for(event))
        hooks[event] = cleaned
    _save(data)
    typer.echo(f"installed ccxd hooks for: {', '.join(_HOOK_EVENTS)}")


@app.command("uninstall-hooks")
def uninstall_hooks() -> None:
    """Remove ccxd entries from ~/.claude/settings.json hooks."""
    _refuse_in_live_session()
    data = _load()
    hooks = data.get("hooks", {})
    for event, matchers in list(hooks.items()):
        cleaned = []
        for matcher in matchers:
            kept = [h for h in matcher.get("hooks", [])
                    if _MARKER not in (h.get("command") or "")]
            if kept:
                m = dict(matcher)
                m["hooks"] = kept
                cleaned.append(m)
        if cleaned:
            hooks[event] = cleaned
        else:
            hooks.pop(event)
    data["hooks"] = hooks
    _save(data)
    typer.echo("uninstalled ccxd hooks")
```

- [x] **Step 5: Mount as `ccxctl ccxd ...` in cli.py**

In `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/cli.py`, add to the imports near the top of the file:

```python
from ccx.ccxd_cli import app as ccxd_app
```

And below the `app = typer.Typer(...)` declaration, mount it:

```python
app.add_typer(ccxd_app, name="ccxd")
```

- [x] **Step 6: Run tests — expect PASS**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/ccxd/test_ccxd_cli.py -v
```

Expected: 5 passed.

- [x] **Step 7: Commit**

Use `/commit`. Message: `feat(ccxd): ccxctl ccxd install-hooks / uninstall-hooks`

---

### Task 3: Systemd unit + `install-service` / `status` / `logs` / `restart` / `stop`

**Files:**
- Create: `/home/david/Work/sesio/sesio__ccx/control-plane/etc/ccxd.service`
- Modify: `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/ccxd_cli.py`
- Modify: `/home/david/Work/sesio/sesio__ccx/control-plane/tests/ccxd/test_ccxd_cli.py`

- [x] **Step 1: Create the systemd unit template**

Create `control-plane/etc/ccxd.service`:

```ini
[Unit]
Description=ccxd — Claude Code session daemon
After=default.target
ConditionPathExists=%h/.claude/projects

[Service]
Type=notify
ExecStart=__PYTHON__ -m ccx.ccxd
Restart=on-failure
RestartSec=2
StandardOutput=journal
StandardError=journal
# State lives under $XDG_DATA_HOME/ccxd/state.db; the daemon creates the dir.
# Sockets live under $XDG_RUNTIME_DIR — systemd sets this on user units.

[Install]
WantedBy=default.target
```

The `__PYTHON__` placeholder is substituted by `install-service` at install time so the path matches whichever interpreter ran the install.

- [x] **Step 2: Add tests for install-service / status / logs / restart / stop**

Append to `control-plane/tests/ccxd/test_ccxd_cli.py`:

```python
@pytest.fixture
def systemd_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    return tmp_path


def test_install_service_writes_unit(systemd_home):
    with patch("ccx.ccxd_cli._systemctl") as sysctl:
        sysctl.return_value = 0
        code, out, _ = _run("install-service")
    assert code == 0, out
    unit = systemd_home / ".config" / "systemd" / "user" / "ccxd.service"
    assert unit.exists()
    body = unit.read_text()
    assert "__PYTHON__" not in body
    assert "ExecStart=" in body
    assert "ccx.ccxd" in body
    # daemon-reload + enable --now were called
    called = [c.args[0] for c in sysctl.call_args_list]
    assert ["daemon-reload"] in called
    assert any(c[:2] == ["enable", "--now"] for c in called)


def test_status_shells_to_systemctl(systemd_home):
    with patch("ccx.ccxd_cli._systemctl") as sysctl:
        sysctl.return_value = 0
        code, _, _ = _run("status")
    assert code == 0
    sysctl.assert_called_once_with(["status", "ccxd"])


def test_logs_passes_extra_args(systemd_home):
    with patch("ccx.ccxd_cli._journalctl") as jctl:
        jctl.return_value = 0
        _run("logs", "--", "-f")
    jctl.assert_called_once()
    args = jctl.call_args.args[0]
    assert "--user-unit" in args and "ccxd" in args
```

- [x] **Step 3: Implement the systemd subcommands in ccxd_cli.py**

Append to `ccxd_cli.py`:

```python
import shlex
import subprocess

_UNIT_NAME = "ccxd"


def _unit_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME",
                                     os.path.expanduser("~/.config")))
    return config_home / "systemd" / "user" / f"{_UNIT_NAME}.service"


def _unit_template() -> str:
    return (Path(__file__).parents[1] / "etc" / "ccxd.service").read_text()


def _systemctl(argv: list[str]) -> int:
    return subprocess.call(["systemctl", "--user", *argv])


def _journalctl(argv: list[str]) -> int:
    return subprocess.call(["journalctl", *argv])


@app.command("install-service")
def install_service() -> None:
    """Materialize ccxd.service in ~/.config/systemd/user/, then enable --now."""
    body = _unit_template().replace("__PYTHON__", shlex.quote(sys.executable))
    target = _unit_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    rc = _systemctl(["daemon-reload"])
    if rc != 0:
        raise typer.Exit(rc)
    rc = _systemctl(["enable", "--now", _UNIT_NAME])
    if rc != 0:
        raise typer.Exit(rc)
    typer.echo(f"installed and started {_UNIT_NAME}.service")


@app.command("status")
def status() -> None:
    raise typer.Exit(_systemctl(["status", _UNIT_NAME]))


@app.command("restart")
def restart() -> None:
    raise typer.Exit(_systemctl(["restart", _UNIT_NAME]))


@app.command("stop")
def stop() -> None:
    raise typer.Exit(_systemctl(["stop", _UNIT_NAME]))


@app.command("logs")
def logs(extra: list[str] = typer.Argument(None)) -> None:
    """Tail ccxd journal. Pass `-- -f` for follow mode."""
    raise typer.Exit(_journalctl(["--user-unit", _UNIT_NAME, *(extra or [])]))
```

(The `_unit_template()` path assumes the package is installed in editable / source mode — `Path(__file__).parents[1]` is the `control-plane/` dir. If that's wrong on the user's machine, `install-service` will FileNotFoundError, which is the correct surface.)

- [x] **Step 4: Run — expect PASS**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/ccxd/test_ccxd_cli.py -v
```

Expected: 8 passed (5 from Task 2 + 3 here).

- [x] **Step 5: Commit**

Use `/commit`. Message: `feat(ccxd): systemd user unit + install-service / status / logs / restart / stop`

---

### Task 4: `ccxctl ccxd query`

**Why last:** Closes the loop — gives the user a CLI to verify hooks are wired and the daemon is collecting state, without writing a Python REPL.

**Files:**
- Modify: `/home/david/Work/sesio/sesio__ccx/control-plane/ccx/ccxd_cli.py`
- Modify: `/home/david/Work/sesio/sesio__ccx/control-plane/tests/ccxd/test_ccxd_cli.py`

- [x] **Step 1: Add the test**

Append to `control-plane/tests/ccxd/test_ccxd_cli.py`:

```python
def test_query_prints_session_table(tmp_path, monkeypatch):
    """query connects to ccxd.sock, sends RPC, prints the sessions table."""
    import socket
    import threading

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    sock_path = tmp_path / "ccxd.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)

    def serve() -> None:
        conn, _ = server.accept()
        # Read one NDJSON line then respond
        buf = b""
        while not buf.endswith(b"\n"):
            buf += conn.recv(4096)
        conn.sendall(json.dumps({
            "id": 1,
            "result": {
                "protocol_version": 1,
                "sessions": [{"session_id": "ses-1", "cwd": "/tmp/x",
                              "model": "claude-opus-4-7",
                              "tokens_in": 100, "tokens_out": 50,
                              "summary": "doing things"}],
            },
        }).encode() + b"\n")
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    code, out, _ = _run("query")
    server.close()
    assert code == 0
    assert "ses-1" in out
    assert "claude-opus-4-7" in out
    assert "100" in out and "50" in out


def test_query_handles_daemon_down(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    code, out, _ = _run("query")
    assert code == 1
    assert "daemon" in out.lower()
```

- [x] **Step 2: Run — expect failure (no `query` command)**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/ccxd/test_ccxd_cli.py::test_query_prints_session_table -v
```

Expected: fail with `No such command 'query'`.

- [x] **Step 3: Implement `query`**

Append to `ccxd_cli.py`:

```python
import socket as _socket

from rich.console import Console
from rich.table import Table


@app.command("query")
def query() -> None:
    """Connect to ccxd.sock, request the session list, print as a table."""
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    sock_path = runtime / "ccxd.sock"
    if not sock_path.exists():
        typer.echo(f"daemon socket not found at {sock_path}; is ccxd running?",
                   err=True)
        raise typer.Exit(1)

    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    try:
        s.connect(str(sock_path))
    except OSError as e:
        typer.echo(f"daemon connect failed: {e}", err=True)
        raise typer.Exit(1)

    s.sendall(json.dumps({"id": 1, "method": "query", "params": {}}).encode() + b"\n")
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    s.close()

    resp = json.loads(buf.decode().strip())
    if "error" in resp:
        typer.echo(f"daemon error: {resp['error']}", err=True)
        raise typer.Exit(1)

    sessions = resp["result"]["sessions"]
    if not sessions:
        typer.echo("no active sessions")
        return

    table = Table(title=f"ccxd · protocol v{resp['result']['protocol_version']}")
    for col in ("session_id", "cwd", "model", "in", "out", "summary"):
        table.add_column(col)
    for s in sessions:
        table.add_row(
            (s.get("session_id") or "")[:12],
            s.get("cwd") or "",
            (s.get("model") or "")[:20],
            str(s.get("tokens_in") or 0),
            str(s.get("tokens_out") or 0),
            (s.get("summary") or "")[:40],
        )
    Console().print(table)
```

- [x] **Step 4: Run — expect PASS**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/ccxd/test_ccxd_cli.py -v
```

Expected: 10 passed.

- [x] **Step 5: End-to-end smoke**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && \
  rm -rf /tmp/ccxd-deploy && mkdir -p /tmp/ccxd-deploy && \
  XDG_RUNTIME_DIR=/tmp/ccxd-deploy CCXD_SKIP_DISCOVERY=1 \
  /usr/bin/uv run python -m ccx.ccxd --memory-store --log-level info > /tmp/ccxd-deploy/d.log 2>&1 &
sleep 1
# emit a hook the same way claude-code would
echo '{"session_id": "smoke-1", "cwd": "/tmp/smoke", "hook_event_name": "SessionStart"}' \
  | XDG_RUNTIME_DIR=/tmp/ccxd-deploy /usr/bin/uv run python -m ccx.ccxd.hook_emitter SessionStart
sleep 0.2
# query
XDG_RUNTIME_DIR=/tmp/ccxd-deploy /usr/bin/uv run ccxctl ccxd query
# stop
pkill -f "ccx.ccxd --memory-store"
```

Expected: `query` prints a table containing `smoke-1` / `/tmp/smoke`.

- [x] **Step 6: Commit**

Use `/commit`. Message: `feat(ccxd): ccxctl ccxd query — RPC client for the session table`

---

## Self-Review Checklist

- [x] **Hook emitter is silent on every failure path** — no socket, no daemon, garbage stdin, OS error → exit 0, no stdout, no stderr (except missing argv → exit 2).
- [x] **install-hooks is idempotent** — running it twice doesn't duplicate entries.
- [x] **install-hooks preserves user's existing non-ccxd hook entries** — only matchers/hooks containing the `_MARKER` substring are touched.
- [x] **uninstall-hooks is the inverse** — after install + uninstall, settings.json hooks block matches the original.
- [x] **CLAUDECODE=1 guard** on install/uninstall mirrors `claude-bedrock` / `claude-sub`.
- [x] **systemd unit Type=notify** lines up with the daemon's `sd_notify(READY=1)` / `sd_notify(STOPPING=1)` calls.
- [x] **query handles daemon-down gracefully** — exit 1 with a clear message, not a stacktrace.
- [x] **No new deps** — stdlib + already-present typer + already-present rich.
- [x] **Plan 4 scope NOT included** — no ansible role for the EC2 box, no monitor_tui migration to read from ccxd, no widget refactor.
