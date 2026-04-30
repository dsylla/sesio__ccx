from __future__ import annotations

import datetime as dt
import datetime as _dt
import json
from pathlib import Path

import pytest

from ccx import sessions


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
    """Claude Code's convention: /, ., and _ all become - in the per-project dir name."""
    from ccx.sessions import encode_project_dir
    assert encode_project_dir("/home/david/Work/sesio/ccx") == "-home-david-Work-sesio-ccx"
    # Underscores must collapse to dashes — `sesio__ccx` shows up on disk
    # as `sesio--ccx`. Anything that didn't would point ccx tooling at a
    # directory that doesn't exist.
    assert encode_project_dir("/home/david/Work/sesio/sesio__ccx") == \
        "-home-david-Work-sesio-sesio--ccx"
    # Dots too.
    assert encode_project_dir("/home/david/.config/foo") == "-home-david--config-foo"


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


def test_parse_jsonl_tokens_today_includes_cache_tokens(tmp_path):
    f = tmp_path / "s.jsonl"
    today_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    f.write_text(json.dumps({
        "timestamp": today_iso,
        "message": {"id": "msg_1", "usage": {
            "input_tokens": 3, "output_tokens": 34,
            "cache_creation_input_tokens": 19793,
            "cache_read_input_tokens": 11752,
        }},
    }) + "\n")
    out = sessions.parse_jsonl_tokens_today([f])
    # cache reads/creates are inputs from the model's perspective and are
    # billed; both must be counted.
    assert out["input"] == 3 + 19793 + 11752
    assert out["output"] == 34


def test_parse_jsonl_tokens_today_dedupes_by_message_id(tmp_path):
    f = tmp_path / "s.jsonl"
    today_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    entry = json.dumps({
        "timestamp": today_iso,
        "message": {"id": "msg_dup", "usage": {"input_tokens": 100, "output_tokens": 50}},
    })
    f.write_text(entry + "\n" + entry + "\n")  # same message twice
    out = sessions.parse_jsonl_tokens_today([f])
    assert out["input"] == 100
    assert out["output"] == 50


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


def test_collect_sessions_happy_path(tmp_path, monkeypatch):
    """Merge tmux rows + claude pid + tokens into a canonical list."""
    from ccx.sessions import collect_sessions

    # Fake /proc so find_claude_pid returns 102 for pane 42
    proc = tmp_path / "proc"
    (proc / "42/task/42").mkdir(parents=True)
    (proc / "42/task/42/children").write_text("102 ")
    (proc / "42/comm").write_text("bash\n")
    (proc / "102/task/102").mkdir(parents=True)
    (proc / "102/task/102/children").write_text("")
    (proc / "102/comm").write_text("claude\n")
    (proc / "102/stat").write_text(
        "102 (claude) S " + "0 " * 18 + "50000 " + "0 " * 30
    )  # field 22 = starttime_ticks = 500 * 100
    monkeypatch.setattr("ccx.sessions._PROC", str(proc))
    monkeypatch.setattr("ccx.sessions._NOW_FN", lambda: 1700)
    monkeypatch.setattr("ccx.sessions._BOOT_FN", lambda: 1000)

    # Fake claude_projects_dir → no jsonl → zero tokens
    monkeypatch.setattr(
        "ccx.sessions._CLAUDE_PROJECTS_DIR",
        str(tmp_path / "not-there"),
    )

    # Mock tmux
    with patch("ccx.sessions.tmux_list_windows", return_value=[
        {"slug": "ccx", "activity": 1700000000,
         "cwd": "/home/david/Work/sesio/ccx", "pane_pid": 42}
    ]):
        sessions = collect_sessions()

    assert sessions == [{
        "agent": "claude",
        "slug": "ccx",
        "window": "ccx",
        "cwd": "/home/david/Work/sesio/ccx",
        "pane_pid": 42,
        "agent_pid": 102,
        "claude_pid": 102,
        "uptime_seconds": pytest.approx(200, abs=1),  # now(1700) - (boot(1000) + 50000/100)
        "usage_today": {"input": 0, "output": 0, "available": True},
        "tokens_today": {"input": 0, "output": 0},
    }]


from typer.testing import CliRunner


def test_session_list_json_empty():
    from ccx.sessions import app
    with patch("ccx.sessions.collect_sessions", return_value=[]):
        result = CliRunner().invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_session_list_table_format():
    from ccx.sessions import app
    row = {
        "slug": "ccx", "cwd": "/home/david/Work/sesio/ccx", "pane_pid": 42,
        "claude_pid": 102, "uptime_seconds": 120.0,
        "tokens_today": {"input": 100, "output": 50},
    }
    with patch("ccx.sessions.collect_sessions", return_value=[row]):
        result = CliRunner().invoke(app, ["list"])
    assert result.exit_code == 0
    assert "ccx" in result.stdout
    assert "100" in result.stdout  # input tokens
    assert "50" in result.stdout   # output tokens


def test_session_launch_creates_when_absent(tmp_path):
    from ccx.sessions import app
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        # has-session returns 1 (absent) when asked, 0 otherwise
        if "has-session" in argv:
            return _mock_run(returncode=1)
        return _mock_run(returncode=0)

    with patch("ccx.sessions.subprocess.run", side_effect=fake_run):
        result = CliRunner().invoke(app, ["launch", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    # assert both new-session -d and new-window were called
    assert any("new-session" in c and "-d" in c for c in calls)
    assert any("new-window" in c for c in calls)


def test_session_launch_attaches_when_present(tmp_path):
    from ccx.sessions import app
    # has-session returns 0 (present) → launch should NOT call new-window
    with patch("ccx.sessions.subprocess.run", return_value=_mock_run(returncode=0)) as run:
        result = CliRunner().invoke(app, ["launch", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    argvs = [call.args[0] for call in run.call_args_list]
    assert not any("new-window" in a for a in argvs)


def test_session_kill_calls_tmux_kill_window():
    from ccx.sessions import app
    with patch("ccx.sessions.subprocess.run", return_value=_mock_run(returncode=0)) as run:
        result = CliRunner().invoke(app, ["kill", "ccx"])
    assert result.exit_code == 0
    argvs = [call.args[0] for call in run.call_args_list]
    assert any("kill-window" in a for a in argvs)


def test_session_attach_without_slug_targets_session(monkeypatch):
    from ccx.sessions import app, SESSION_NAME
    captured: list[list[str]] = []
    monkeypatch.setattr("ccx.sessions.os.execvp", lambda _, argv: captured.append(argv))
    CliRunner().invoke(app, ["attach"])
    assert captured, "execvp was not called"
    assert captured[0] == ["tmux", "attach-session", "-t", SESSION_NAME]


def test_session_attach_with_slug_targets_window(monkeypatch):
    from ccx.sessions import app, SESSION_NAME
    captured: list[list[str]] = []
    monkeypatch.setattr("ccx.sessions.os.execvp", lambda _, argv: captured.append(argv))
    # Bare slug matches an existing legacy claude window
    monkeypatch.setattr("ccx.sessions.tmux_has_window", lambda s, **_: True)
    CliRunner().invoke(app, ["attach", "ccx"])
    assert captured[0] == ["tmux", "attach-session", "-t", f"{SESSION_NAME}:ccx"]


def test_agent_catalog_contains_claude_and_codex():
    from ccx.agents import AGENTS, DEFAULT_AGENT, get_agent

    assert DEFAULT_AGENT == "claude"
    assert get_agent("claude").command == "claude"
    assert get_agent("codex").command == "codex"
    assert set(AGENTS) >= {"claude", "codex"}


def test_agent_window_names_round_trip_and_legacy_claude():
    from ccx.agents import split_window_name, window_name

    assert window_name("codex", "sesio__ccx") == "codex:sesio__ccx"
    assert split_window_name("codex:sesio__ccx") == ("codex", "sesio__ccx")
    assert split_window_name("sesio__ccx") == ("claude", "sesio__ccx")


def test_find_agent_pid_accepts_codex_process(tmp_path, monkeypatch):
    from ccx.agents import get_agent
    from ccx.sessions import find_agent_pid

    proc = tmp_path / "proc"
    (proc / "100/task/100").mkdir(parents=True)
    (proc / "100/task/100/children").write_text("101 ")
    (proc / "100/comm").write_text("bash\n")
    (proc / "101/task/101").mkdir(parents=True)
    (proc / "101/task/101/children").write_text("")
    (proc / "101/comm").write_text("codex\n")
    monkeypatch.setattr("ccx.sessions._PROC", str(proc))

    assert find_agent_pid(100, get_agent("codex")) == 101


def test_collect_sessions_reports_agent_and_legacy_claude(tmp_path, monkeypatch):
    from ccx.sessions import collect_sessions

    proc = tmp_path / "proc"
    for pid, comm in [(42, "bash"), (102, "claude"), (43, "bash"), (103, "codex")]:
        (proc / f"{pid}/task/{pid}").mkdir(parents=True)
        (proc / f"{pid}/comm").write_text(f"{comm}\n")
    (proc / "42/task/42/children").write_text("102 ")
    (proc / "43/task/43/children").write_text("103 ")
    (proc / "102/task/102/children").write_text("")
    (proc / "102/stat").write_text("102 (claude) S " + "0 " * 18 + "50000 " + "0 " * 30)
    (proc / "103/task/103/children").write_text("")
    (proc / "103/stat").write_text("103 (codex) S " + "0 " * 18 + "60000 " + "0 " * 30)

    monkeypatch.setattr("ccx.sessions._PROC", str(proc))
    monkeypatch.setattr("ccx.sessions._NOW_FN", lambda: 1700)
    monkeypatch.setattr("ccx.sessions._BOOT_FN", lambda: 1000)
    monkeypatch.setattr("ccx.sessions._CLAUDE_PROJECTS_DIR", str(tmp_path / "not-there"))

    with patch("ccx.sessions.tmux_list_windows", return_value=[
        {"slug": "legacy", "activity": 1, "cwd": "/work/legacy", "pane_pid": 42},
        {"slug": "codex:modern", "activity": 2, "cwd": "/work/modern", "pane_pid": 43},
    ]):
        rows = collect_sessions()

    assert rows[0]["agent"] == "claude"
    assert rows[0]["slug"] == "legacy"
    assert rows[0]["agent_pid"] == 102
    assert rows[1]["agent"] == "codex"
    assert rows[1]["slug"] == "modern"
    assert rows[1]["agent_pid"] == 103
    assert rows[1]["usage_today"]["available"] is False


def test_session_launch_codex_starts_codex_window(tmp_path):
    from ccx.sessions import app

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if "has-session" in argv:
            return _mock_run(returncode=1)
        return _mock_run(returncode=0)

    with patch("ccx.sessions.subprocess.run", side_effect=fake_run):
        result = CliRunner().invoke(app, ["launch", "--agent", "codex", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    new_window = next(c for c in calls if "new-window" in c)
    assert "codex:{}".format(tmp_path.name.lower()) in new_window
    assert new_window[-1] == "codex"


def test_session_list_table_includes_agent():
    from ccx.sessions import app

    row = {
        "agent": "codex",
        "slug": "ccx",
        "window": "codex:ccx",
        "cwd": "/work/ccx",
        "pane_pid": 42,
        "agent_pid": 102,
        "claude_pid": None,
        "uptime_seconds": 120.0,
        "usage_today": {"input": 0, "output": 0, "available": False},
        "tokens_today": {"input": 0, "output": 0},
    }
    with patch("ccx.sessions.collect_sessions", return_value=[row]):
        result = CliRunner().invoke(app, ["list"])

    assert result.exit_code == 0
    assert "AGENT" in result.stdout
    assert "codex" in result.stdout
    assert "ccx" in result.stdout
