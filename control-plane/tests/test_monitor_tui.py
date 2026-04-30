"""Tests for ccx.monitor_tui — dataclass, fetchers, render, loop."""
from __future__ import annotations

import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from ccx import monitor_tui


def _sample_dict() -> dict:
    return {
        "agent": "claude",
        "slug": "demo",
        "window": "claude:demo",
        "cwd": "/home/david/demo",
        "pane_pid": 1234,
        "agent_pid": 1240,
        "claude_pid": 1240,
        "uptime_seconds": 600.0,
        "usage_today": {"input": 100, "output": 50, "available": True},
        "tokens_today": {"input": 100, "output": 50},
    }


def test_session_row_from_dict_populates_all_fields():
    row = monitor_tui.SessionRow.from_dict(_sample_dict(), source="local")
    assert row.source == "local"
    assert row.agent == "claude"
    assert row.slug == "demo"
    assert row.cwd == "/home/david/demo"
    assert row.uptime_seconds == 600.0
    assert row.tokens_in == 100
    assert row.tokens_out == 50
    assert row.pid == 1240


def test_fetch_local_uses_collect_sessions(monkeypatch):
    fake_rows = [_sample_dict()]
    monkeypatch.setattr(monitor_tui, "collect_sessions", lambda: fake_rows)
    out = monitor_tui.fetch_local()
    assert len(out) == 1
    assert out[0].source == "local"
    assert out[0].slug == "demo"


def test_fetch_ccx_uses_controlpersist_and_parses_json(monkeypatch):
    captured: dict = {}

    def fake_run(cmd, *, capture_output, text, check, timeout):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=json.dumps([_sample_dict()]), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = monitor_tui.fetch_ccx(
        ssh_user="david",
        hostname="ccx.dsylla.sesio.io",
        ssh_key="/home/david/.ssh/keys/dsylla-ccx",
    )
    assert len(out) == 1
    assert out[0].source == "ccx"
    flat = " ".join(captured["cmd"])
    assert "ssh" in flat
    assert "david@ccx.dsylla.sesio.io" in flat
    # ControlPersist multiplexing — required so 5 s polls don't burn TCPs
    assert "ControlMaster=auto" in flat
    assert "ControlPersist=" in flat
    assert "ccxctl" in flat


def test_fetch_ccx_returns_empty_on_ssh_failure(monkeypatch):
    def fake_run(*a, **kw):
        return subprocess.CompletedProcess(args=a, returncode=255, stdout="", stderr="permission denied")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert monitor_tui.fetch_ccx(ssh_user="david", hostname="ccx", ssh_key="/tmp/k") == []


def test_fetch_ccx_returns_empty_on_timeout(monkeypatch):
    def fake_run(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=a, timeout=5)
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert monitor_tui.fetch_ccx(ssh_user="david", hostname="ccx", ssh_key="/tmp/k") == []


def test_fetch_ccx_returns_empty_on_garbage_stdout(monkeypatch):
    def fake_run(*a, **kw):
        return subprocess.CompletedProcess(args=a, returncode=0, stdout="not json", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert monitor_tui.fetch_ccx(ssh_user="david", hostname="ccx", ssh_key="/tmp/k") == []
