# Agent Monitor Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run `hoangsonww/Claude-Code-Agent-Monitor` v1.1.0 as a systemd service on the ccx EC2 host, wire it to Claude Code hooks, and expose it via SSH tunnel through new `ccxctl monitor {status,tunnel,logs}` subcommands.

**Architecture:** New Ansible role `agent_monitor` clones the upstream repo to `/opt/agent-monitor`, runs `npm run setup` + `npm run build` under the ccx user, drops a `agent-monitor.service` systemd unit (Type=simple, asdf-shimmed Node, `127.0.0.1:4820`-style local-only access via SG), and bootstraps Claude hooks via `npm run install-hooks`. A new `ccx/monitor.py` Typer app drives lifecycle from the laptop using the existing SSH config in `cli.CFG`. Spec: `docs/superpowers/specs/2026-04-28-agent-monitor-design.md`.

**Tech Stack:** Ansible (existing role conventions), Python 3.11 + Typer + Rich (existing), pytest (existing), systemd, asdf-managed Node ≥22.

---

## File Structure

**Created:**
- `ansible/roles/agent_monitor/defaults/main.yml`
- `ansible/roles/agent_monitor/handlers/main.yml`
- `ansible/roles/agent_monitor/tasks/main.yml`
- `ansible/roles/agent_monitor/templates/agent-monitor.service.j2`
- `control-plane/ccx/ui.py` — extracted UI helpers (`console`, `step`, `sub`, `ok`, `die`)
- `control-plane/ccx/monitor.py` — `ccxctl monitor` Typer app
- `control-plane/tests/test_monitor.py`
- `docs/agent-monitor.md` — version-pinning policy

**Modified:**
- `control-plane/ccx/cli.py` — extract helpers to `ui.py` + register monitor app
- `ansible/site.yml` — add `agent_monitor` after `claude_plugins`
- `ansible/roles/verify/tasks/main.yml` — three new checks + provision-ok marker
- `README.md` — Agent Monitor section + smoke checklist
- `control-plane/README.md` — extend subcommand table

**Deleted:**
- (none)

---

## Task 0: Pre-flight + housekeeping

**Files:**
- Modify: `.gitignore` or commit/remove `.codex` (currently empty, untracked)
- Move: `docs/agent-monitor-integration-prompt.md` → discard (superseded by this plan)

- [ ] **Step 1: Decide what to do with the untracked `.codex` empty file**

```bash
ls -la /home/david/Work/sesio/sesio__ccx/.codex
```

If empty and uncommitted: delete it (`rm /home/david/Work/sesio/sesio__ccx/.codex`). It's an artifact of an earlier codex run; not load-bearing.

- [ ] **Step 2: Discard the integration prompt now that the plan exists**

```bash
rm /home/david/Work/sesio/sesio__ccx/docs/agent-monitor-integration-prompt.md
```

- [ ] **Step 3: Verify clean working tree before starting**

```bash
cd /home/david/Work/sesio/sesio__ccx && git status --short
```

Expected: nothing in `git status` other than the new plan file at `docs/superpowers/plans/2026-04-29-agent-monitor.md`.

- [ ] **Step 4: Commit the plan + housekeeping**

```bash
cd /home/david/Work/sesio/sesio__ccx
git add docs/superpowers/plans/2026-04-29-agent-monitor.md
git commit -m "docs(agent-monitor): add implementation plan"
```

---

## Task 1: Extract UI helpers from cli.py to ui.py

The design calls for `monitor.py` to import styled helpers from a public module rather than crossing the `_`-prefixed private boundary. Move the helpers, keep `cli.py` re-exporting them so existing call sites (and `motd.py`, `sessions.py` if any) keep working.

**Files:**
- Create: `control-plane/ccx/ui.py`
- Modify: `control-plane/ccx/cli.py:23-23, 96-110, 221-225` (move `console`, `_step`, `_sub`, `_ok`, `die`)

- [ ] **Step 1: Search for callers of the underscore helpers across the package**

```bash
grep -rn "from ccx.cli import\|cli\._step\|cli\._sub\|cli\._ok\|cli\.die\|cli\.console" /home/david/Work/sesio/sesio__ccx/control-plane/ccx /home/david/Work/sesio/sesio__ccx/control-plane/tests
```

Inside `cli.py` the helpers are referenced by their bare `_step` / `_sub` / `_ok` / `die` names. `sessions.py:350` imports `pick_menu` from `cli`. Make sure no test reaches into `_step`/`_sub`/`_ok` directly — if any do, rename them at the call site too.

- [ ] **Step 2: Create `ui.py` with the public-named helpers**

```python
"""ccx.ui — styled output + fatal-exit helpers.

Lifted from cli.py so other modules (monitor.py, future ones) can import
from a public surface instead of crossing `_`-prefixed names.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

import typer
from rich.console import Console

console = Console()


def step(msg: str) -> None:
    """Top-level step — `▶ msg`."""
    console.print(f"[blue]▶[/] {msg}")


def sub(msg: str) -> None:
    """Indented detail line — `  · msg`."""
    console.print(f"  [dim]·[/] {msg}")


def ok(msg: str) -> None:
    """Success line — `✓ msg`."""
    console.print(f"[green]✓[/] {msg}")


def die(msg: str) -> "typer.Exit":
    """Log + desktop-notify + exit 1.

    Notification is best-effort: skipped if `notify-send` is missing.
    """
    print(f"error: {msg}", file=sys.stderr, flush=True)
    if shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", "-u", "critical", "ccx error", msg],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    raise typer.Exit(code=1)
```

Note: `die()` here uses a self-contained notify-send call rather than the rich `notify()` helper from `cli.py`, because pulling `notify()` (and its `ProgressNotifier` machinery) out of `cli.py` is bigger surgery than this plan needs. The downside is the icon path is dropped from `die()`'s notification — acceptable.

- [ ] **Step 3: Update `cli.py` to import + re-export from `ui.py`**

Replace:
```python
console = Console()
```
near `cli.py:23` with:
```python
from ccx.ui import console, step, sub, ok, die  # re-exports for back-compat
```

Delete the `_step`, `_sub`, `_ok`, `die` definitions at `cli.py:96-110` and `cli.py:221-225`.

Then rename the in-file callers from `_step(` → `step(`, `_sub(` → `sub(`, `_ok(` → `ok(`, leaving `die(` (already public) untouched.

- [ ] **Step 4: Run the existing test suite to confirm nothing broke**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest -x
```

Expected: all existing tests still pass. (`test_cli.py`, `test_motd.py`, `test_sessions.py`.)

- [ ] **Step 5: Commit**

```bash
cd /home/david/Work/sesio/sesio__ccx
git add control-plane/ccx/ui.py control-plane/ccx/cli.py
git commit -m "refactor(control-plane): extract ui helpers to ccx.ui"
```

---

## Task 2: Write `ccxctl monitor` failing tests (status, happy path)

Mirror `test_sessions.py` style. Tests reference `ccx.monitor` which does not yet exist.

**Files:**
- Create: `control-plane/tests/test_monitor.py`

- [ ] **Step 1: Write the test file**

```python
"""Tests for ccxctl monitor — mirrors test_sessions.py style."""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner


def _mock_run(stdout: str = "", returncode: int = 0, stderr: str = ""):
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.stdout = stdout
    m.returncode = returncode
    m.stderr = stderr
    return m


def test_monitor_help_lists_subcommands():
    """Top-level monitor --help should mention the three subcommands."""
    from ccx.cli import app
    result = CliRunner().invoke(app, ["monitor", "--help"])
    assert result.exit_code == 0
    out = result.stdout
    assert "status" in out
    assert "tunnel" in out
    assert "logs" in out


def test_status_active_and_healthy():
    """systemctl=active + /api/health=ok → exit 0 + lines on stdout."""
    from ccx.monitor import app
    health = json.dumps({"status": "ok", "timestamp": "2026-04-29T00:00:00Z"})
    combined = f"active\n@@@\n{health}"
    with patch("ccx.monitor.subprocess.run", return_value=_mock_run(combined, 0)):
        result = CliRunner().invoke(app, ["status"])
    assert result.exit_code == 0
    assert "active" in result.stdout
    assert "ok" in result.stdout
```

- [ ] **Step 2: Run the test to confirm it fails the right way**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/test_monitor.py -v
```

Expected: ImportError or ModuleNotFoundError for `ccx.monitor`. (Both `test_monitor_help_lists_subcommands` and `test_status_active_and_healthy` should error out at import time, which pytest reports as errors — that's fine.)

---

## Task 3: Implement minimal `ccx/monitor.py` to make Task 2 pass

**Files:**
- Create: `control-plane/ccx/monitor.py`
- Modify: `control-plane/ccx/cli.py:341-342` (register the monitor app after the sessions app)

- [ ] **Step 1: Write the minimum monitor module**

```python
"""ccxctl monitor — manage the agent-monitor systemd service via SSH."""
from __future__ import annotations

import json
import os
import subprocess

import typer

from ccx import cli  # lazy CFG access — see CFG access pattern in design
from ccx.ui import die, ok, step, sub

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Manage the Claude Code agent monitor service.",
)

UNIT = "agent-monitor"
PORT = 4820
SENTINEL = "@@@"


def _ssh_base() -> list[str]:
    """SSH argv prefix matching cli.ssh()'s flags."""
    return [
        "ssh",
        "-i", str(cli.CFG.ssh_key),
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{cli.CFG.ssh_user}@{cli.CFG.hostname}",
    ]


@app.command("status")
def cmd_status() -> None:
    """systemctl is-active + curl /api/health, single SSH round-trip."""
    remote = (
        f"systemctl is-active {UNIT}; "
        f"printf '{SENTINEL}\\n'; "
        f"curl -fsS http://127.0.0.1:{PORT}/api/health"
    )
    r = subprocess.run(
        _ssh_base() + ["bash", "-c", remote],
        capture_output=True, text=True, check=False,
    )
    if r.returncode == 255:
        die(f"ssh failed: {r.stderr.strip() or 'no stderr'}")

    if SENTINEL not in r.stdout:
        die(f"unexpected ssh output (no sentinel): {r.stdout!r}")
    systemd_part, _, health_part = r.stdout.partition(f"\n{SENTINEL}\n")
    systemd_state = systemd_part.strip()

    step(f"systemd: [bold]{systemd_state}[/]")
    if systemd_state != "active":
        die(f"unit {UNIT} is {systemd_state!r}")

    health_raw = health_part.strip()
    if not health_raw:
        die(f"/api/health unreachable on the host")
    try:
        health = json.loads(health_raw)
    except json.JSONDecodeError as exc:
        die(f"could not parse /api/health JSON: {exc}")
    sub(f"health: {health!r}")
    if health.get("status") != "ok":
        die(f"health status {health.get('status')!r} (expected 'ok')")

    ok(f"{UNIT} active and healthy on port {PORT}")
```

- [ ] **Step 2: Register `monitor` in `cli.py`**

In `cli.py`, immediately after the line:
```python
app.add_typer(_sessions_app, name="session", help="Manage claude sessions (tmux).")
```

add:
```python
from ccx.monitor import app as _monitor_app
app.add_typer(_monitor_app, name="monitor", help="Manage the Claude Code agent monitor service.")
```

- [ ] **Step 3: Run the tests to confirm both pass**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/test_monitor.py -v
```

Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
cd /home/david/Work/sesio/sesio__ccx
git add control-plane/ccx/monitor.py control-plane/ccx/cli.py control-plane/tests/test_monitor.py
git commit -m "feat(control-plane): add ccxctl monitor status"
```

---

## Task 4: Add the `status` failure-path tests + verify they pass

The status command already implements the failure paths defensively. We add the tests now to lock the contract in.

**Files:**
- Modify: `control-plane/tests/test_monitor.py`

- [ ] **Step 1: Append the failure-path tests**

```python
def test_status_systemd_inactive_exits_nonzero():
    """systemctl=inactive → exit != 0, error mentions unit name."""
    from ccx.monitor import app
    health = json.dumps({"status": "ok"})
    combined = f"inactive\n@@@\n{health}"
    with patch("ccx.monitor.subprocess.run", return_value=_mock_run(combined, 0)):
        result = CliRunner().invoke(app, ["status"])
    assert result.exit_code != 0
    # die() emits to stderr, but typer's CliRunner mixes both into output by default
    assert "agent-monitor" in (result.stdout + result.stderr)


def test_status_health_endpoint_unreachable():
    """systemctl=active but no JSON after sentinel (curl rc!=0) → exit != 0."""
    from ccx.monitor import app
    combined = "active\n@@@\n"  # empty health part
    with patch("ccx.monitor.subprocess.run", return_value=_mock_run(combined, 0)):
        result = CliRunner().invoke(app, ["status"])
    assert result.exit_code != 0
    assert "/api/health" in (result.stdout + result.stderr)


def test_status_invalid_health_json():
    """Health payload is non-JSON garbage → parse-failure exit."""
    from ccx.monitor import app
    combined = "active\n@@@\nnot-json-at-all"
    with patch("ccx.monitor.subprocess.run", return_value=_mock_run(combined, 0)):
        result = CliRunner().invoke(app, ["status"])
    assert result.exit_code != 0
    assert "parse" in (result.stdout + result.stderr).lower() or \
           "json" in (result.stdout + result.stderr).lower()


def test_status_health_status_not_ok():
    """Health returns status='degraded' → exit != 0, surfaces the actual value."""
    from ccx.monitor import app
    health = json.dumps({"status": "degraded"})
    combined = f"active\n@@@\n{health}"
    with patch("ccx.monitor.subprocess.run", return_value=_mock_run(combined, 0)):
        result = CliRunner().invoke(app, ["status"])
    assert result.exit_code != 0
    assert "degraded" in (result.stdout + result.stderr)


def test_status_ssh_failure_rc_255():
    """ssh itself fails (rc=255) → 'ssh failed:' message."""
    from ccx.monitor import app
    with patch(
        "ccx.monitor.subprocess.run",
        return_value=_mock_run("", returncode=255, stderr="Connection refused"),
    ):
        result = CliRunner().invoke(app, ["status"])
    assert result.exit_code != 0
    assert "ssh failed" in (result.stdout + result.stderr)
    assert "Connection refused" in (result.stdout + result.stderr)
```

- [ ] **Step 2: Run the new tests**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/test_monitor.py -v
```

Expected: 7 passed (the 2 from before + 5 new).

- [ ] **Step 3: Commit**

```bash
cd /home/david/Work/sesio/sesio__ccx
git add control-plane/tests/test_monitor.py
git commit -m "test(control-plane): cover monitor status failure paths"
```

---

## Task 5: Implement `tunnel` command (foreground + --print)

**Files:**
- Modify: `control-plane/ccx/monitor.py`
- Modify: `control-plane/tests/test_monitor.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_monitor.py`:

```python
def test_tunnel_default_execs_ssh_with_L_flag(monkeypatch):
    """Default tunnel exec'd ssh argv contains -L 4820:127.0.0.1:4820 -N + the host."""
    from ccx.monitor import app
    captured: list[list[str]] = []
    monkeypatch.setattr("ccx.monitor.os.execvp", lambda _, argv: captured.append(argv))
    result = CliRunner().invoke(app, ["tunnel"])
    assert result.exit_code == 0  # execvp is patched out → returns
    assert captured, "execvp not called"
    argv = captured[0]
    assert argv[0] == "ssh"
    assert "-L" in argv
    assert "4820:127.0.0.1:4820" in argv
    assert "-N" in argv
    # default CFG → david@ccx.dsylla.sesio.io
    assert any("@" in a and a.endswith("ccx.dsylla.sesio.io") for a in argv)


def test_tunnel_print_outputs_command_no_exec(monkeypatch):
    """--print emits the command and does NOT exec ssh."""
    from ccx.monitor import app
    called: list = []
    monkeypatch.setattr(
        "ccx.monitor.os.execvp",
        lambda _, argv: called.append(argv),
    )
    result = CliRunner().invoke(app, ["tunnel", "--print"])
    assert result.exit_code == 0
    assert "ssh" in result.stdout
    assert "-L 4820:127.0.0.1:4820" in result.stdout
    assert called == [], "execvp should NOT be called with --print"
```

- [ ] **Step 2: Run, confirm failure**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/test_monitor.py::test_tunnel_default_execs_ssh_with_L_flag tests/test_monitor.py::test_tunnel_print_outputs_command_no_exec -v
```

Expected: both fail with `No such command 'tunnel'`.

- [ ] **Step 3: Implement `tunnel`**

Add to `ccx/monitor.py`:

```python
@app.command("tunnel")
def cmd_tunnel(
    print_only: bool = typer.Option(
        False, "--print", "-p", help="Print the ssh command instead of running it.",
    ),
) -> None:
    """Open an SSH tunnel forwarding localhost:4820 → ccx 127.0.0.1:4820 (foreground)."""
    forward = f"{PORT}:127.0.0.1:{PORT}"
    argv = [
        "ssh",
        "-i", str(cli.CFG.ssh_key),
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-N", "-L", forward,
        f"{cli.CFG.ssh_user}@{cli.CFG.hostname}",
    ]
    if print_only:
        # Print without -i/-o noise (just the actionable part the user copies).
        print(f"ssh -L {forward} -N {cli.CFG.ssh_user}@{cli.CFG.hostname}")
        return
    os.execvp("ssh", argv)
```

- [ ] **Step 4: Run, confirm pass**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/test_monitor.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/david/Work/sesio/sesio__ccx
git add control-plane/ccx/monitor.py control-plane/tests/test_monitor.py
git commit -m "feat(control-plane): add ccxctl monitor tunnel"
```

---

## Task 6: Implement `logs` command (no-follow + --follow)

**Files:**
- Modify: `control-plane/ccx/monitor.py`
- Modify: `control-plane/tests/test_monitor.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_monitor.py`:

```python
def test_logs_no_follow_omits_t_flag_and_uses_no_pager(monkeypatch):
    """logs (no -f) should NOT request a TTY and must use --no-pager."""
    from ccx.monitor import app
    captured: list[list[str]] = []
    monkeypatch.setattr("ccx.monitor.os.execvp", lambda _, argv: captured.append(argv))
    result = CliRunner().invoke(app, ["logs"])
    assert result.exit_code == 0
    assert captured, "execvp not called"
    argv = captured[0]
    assert "-t" not in argv
    remote = " ".join(a for a in argv if not a.startswith("-") and "@" not in a and a != "ssh")
    assert "journalctl" in remote
    assert "-u agent-monitor" in remote
    assert "--no-pager" in remote


def test_logs_follow_adds_f_and_t_flags(monkeypatch):
    """logs -f should request a TTY and pass -f to journalctl."""
    from ccx.monitor import app
    captured: list[list[str]] = []
    monkeypatch.setattr("ccx.monitor.os.execvp", lambda _, argv: captured.append(argv))
    result = CliRunner().invoke(app, ["logs", "--follow"])
    assert result.exit_code == 0
    argv = captured[0]
    assert "-t" in argv
    remote = " ".join(a for a in argv if not a.startswith("-") and "@" not in a and a != "ssh")
    assert "journalctl" in remote
    assert "-u agent-monitor" in remote
    assert "-f" in remote
```

- [ ] **Step 2: Confirm failure**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/test_monitor.py::test_logs_no_follow_omits_t_flag_and_uses_no_pager tests/test_monitor.py::test_logs_follow_adds_f_and_t_flags -v
```

Expected: both fail (`No such command 'logs'`).

- [ ] **Step 3: Implement `logs`**

Add to `ccx/monitor.py`:

```python
@app.command("logs")
def cmd_logs(
    follow: bool = typer.Option(
        False, "--follow", "-f", help="Stream logs (allocates a TTY).",
    ),
) -> None:
    """Tail the agent-monitor systemd journal over SSH."""
    base = [
        "ssh",
        "-i", str(cli.CFG.ssh_key),
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=accept-new",
    ]
    if follow:
        base.append("-t")
        remote = f"journalctl -u {UNIT} -f"
    else:
        # No `-t`: avoids spurious pseudo-TTY allocation; --no-pager prevents
        # less from being invoked on the remote end.
        remote = f"journalctl -u {UNIT} --no-pager"
    argv = base + [f"{cli.CFG.ssh_user}@{cli.CFG.hostname}", remote]
    os.execvp("ssh", argv)
```

- [ ] **Step 4: Run all monitor tests**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/test_monitor.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/david/Work/sesio/sesio__ccx
git add control-plane/ccx/monitor.py control-plane/tests/test_monitor.py
git commit -m "feat(control-plane): add ccxctl monitor logs"
```

---

## Task 7: Add the `CFG`-override test

This pins down that the SSH user/host come from `cli.CFG` and survives a monkeypatch — the spec is explicit that we use `monkeypatch.setattr("ccx.cli.CFG", ...)` and never `importlib.reload`.

**Files:**
- Modify: `control-plane/tests/test_monitor.py`

- [ ] **Step 1: Write the test**

Append to `test_monitor.py`:

```python
def test_status_uses_configured_host_and_user(monkeypatch):
    """SSH argv must reflect cli.CFG.ssh_user/hostname/ssh_key, not import-time defaults."""
    from ccx.cli import Config
    from ccx.monitor import app
    fake_cfg = Config()
    fake_cfg.hostname = "alt.example.test"
    fake_cfg.ssh_user = "alice"
    monkeypatch.setattr("ccx.cli.CFG", fake_cfg)

    captured: list[list[str]] = []

    def fake_run(argv, **kwargs):
        captured.append(argv)
        return _mock_run("active\n@@@\n" + json.dumps({"status": "ok"}), 0)

    with patch("ccx.monitor.subprocess.run", side_effect=fake_run):
        result = CliRunner().invoke(app, ["status"])
    assert result.exit_code == 0
    argv = captured[0]
    assert any(a == "alice@alt.example.test" for a in argv)
```

- [ ] **Step 2: Run**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest tests/test_monitor.py -v
```

Expected: 12 passed.

- [ ] **Step 3: Commit**

```bash
cd /home/david/Work/sesio/sesio__ccx
git add control-plane/tests/test_monitor.py
git commit -m "test(control-plane): pin monitor SSH args to cli.CFG"
```

---

## Task 8: Scaffold the `agent_monitor` Ansible role

**Files:**
- Create: `ansible/roles/agent_monitor/defaults/main.yml`
- Create: `ansible/roles/agent_monitor/handlers/main.yml`
- Create: `ansible/roles/agent_monitor/templates/agent-monitor.service.j2`

- [ ] **Step 1: Create the role directory tree**

```bash
mkdir -p /home/david/Work/sesio/sesio__ccx/ansible/roles/agent_monitor/{defaults,handlers,tasks,templates}
```

- [ ] **Step 2: Write `defaults/main.yml`**

```yaml
---
# Pinned upstream version. Bump by editing this and re-running the playbook;
# the git task will update the working tree, npm setup/build will re-run, and
# the systemd handler restarts the service.
agent_monitor_version: v1.1.0
agent_monitor_repo: https://github.com/hoangsonww/Claude-Code-Agent-Monitor.git
agent_monitor_install_dir: /opt/agent-monitor
agent_monitor_port: 4820
```

- [ ] **Step 3: Write `handlers/main.yml`**

```yaml
---
- name: daemon-reload
  ansible.builtin.systemd:
    daemon_reload: true

- name: restart agent-monitor
  ansible.builtin.systemd:
    name: agent-monitor
    state: restarted
  listen:
    - daemon-reload   # pick up unit changes before restart
```

- [ ] **Step 4: Write `templates/agent-monitor.service.j2`**

```ini
# Managed by Ansible — agent_monitor role. Do not edit by hand.
[Unit]
Description=Claude Code Agent Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={{ target_user }}
Group={{ target_user }}
WorkingDirectory={{ agent_monitor_install_dir }}
Environment=PATH={{ target_home }}/.asdf/shims:/usr/local/bin:/usr/bin:/bin
Environment=NODE_ENV=production
Environment=DASHBOARD_PORT={{ agent_monitor_port }}
ExecStart={{ target_home }}/.asdf/shims/npm start
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 5: Commit**

```bash
cd /home/david/Work/sesio/sesio__ccx
git add ansible/roles/agent_monitor
git commit -m "feat(ansible agent_monitor): scaffold role + service unit template"
```

---

## Task 9: Write the `agent_monitor` task list

**Files:**
- Create: `ansible/roles/agent_monitor/tasks/main.yml`

- [ ] **Step 1: Write `tasks/main.yml`**

```yaml
---
# Tasks for agent_monitor role. The install dir is under /opt; only the
# directory-create task runs as root. Everything else uses become_user so
# the working tree (npm, git) stays user-owned, mirroring `claude_plugins`.

- name: Ensure install dir exists (root-owned ancestor, user-owned tree)
  ansible.builtin.file:
    path: "{{ agent_monitor_install_dir }}"
    state: directory
    owner: "{{ target_user }}"
    group: "{{ target_user }}"
    mode: "0755"

- name: Clone agent-monitor (pinned)
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.git:
    repo: "{{ agent_monitor_repo }}"
    dest: "{{ agent_monitor_install_dir }}"
    version: "{{ agent_monitor_version }}"
    update: true
    force: false
  register: _agent_monitor_repo
  notify: restart agent-monitor

- name: Stat node_modules to gate npm setup
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.stat:
    path: "{{ agent_monitor_install_dir }}/node_modules"
  register: _agent_monitor_node_modules

- name: npm run setup (fresh tree or upstream change)
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.shell: |
    source "{{ target_home }}/.asdf/asdf.sh"
    npm run setup
  args:
    chdir: "{{ agent_monitor_install_dir }}"
    executable: /bin/bash
  when: not _agent_monitor_node_modules.stat.exists or _agent_monitor_repo.changed
  changed_when: true
  notify: restart agent-monitor

- name: Stat client/dist to gate npm build
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.stat:
    path: "{{ agent_monitor_install_dir }}/client/dist/index.html"
  register: _agent_monitor_client_dist

- name: npm run build (production React assets)
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.shell: |
    source "{{ target_home }}/.asdf/asdf.sh"
    npm run build
  args:
    chdir: "{{ agent_monitor_install_dir }}"
    executable: /bin/bash
  when: not _agent_monitor_client_dist.stat.exists or _agent_monitor_repo.changed
  changed_when: true
  notify: restart agent-monitor

- name: Render agent-monitor.service unit
  ansible.builtin.template:
    src: agent-monitor.service.j2
    dest: /etc/systemd/system/agent-monitor.service
    owner: root
    group: root
    mode: "0644"
  notify:
    - daemon-reload
    - restart agent-monitor

- name: Enable + start agent-monitor
  ansible.builtin.systemd:
    name: agent-monitor
    enabled: true
    state: started
    daemon_reload: true

# Bootstrap the Claude Code hook entries in ~/.claude/settings.json so events
# flow on the very first session before the service ever restarts. The server
# itself runs installHooks(silent=true) on every startup, which makes this
# task idempotent in spirit even though we mark it changed_when=false.
- name: Bootstrap Claude Code hooks (idempotent on first run; rewritten by server thereafter)
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.shell: |
    source "{{ target_home }}/.asdf/asdf.sh"
    npm run install-hooks
  args:
    chdir: "{{ agent_monitor_install_dir }}"
    executable: /bin/bash
  changed_when: false
```

- [ ] **Step 2: Lint the role**

```bash
cd /home/david/Work/sesio/sesio__ccx && /usr/bin/uv run --with ansible-lint -- ansible-lint ansible/roles/agent_monitor
```

Expected: lint passes (production profile clean). If it complains about `changed_when: true` on the shell tasks, that's the intentional behaviour (we have explicit gating via `when:`); silence individual rules with `# noqa` only if production profile fails.

- [ ] **Step 3: Commit**

```bash
cd /home/david/Work/sesio/sesio__ccx
git add ansible/roles/agent_monitor/tasks
git commit -m "feat(ansible agent_monitor): add task list (clone, npm setup/build, unit, hooks)"
```

---

## Task 10: Wire the role into `site.yml`

**Files:**
- Modify: `ansible/site.yml:14-22`

- [ ] **Step 1: Insert `agent_monitor` after `claude_plugins`**

Edit `ansible/site.yml` to add a new role line. The current ordering is:
```yaml
    - claude_code
    - codex_code
    - codex_config
    - codex_mcp
    - claude_plugins
    - rtk
    - motd
    - verify
```

After:
```yaml
    - claude_code
    - codex_code
    - codex_config
    - codex_mcp
    - claude_plugins
    - agent_monitor
    - rtk
    - motd
    - verify
```

Order rationale (recapped from the design): `claude_code` provisions Node + the binary; `claude_plugins` writes user-scope MCP entries to `~/.claude.json` and `~/.claude/settings.json`; `agent_monitor` then writes hook entries to `~/.claude/settings.json` last so there's no read-modify-write race even though the JSON keys are non-overlapping.

- [ ] **Step 2: Syntax check + lint**

```bash
cd /home/david/Work/sesio/sesio__ccx && make ansible-check && make ansible-lint
```

Expected: both green.

- [ ] **Step 3: Commit**

```bash
cd /home/david/Work/sesio/sesio__ccx
git add ansible/site.yml
git commit -m "feat(ansible): wire agent_monitor role after claude_plugins"
```

---

## Task 11: Extend the `verify` role

**Files:**
- Modify: `ansible/roles/verify/tasks/main.yml` — append three checks + grep, extend marker.

- [ ] **Step 1: Append the four verify tasks before the `Write provision-ok marker` task**

Open `ansible/roles/verify/tasks/main.yml`. Locate the existing `Write provision-ok marker` task and insert these blocks immediately above it (after the `Verify ccxctl motd runs without error` block):

```yaml
- name: Verify agent-monitor service is active
  ansible.builtin.command: systemctl is-active agent-monitor
  register: _v_agent_monitor
  changed_when: false

- name: Verify /api/health responds (retry — listener may take a few seconds after systemd state=started)
  ansible.builtin.uri:
    url: "http://127.0.0.1:4820/api/health"
    return_content: true
  register: _v_agent_monitor_health
  retries: 10
  delay: 2
  until: _v_agent_monitor_health.status == 200
  changed_when: false

- name: Verify Node version is >= 22 (better-sqlite3 → node:sqlite fallback floor)
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.shell: |
    source "{{ target_home }}/.asdf/asdf.sh"
    node -e 'process.exit(parseInt(process.versions.node.split(".")[0],10) >= 22 ? 0 : 1)'
  args:
    executable: /bin/bash
  register: _v_node_22
  changed_when: false

- name: Verify Claude Code hooks reference hook-handler.js
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.shell: |
    grep -q hook-handler.js {{ target_home }}/.claude/settings.json
  args:
    executable: /bin/bash
  changed_when: false
```

- [ ] **Step 2: Extend the `provision-ok` marker content**

In the same file, find the `Write provision-ok marker` task (`ansible.builtin.copy: dest: /var/log/ccx-provision-ok`). In the `content:` block, add three lines immediately above the `time:` line:

```yaml
      agent-monitor: {{ _v_agent_monitor.stdout | trim }}
      agent-health:  {{ ((_v_agent_monitor_health.json.status | default('')) == 'ok') | ternary('ok', 'failed') }}
      agent-hooks:   ok
      time:          {{ ansible_date_time.iso8601 }}
```

(`agent-hooks: ok` is unconditional because the playbook hard-fails earlier if `grep -q hook-handler.js …` returns non-zero.)

- [ ] **Step 3: Syntax check + lint**

```bash
cd /home/david/Work/sesio/sesio__ccx && make ansible-check && make ansible-lint
```

Expected: green.

- [ ] **Step 4: Commit**

```bash
cd /home/david/Work/sesio/sesio__ccx
git add ansible/roles/verify/tasks/main.yml
git commit -m "feat(ansible verify): assert agent-monitor active + healthy + hooks wired"
```

---

## Task 12: Update top-level `README.md`

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append an "Agent Monitor" section + smoke checklist**

Edit `README.md`. After the existing `ccxctl session …` block (currently the file ends at line 16), append:

```markdown

## Agent Monitor

[`hoangsonww/Claude-Code-Agent-Monitor`](https://github.com/hoangsonww/Claude-Code-Agent-Monitor) (pinned to `v1.1.0`) runs as the `agent-monitor.service` systemd unit on the ccx host, listening on `127.0.0.1:4820`. The EC2 security group does not open 4820; access from the laptop is via SSH tunnel only.

```bash
ccxctl monitor status     # is the service active + /api/health ok?
ccxctl monitor tunnel     # forward localhost:4820 → ccx 127.0.0.1:4820
ccxctl monitor logs -f    # tail journald
```

Visit `http://localhost:4820` in a browser while the tunnel is open.

To disable: comment out `agent_monitor` in `ansible/site.yml`, then `sudo systemctl disable --now agent-monitor` on the host. Hooks fail silently when the service is down (`hook-handler.js` exits 0 on connect-refused), so Claude Code sessions are never blocked.

To bump the version: edit `agent_monitor_version` in `ansible/roles/agent_monitor/defaults/main.yml` and re-run the playbook. See `docs/agent-monitor.md`.

### Smoke checklist

- [ ] On host: `systemctl is-active agent-monitor` → `active`
- [ ] On host: `curl http://127.0.0.1:4820/api/health` → `{"status":"ok",…}`
- [ ] From laptop: `ccxctl monitor tunnel` → opens; `http://localhost:4820` loads the React UI
- [ ] Triggering any Claude Code event makes it appear in the dashboard
- [ ] `ccxctl monitor logs -f` streams journald output
- [ ] `sudo systemctl restart agent-monitor` → still healthy
```

- [ ] **Step 2: Commit**

```bash
cd /home/david/Work/sesio/sesio__ccx
git add README.md
git commit -m "docs: document agent-monitor + ccxctl monitor"
```

---

## Task 13: Update `control-plane/README.md`

**Files:**
- Modify: `control-plane/README.md`

- [ ] **Step 1: Add three rows to the subcommand table**

Open `control-plane/README.md`. In the existing subcommand table, after the `ccxctl session list …` row, add:

```markdown
| `ccxctl monitor status` | systemctl + /api/health roundtrip via SSH |
| `ccxctl monitor tunnel [--print]` | forward localhost:4820 → ccx 127.0.0.1:4820 |
| `ccxctl monitor logs [-f]` | tail journald for `agent-monitor.service` |
```

- [ ] **Step 2: Commit**

```bash
cd /home/david/Work/sesio/sesio__ccx
git add control-plane/README.md
git commit -m "docs(control-plane): list ccxctl monitor subcommands"
```

---

## Task 14: Add `docs/agent-monitor.md` (version-pinning policy)

**Files:**
- Create: `docs/agent-monitor.md`

- [ ] **Step 1: Write the document**

```markdown
# Agent Monitor — operations notes

The dashboard is `hoangsonww/Claude-Code-Agent-Monitor`, MIT, vendored at a pinned tag and run as `agent-monitor.service` on the ccx EC2 host.

## Pinned version

Version source of truth:

```
ansible/roles/agent_monitor/defaults/main.yml
  agent_monitor_version: v1.1.0
```

Why pinned: upstream is a single-maintainer project and the `master` branch occasionally regresses. Pinning to a tag means the role is reproducible across re-provisions.

## Bumping

1. Identify the new tag at <https://github.com/hoangsonww/Claude-Code-Agent-Monitor/tags>.
2. Edit `agent_monitor_version` in `defaults/main.yml`.
3. Re-run the playbook on ccx (`ansible-pull` will run on the next boot, or run by hand from the host: `cd ~/sesio__ccx/ansible && ansible-playbook site.yml --tags agent_monitor` — note: tagging is not yet wired; until then, full playbook).
4. Verify: `ccxctl monitor status` → `active`, health `ok`.

What the bump does:
- The `git` task fetches the new ref and updates the working tree → `_agent_monitor_repo.changed = true`.
- That triggers `npm run setup` (rebuilds `node_modules` against any updated `package.json`) and `npm run build` (re-renders `client/dist`).
- The `restart agent-monitor` handler fires after both finish.

## Rollback

Revert the version bump in `defaults/main.yml` and re-run the playbook. The git task will check out the older ref; `node_modules` and `client/dist` are rebuilt against it. No data migration concerns — the SQLite schema lives at `/opt/agent-monitor/data/dashboard.db` and is preserved across version changes; if the new version's schema diverged forward and rollback breaks reads, delete the DB (it is monitoring exhaust, not user state).

## Manual verification

```bash
ssh david@ccx.dsylla.sesio.io
systemctl status agent-monitor
journalctl -u agent-monitor -n 100 --no-pager
curl -s http://127.0.0.1:4820/api/health | jq
```

From the laptop:

```bash
ccxctl monitor tunnel &     # foreground, but backgrounded with `&`
xdg-open http://localhost:4820
```
```

- [ ] **Step 2: Commit**

```bash
cd /home/david/Work/sesio/sesio__ccx
git add docs/agent-monitor.md
git commit -m "docs(agent-monitor): document version pinning + bump/rollback"
```

---

## Task 15: Final local verification

- [ ] **Step 1: Full test suite + lints**

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane && /usr/bin/uv run pytest -v
cd /home/david/Work/sesio/sesio__ccx && make check
```

Expected: all green. The full test count should be the prior baseline (52 in the codex-first-class-support summary) **plus 12 new tests in `test_monitor.py`** = 64.

- [ ] **Step 2: Walk the smoke checklist on the live host**

This is a manual step against real infra. Bring the box up if it isn't:

```bash
ccxctl start --no-ssh
ccxctl monitor status     # exits 0 once provision finishes
ccxctl monitor logs       # journalctl --no-pager output (no -t)
ccxctl monitor tunnel &   # forward + background
sleep 2 && curl -fsS http://localhost:4820/api/health
kill %1                   # close tunnel
```

If any of those fail, the failure must be diagnosed before declaring done — the Ansible role is the reproducible artifact and it has to actually produce a healthy service.

- [ ] **Step 3: If everything is green, no extra commit needed.**

The commits from Tasks 1–14 are the deliverable.

---

## Self-review notes

- **Spec coverage:** Tasks map 1:1 to the design components — Ansible role (defaults + handlers + tasks + template), site wiring, ccxctl `monitor.py`, all 12 named tests, verify-role checks, README + control-plane README + new `docs/agent-monitor.md`.
- **Refactor first / ui.py:** done in Task 1 before `monitor.py` lands (Task 3).
- **CFG access pattern:** `monitor.py` does `from ccx import cli` at module top and reads `cli.CFG.…` lazily inside command bodies. Test 12 (Task 7) pins this with `monkeypatch.setattr("ccx.cli.CFG", …)`. No `importlib.reload`.
- **Helper de-duplication:** the design notes lifting `_mock_run` to `tests/conftest.py` is **optional** — explicitly out of scope here to avoid churning unrelated tests.
- **Lint floor:** `make check` is run after every task that touches Ansible. Production profile.
- **Smoke step:** Task 15 calls out that the smoke checklist must actually pass on the live host before claiming done — verifying functionality, per global Rule 4.
