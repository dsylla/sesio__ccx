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


def test_fetch_local_merges_collect_sessions_and_freestanding(monkeypatch):
    """fetch_local now returns both tmux-managed sessions AND any free-standing
    claude process detected via /proc. The two sources are merged with the
    /proc scan filtered to PIDs not already covered by collect_sessions."""
    fake_rows = [_sample_dict()]
    monkeypatch.setattr(monitor_tui, "collect_sessions", lambda: fake_rows)

    captured: dict = {}

    def fake_scan(exclude_pids):
        captured["exclude_pids"] = exclude_pids
        return [
            monitor_tui.SessionRow(
                source="local", agent="claude", slug="standalone",
                cwd="/home/david/elsewhere", pid=9999,
                uptime_seconds=12.0, tokens_in=42, tokens_out=7,
            )
        ]

    monkeypatch.setattr(monitor_tui, "_scan_freestanding_claudes", fake_scan)
    out = monitor_tui.fetch_local()

    assert {r.slug for r in out} == {"demo", "standalone"}
    # The scan must be told which pids are already counted.
    assert captured["exclude_pids"] == {1240}


def test_scan_freestanding_finds_claude_proc(tmp_path, monkeypatch):
    """Build a fake /proc with one claude pid + one bash pid; scan should
    return a single SessionRow for the claude pid."""
    proc = tmp_path / "proc"
    proc.mkdir()
    # claude pid 555
    (proc / "555").mkdir()
    (proc / "555" / "comm").write_text("claude\n")
    cwd_link = tmp_path / "proj-foo"
    cwd_link.mkdir()
    (proc / "555" / "cwd").symlink_to(cwd_link)
    # bash pid 777 — should be ignored
    (proc / "777").mkdir()
    (proc / "777" / "comm").write_text("bash\n")

    monkeypatch.setattr(monitor_tui, "process_uptime_seconds", lambda pid: 30.0)
    monkeypatch.setattr(monitor_tui, "parse_jsonl_tokens_today", lambda _: {"input": 11, "output": 4})
    monkeypatch.setattr(monitor_tui, "project_jsonl_files", lambda _cwd: [])

    rows = monitor_tui._scan_freestanding_claudes(set(), proc_root=str(proc))
    assert len(rows) == 1
    assert rows[0].pid == 555
    assert rows[0].slug == "proj-foo"
    assert rows[0].cwd == str(cwd_link)
    assert rows[0].uptime_seconds == 30.0
    assert rows[0].tokens_in == 11
    assert rows[0].tokens_out == 4


def test_scan_freestanding_skips_excluded_pids(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "100").mkdir()
    (proc / "100" / "comm").write_text("claude\n")
    rows = monitor_tui._scan_freestanding_claudes({100}, proc_root=str(proc))
    assert rows == []


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
    # Wrap remote command in `bash -lc` so ~/.local/bin (where ccxctl
    # lives on the EC2 box) is on PATH — non-login ssh shells skip
    # the profile that adds it.
    assert "bash" in flat
    assert "-lc" in flat


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


from rich.console import Console


def _row(**over):
    base = dict(
        source="local", agent="claude", slug="demo", cwd="/home/david/demo",
        pid=1234, uptime_seconds=600.0, tokens_in=1500, tokens_out=750,
    )
    base.update(over)
    return monitor_tui.SessionRow(**base)


def _render(panel) -> str:
    console = Console(record=True, width=120)
    console.print(panel)
    return console.export_text()


def test_build_panel_includes_source_column_and_help_caption():
    out = _render(monitor_tui.build_panel([_row()]))
    assert "SOURCE" in out.upper()
    assert "local" in out
    assert "demo" in out
    # Help caption mentions all three supported keys
    assert "q" in out and "r" in out and "f" in out


def test_build_panel_renders_humanized_tokens():
    out = _render(monitor_tui.build_panel([_row(tokens_in=1500, tokens_out=750)]))
    assert "1.5k" in out
    assert "750" in out  # too small to humanize


def test_build_panel_handles_unreachable_source():
    out = _render(monitor_tui.build_panel([], unreachable_sources=["ccx"]))
    assert "ccx" in out and "unreachable" in out.lower()


def test_build_panel_empty_local_and_ccx_no_unreachable():
    out = _render(monitor_tui.build_panel([]))
    assert "no sessions" in out.lower()


def test_build_panel_includes_rate_limits_when_provided():
    out = _render(monitor_tui.build_panel(
        [_row()],
        rate_limits={"five_hour": {"used_percentage": 41.0, "resets_at": 9999999999},
                     "seven_day": {"used_percentage": 47.0, "resets_at": 9999999999}},
    ))
    assert "5h" in out and "41%" in out
    assert "7d" in out and "47%" in out


def test_build_panel_omits_rate_limits_when_none():
    out = _render(monitor_tui.build_panel([_row()], rate_limits=None))
    assert "5h" not in out
    assert "7d" not in out


def test_load_rate_limits_reads_json(tmp_path, monkeypatch):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"rate_limits": {
        "five_hour": {"used_percentage": 30, "resets_at": 1},
        "seven_day": {"used_percentage": 40, "resets_at": 2},
    }}))
    out = monitor_tui.load_rate_limits(p)
    assert out["five_hour"]["used_percentage"] == 30


def test_load_rate_limits_returns_none_on_missing_file(tmp_path):
    assert monitor_tui.load_rate_limits(tmp_path / "nope.json") is None


import io
from unittest.mock import MagicMock


def test_collect_rows_combines_local_and_ccx():
    fa = MagicMock(return_value=[_row(slug="L")])
    fb = MagicMock(return_value=[_row(slug="C", source="ccx")])
    rows, unreachable = monitor_tui.collect_rows([("local", fa), ("ccx", fb)])
    assert {r.slug for r in rows} == {"L", "C"}
    assert unreachable == []


def test_collect_rows_filters_disabled_source():
    fa = MagicMock(return_value=[_row(slug="L")])
    fb = MagicMock(return_value=[_row(slug="C", source="ccx")])
    rows, _ = monitor_tui.collect_rows(
        [("local", fa), ("ccx", fb)], filter_source="local",
    )
    assert {r.slug for r in rows} == {"L"}


def test_collect_rows_marks_failing_source_unreachable():
    bad = MagicMock(side_effect=OSError("boom"))
    rows, unreachable = monitor_tui.collect_rows([("ccx", bad)])
    assert rows == []
    assert unreachable == ["ccx"]


def test_run_tui_non_tty_renders_one_frame_and_exits_zero(monkeypatch, capsys):
    """The non-interactive path must be deterministic and CI-safe."""
    monkeypatch.setattr("sys.stdin", io.StringIO())  # not a tty
    fakes = [("local", MagicMock(return_value=[_row(slug="X")]))]
    rc = monitor_tui.run_tui(fakes, interval=99.0)
    assert rc == 0
    out = capsys.readouterr().out
    assert "X" in out


def test_cycle_filter_progresses_both_local_ccx_both():
    assert monitor_tui.cycle_filter(None) == "local"
    assert monitor_tui.cycle_filter("local") == "ccx"
    assert monitor_tui.cycle_filter("ccx") is None
