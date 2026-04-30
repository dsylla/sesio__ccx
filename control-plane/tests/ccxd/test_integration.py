"""Integration test — spawn daemon, fire hook, query state."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture
def runtime_dir(tmp_path: Path):
    """Provide a tmp runtime dir for the daemon."""
    return tmp_path / "runtime"


@pytest.mark.asyncio
class TestIntegration:
    async def test_daemon_lifecycle(self, tmp_path: Path):
        """Start daemon as subprocess, query it, shut it down cleanly."""
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        env = os.environ.copy()
        env["XDG_RUNTIME_DIR"] = str(runtime)
        env["CCXD_LOG_LEVEL"] = "debug"
        # Ensure no real /proc scanning interferes
        env["CCXD_SKIP_DISCOVERY"] = "1"

        proc = subprocess.Popen(
            [sys.executable, "-m", "ccx.ccxd"],
            env=env,
            cwd=str(Path(__file__).parents[2]),  # control-plane/
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        control_sock = runtime / "ccxd.sock"
        hook_sock = runtime / "ccxd-hooks.sock"

        # Wait for sockets to appear (up to 3s)
        for _ in range(30):
            if control_sock.exists() and hook_sock.exists():
                break
            await asyncio.sleep(0.1)
        else:
            proc.terminate()
            proc.wait(timeout=5)
            pytest.fail("daemon did not create sockets within 3s")

        try:
            # Send a hook event via DGRAM
            dgram = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            payload = json.dumps({
                "event": "SessionStart",
                "payload": {
                    "hook_event_name": "SessionStart",
                    "session_id": "integ-test-ses",
                    "cwd": "/tmp/test-project",
                },
            })
            dgram.sendto(payload.encode(), str(hook_sock))
            dgram.close()

            # Give daemon time to process
            await asyncio.sleep(0.2)

            # Query via control socket
            ctrl = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            ctrl.connect(str(control_sock))
            ctrl.settimeout(2.0)
            query = json.dumps({"id": 1, "method": "query", "params": {}}) + "\n"
            ctrl.sendall(query.encode())

            data = b""
            while b"\n" not in data:
                chunk = ctrl.recv(4096)
                if not chunk:
                    break
                data += chunk
            ctrl.close()

            response = json.loads(data.decode().strip())
            assert response["id"] == 1
            sessions = response["result"]["sessions"]
            assert len(sessions) == 1
            assert sessions[0]["session_id"] == "integ-test-ses"
            assert sessions[0]["cwd"] == "/tmp/test-project"

        finally:
            # Clean shutdown via SIGTERM
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

        # Verify sockets were cleaned up
        assert not control_sock.exists()
        assert not hook_sock.exists()

    async def test_subscribe_receives_hook_events(self, tmp_path: Path):
        """Subscribe, fire a hook, verify the event arrives on the subscription."""
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        env = os.environ.copy()
        env["XDG_RUNTIME_DIR"] = str(runtime)
        env["CCXD_LOG_LEVEL"] = "warning"
        env["CCXD_SKIP_DISCOVERY"] = "1"

        proc = subprocess.Popen(
            [sys.executable, "-m", "ccx.ccxd"],
            env=env,
            cwd=str(Path(__file__).parents[2]),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        control_sock = runtime / "ccxd.sock"
        hook_sock = runtime / "ccxd-hooks.sock"

        for _ in range(30):
            if control_sock.exists() and hook_sock.exists():
                break
            await asyncio.sleep(0.1)
        else:
            proc.terminate()
            proc.wait(timeout=5)
            pytest.fail("daemon sockets not created")

        try:
            # Connect and subscribe
            ctrl = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            ctrl.connect(str(control_sock))
            ctrl.settimeout(3.0)

            sub_req = json.dumps({"id": 1, "method": "subscribe", "params": {"events": ["session.*"]}}) + "\n"
            ctrl.sendall(sub_req.encode())

            # Read subscribe response
            data = b""
            while b"\n" not in data:
                data += ctrl.recv(4096)
            sub_resp = json.loads(data.decode().strip())
            assert "sub_id" in sub_resp["result"]

            # Fire a notification hook
            dgram = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            # First seed the session
            dgram.sendto(json.dumps({
                "event": "SessionStart",
                "payload": {"hook_event_name": "SessionStart", "session_id": "sub-ses", "cwd": "/x"},
            }).encode(), str(hook_sock))
            await asyncio.sleep(0.1)
            # Then fire notification
            dgram.sendto(json.dumps({
                "event": "Notification",
                "payload": {
                    "hook_event_name": "Notification",
                    "session_id": "sub-ses",
                    "cwd": "/x",
                    "notification_type": "permission_prompt",
                },
            }).encode(), str(hook_sock))
            dgram.close()

            # Read events from subscription
            await asyncio.sleep(0.2)
            events_raw = b""
            try:
                while True:
                    chunk = ctrl.recv(4096)
                    if not chunk:
                        break
                    events_raw += chunk
            except socket.timeout:
                pass
            ctrl.close()

            # Parse all received event lines
            event_lines = [json.loads(line) for line in events_raw.decode().strip().split("\n") if line.strip()]
            event_names = [e.get("event") for e in event_lines]
            assert "session.added" in event_names
            assert "session.attention" in event_names

        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
