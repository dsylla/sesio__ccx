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
