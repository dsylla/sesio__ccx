"""Tests for ccx.ccxd.hook_emitter — claude-code -> ccxd DGRAM bridge."""
from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest


def _spawn_emitter(event: str, payload: dict, runtime_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ccx.ccxd.hook_emitter", event],
        input=json.dumps(payload),
        text=True,
        env={"XDG_RUNTIME_DIR": str(runtime_dir), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        timeout=2,
    )


def test_emitter_sends_dgram_envelope(tmp_path: Path):
    sock_path = tmp_path / "ccxd-hooks.sock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    s.bind(str(sock_path))
    try:
        proc = _spawn_emitter("SessionStart",
                              {"session_id": "abc", "cwd": "/x"}, tmp_path)
        assert proc.returncode == 0, proc.stderr
        s.settimeout(1.0)
        data, _ = s.recvfrom(1024)
        msg = json.loads(data.decode())
        assert msg == {
            "event": "SessionStart",
            "payload": {"session_id": "abc", "cwd": "/x"},
        }
    finally:
        s.close()


def test_emitter_silent_drops_when_socket_missing(tmp_path: Path):
    """No daemon? Emitter must exit 0 silently — Claude Code can't be blocked."""
    proc = _spawn_emitter("SessionStart", {"session_id": "abc"}, tmp_path)
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == ""


def test_emitter_silent_drops_on_garbage_stdin(tmp_path: Path):
    sock_path = tmp_path / "ccxd-hooks.sock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    s.bind(str(sock_path))
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "ccx.ccxd.hook_emitter", "Notification"],
            input="not json {",
            text=True,
            env={"XDG_RUNTIME_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
            capture_output=True, timeout=2,
        )
        assert proc.returncode == 0
        assert proc.stdout == ""
    finally:
        s.close()


def test_emitter_requires_event_argv(tmp_path: Path):
    proc = subprocess.run(
        [sys.executable, "-m", "ccx.ccxd.hook_emitter"],
        input='{"x": 1}', text=True,
        env={"XDG_RUNTIME_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True, timeout=2,
    )
    assert proc.returncode != 0
