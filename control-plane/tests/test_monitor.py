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
    """Top-level monitor --help should mention all subcommands."""
    from ccx.cli import app
    result = CliRunner().invoke(app, ["monitor", "--help"])
    assert result.exit_code == 0
    out = result.stdout
    for cmd in ("status", "tunnel", "logs", "open", "close"):
        assert cmd in out


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


def test_open_when_tunnel_already_running_skips_spawn(monkeypatch, tmp_path):
    """open is idempotent: live pidfile → no Popen, browser still launches."""
    from ccx import monitor as mon
    from ccx.monitor import app

    pid_file = tmp_path / "tunnel.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mon, "_TUNNEL_PIDFILE", pid_file)
    monkeypatch.setattr(mon.os, "kill", lambda pid, sig: None)  # process "alive"
    popen_calls: list[list[str]] = []
    monkeypatch.setattr(
        mon.subprocess, "Popen",
        lambda argv, **kw: popen_calls.append(argv) or MagicMock(pid=999),
    )
    monkeypatch.setattr(mon.shutil, "which", lambda name: "/usr/bin/xdg-open")

    result = CliRunner().invoke(app, ["open"])
    assert result.exit_code == 0
    # Only the browser opener should have been Popen'd, not ssh.
    assert len(popen_calls) == 1
    assert popen_calls[0][0] == "/usr/bin/xdg-open"
    assert popen_calls[0][1].startswith("http://localhost:4820")


def test_open_no_browser_does_not_invoke_xdg_open(monkeypatch, tmp_path):
    """--no-browser skips the opener Popen even when xdg-open is present."""
    from ccx import monitor as mon
    from ccx.monitor import app

    pid_file = tmp_path / "tunnel.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mon, "_TUNNEL_PIDFILE", pid_file)
    monkeypatch.setattr(mon.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(mon.shutil, "which", lambda name: "/usr/bin/xdg-open")
    popen_calls: list[list[str]] = []
    monkeypatch.setattr(
        mon.subprocess, "Popen",
        lambda argv, **kw: popen_calls.append(argv) or MagicMock(pid=999),
    )

    result = CliRunner().invoke(app, ["open", "--no-browser"])
    assert result.exit_code == 0
    assert popen_calls == []  # neither ssh nor opener


def test_close_kills_pid_and_removes_file(monkeypatch, tmp_path):
    """close: SIGTERM the pid in the file and unlink the file."""
    from ccx import monitor as mon
    from ccx.monitor import app

    pid_file = tmp_path / "tunnel.pid"
    pid_file.write_text("54321")
    monkeypatch.setattr(mon, "_TUNNEL_PIDFILE", pid_file)
    monkeypatch.setattr(mon.os, "kill", lambda pid, sig: None)  # alive for _tunnel_pid; SIGTERM no-op
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(mon.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    result = CliRunner().invoke(app, ["close"])
    assert result.exit_code == 0
    # First kill(0) probe + then SIGTERM
    assert (54321, 0) in killed
    assert any(sig != 0 for _, sig in killed)  # SIGTERM
    assert not pid_file.exists()


def test_close_when_no_tunnel_is_a_noop(monkeypatch, tmp_path):
    """close: missing pidfile → exit 0, no kill calls."""
    from ccx import monitor as mon
    from ccx.monitor import app

    monkeypatch.setattr(mon, "_TUNNEL_PIDFILE", tmp_path / "missing.pid")
    killed: list = []
    monkeypatch.setattr(mon.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    result = CliRunner().invoke(app, ["close"])
    assert result.exit_code == 0
    assert killed == []


def test_tunnel_pid_clears_stale_file(monkeypatch, tmp_path):
    """_tunnel_pid: pidfile points at a dead pid → returns None, removes file."""
    from ccx import monitor as mon

    pid_file = tmp_path / "stale.pid"
    pid_file.write_text("99999")
    monkeypatch.setattr(mon, "_TUNNEL_PIDFILE", pid_file)
    def fake_kill(pid, sig):
        raise ProcessLookupError
    monkeypatch.setattr(mon.os, "kill", fake_kill)

    assert mon._tunnel_pid() is None
    assert not pid_file.exists()


def test_monitor_tui_listed_in_help():
    from typer.testing import CliRunner
    from ccx.cli import app
    result = CliRunner().invoke(app, ["monitor", "--help"])
    assert result.exit_code == 0
    assert "tui" in result.stdout.lower()


def test_monitor_tui_invokes_run_tui_with_filter_and_debug(monkeypatch):
    called: dict = {}
    def fake_run(sources, **kw):
        called["sources"] = sources
        called["kw"] = kw
        return 0
    monkeypatch.setattr("ccx.monitor_tui.run_tui", fake_run)

    from typer.testing import CliRunner
    from ccx.cli import app
    result = CliRunner().invoke(app, ["monitor", "tui", "--source", "local", "--debug"])
    assert result.exit_code == 0
    assert called["kw"]["initial_filter"] == "local"
    assert called["kw"]["debug"] is True


def test_monitor_tui_rejects_invalid_source():
    from typer.testing import CliRunner
    from ccx.cli import app
    result = CliRunner().invoke(app, ["monitor", "tui", "--source", "nope"])
    assert result.exit_code != 0
