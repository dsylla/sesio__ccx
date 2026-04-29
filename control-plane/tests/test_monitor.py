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
