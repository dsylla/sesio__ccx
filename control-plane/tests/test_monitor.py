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
