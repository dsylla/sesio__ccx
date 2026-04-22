from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest


def test_slug_basic():
    from ccx.sessions import slug
    assert slug("/home/david/Work/sesio/sesio__ccx") == "sesio__ccx"


def test_slug_special_chars():
    from ccx.sessions import slug
    assert slug("/home/david/Work/My Project!") == "my-project-"


def test_slug_lower_collapse_dashes():
    from ccx.sessions import slug
    assert slug("/tmp/A  B  C") == "a-b-c"


def test_encode_project_dir():
    """Claude Code's convention: /home/david/x/y -> -home-david-x-y"""
    from ccx.sessions import encode_project_dir
    assert encode_project_dir("/home/david/Work/sesio/ccx") == "-home-david-Work-sesio-ccx"


def test_parse_jsonl_tokens_today_sums_today(tmp_path: Path):
    from ccx.sessions import parse_jsonl_tokens_today
    today = dt.datetime.now(dt.timezone.utc).isoformat()
    yesterday = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat()
    f = tmp_path / "log.jsonl"
    f.write_text(
        json.dumps({"timestamp": today,     "message": {"usage": {"input_tokens": 100, "output_tokens": 50}}}) + "\n"
        + json.dumps({"timestamp": today,   "message": {"usage": {"input_tokens": 7,   "output_tokens": 3}}})  + "\n"
        + json.dumps({"timestamp": yesterday,"message": {"usage": {"input_tokens": 999, "output_tokens": 999}}}) + "\n"
    )
    assert parse_jsonl_tokens_today([f]) == {"input": 107, "output": 53}


def test_parse_jsonl_tokens_today_handles_missing_keys(tmp_path: Path):
    from ccx.sessions import parse_jsonl_tokens_today
    today = dt.datetime.now(dt.timezone.utc).isoformat()
    f = tmp_path / "log.jsonl"
    f.write_text(
        json.dumps({"timestamp": today}) + "\n"
        + "not json\n"
        + json.dumps({"timestamp": today, "message": {"usage": {"input_tokens": 5, "output_tokens": 2}}}) + "\n"
    )
    assert parse_jsonl_tokens_today([f]) == {"input": 5, "output": 2}


def test_parse_jsonl_tokens_today_no_files():
    from ccx.sessions import parse_jsonl_tokens_today
    assert parse_jsonl_tokens_today([]) == {"input": 0, "output": 0}


from unittest.mock import patch, MagicMock
import subprocess


def _mock_run(stdout: str = "", returncode: int = 0):
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.stdout = stdout
    m.returncode = returncode
    m.stderr = ""
    return m


def test_tmux_list_windows_parses_format():
    from ccx.sessions import tmux_list_windows
    raw = (
        "ccx|1700000000|/home/david/Work/sesio/sesio__ccx|42\n"
        "foo|1700000010|/home/david/Work/foo|100\n"
    )
    with patch("ccx.sessions.subprocess.run", return_value=_mock_run(raw)):
        rows = tmux_list_windows()
    assert rows == [
        {"slug": "ccx", "activity": 1700000000, "cwd": "/home/david/Work/sesio/sesio__ccx", "pane_pid": 42},
        {"slug": "foo", "activity": 1700000010, "cwd": "/home/david/Work/foo", "pane_pid": 100},
    ]


def test_tmux_list_windows_no_session_returns_empty():
    from ccx.sessions import tmux_list_windows
    err = _mock_run("", returncode=1)
    err.stderr = "no server running on /tmp/tmux-1000/default"
    with patch("ccx.sessions.subprocess.run", return_value=err):
        assert tmux_list_windows() == []


def test_tmux_has_window_true():
    from ccx.sessions import tmux_has_window
    with patch("ccx.sessions.subprocess.run", return_value=_mock_run(returncode=0)):
        assert tmux_has_window("ccx") is True


def test_tmux_has_window_false():
    from ccx.sessions import tmux_has_window
    with patch("ccx.sessions.subprocess.run", return_value=_mock_run(returncode=1)):
        assert tmux_has_window("ccx") is False


def test_find_claude_pid_reads_proc(tmp_path, monkeypatch):
    """Walk /proc/<pane_pid>/task/<tid>/children for a claude descendant."""
    from ccx.sessions import find_claude_pid
    # Build a fake /proc tree: pane=100 → child 101 (bash) → child 102 (claude)
    proc = tmp_path / "proc"
    (proc / "100/task/100").mkdir(parents=True)
    (proc / "100/task/100/children").write_text("101 ")
    (proc / "101/task/101").mkdir(parents=True)
    (proc / "101/task/101/children").write_text("102 ")
    (proc / "101/comm").write_text("bash\n")
    (proc / "102/task/102").mkdir(parents=True)
    (proc / "102/task/102/children").write_text("")
    (proc / "102/comm").write_text("claude\n")
    monkeypatch.setattr("ccx.sessions._PROC", str(proc))
    assert find_claude_pid(100) == 102


def test_find_claude_pid_none_when_absent(tmp_path, monkeypatch):
    from ccx.sessions import find_claude_pid
    proc = tmp_path / "proc"
    (proc / "100/task/100").mkdir(parents=True)
    (proc / "100/task/100/children").write_text("")
    (proc / "100/comm").write_text("bash\n")
    monkeypatch.setattr("ccx.sessions._PROC", str(proc))
    assert find_claude_pid(100) is None
