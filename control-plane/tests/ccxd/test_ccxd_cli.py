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
    monkeypatch.delenv("CLAUDECODE", raising=False)
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
