"""Tests for ccx.ccxd.discovery — /proc walk + PID-session linkage."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from ccx.ccxd.discovery import discover_sessions


def _build_fake_proc(tmp_path: Path, pid: int, comm: str, cwd: str,
                     fd_targets: dict[str, str] | None = None,
                     stat_starttime: int = 50000) -> None:
    """Build a fake /proc/<pid> tree for testing."""
    proc_pid = tmp_path / "proc" / str(pid)
    proc_pid.mkdir(parents=True, exist_ok=True)
    (proc_pid / "comm").write_text(f"{comm}\n")
    # cwd as a regular file (can't symlink to non-existent in tests easily)
    cwd_target = Path(cwd)
    cwd_target.mkdir(parents=True, exist_ok=True)
    (proc_pid / "cwd").symlink_to(cwd_target)
    # stat file
    (proc_pid / "stat").write_text(
        f"{pid} ({comm}) S " + "0 " * 18 + f"{stat_starttime} " + "0 " * 30
    )
    # fd directory
    fd_dir = proc_pid / "fd"
    fd_dir.mkdir(exist_ok=True)
    if fd_targets:
        for fd_num, target in fd_targets.items():
            target_path = Path(target)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.touch()
            (fd_dir / fd_num).symlink_to(target_path)


class TestDiscoverSessions:
    def test_finds_claude_with_jsonl_fd(self, tmp_path: Path, monkeypatch):
        projects = tmp_path / "claude_projects"
        jsonl = projects / "-work-myproj" / "abc-123.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text('{"type":"ai-title","aiTitle":"hello"}\n')

        _build_fake_proc(
            tmp_path, pid=555, comm="claude",
            cwd=str(tmp_path / "work" / "myproj"),
            fd_targets={"3": str(jsonl)},
        )
        monkeypatch.setattr("ccx.ccxd.discovery._PROC", str(tmp_path / "proc"))
        monkeypatch.setattr("ccx.ccxd.discovery._CLAUDE_PROJECTS_DIR", str(projects))
        monkeypatch.setattr("ccx.ccxd.discovery._BOOT_TIME", 1000.0)
        monkeypatch.setattr("ccx.ccxd.discovery._NOW_FN", lambda: 1700.0)

        sessions = discover_sessions()
        assert len(sessions) == 1
        s = sessions[0]
        assert s.session_id == "abc-123"
        assert s.pid == 555

    def test_skips_non_claude_processes(self, tmp_path: Path, monkeypatch):
        _build_fake_proc(tmp_path, pid=100, comm="bash",
                         cwd=str(tmp_path / "work"))
        monkeypatch.setattr("ccx.ccxd.discovery._PROC", str(tmp_path / "proc"))
        monkeypatch.setattr("ccx.ccxd.discovery._CLAUDE_PROJECTS_DIR", str(tmp_path / "p"))
        sessions = discover_sessions()
        assert sessions == []

    def test_skips_subagent_jsonl(self, tmp_path: Path, monkeypatch):
        """FDs pointing into subagents/ subdirectory are not top-level sessions."""
        projects = tmp_path / "claude_projects"
        jsonl = projects / "-work-proj" / "ses-1" / "subagents" / "agent-1.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.touch()

        _build_fake_proc(
            tmp_path, pid=600, comm="claude",
            cwd=str(tmp_path / "work" / "proj"),
            fd_targets={"4": str(jsonl)},
        )
        monkeypatch.setattr("ccx.ccxd.discovery._PROC", str(tmp_path / "proc"))
        monkeypatch.setattr("ccx.ccxd.discovery._CLAUDE_PROJECTS_DIR", str(projects))
        sessions = discover_sessions()
        assert sessions == []

    def test_handles_permission_error(self, tmp_path: Path, monkeypatch):
        proc = tmp_path / "proc" / "999"
        proc.mkdir(parents=True)
        (proc / "comm").write_text("claude\n")
        # No cwd symlink — will raise
        monkeypatch.setattr("ccx.ccxd.discovery._PROC", str(tmp_path / "proc"))
        monkeypatch.setattr("ccx.ccxd.discovery._CLAUDE_PROJECTS_DIR", str(tmp_path / "p"))
        # Should not raise
        sessions = discover_sessions()
        assert sessions == []
