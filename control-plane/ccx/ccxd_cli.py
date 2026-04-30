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
