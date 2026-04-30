"""ccxctl ccxd ... — install/manage the ccxd daemon and its claude-code hooks."""
from __future__ import annotations

import json
import os
import shlex
import socket as _socket
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

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
