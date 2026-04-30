"""Tests for ccx.ccxd.server — sockets + subscriber broadcast."""
from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import pytest
import pytest_asyncio

from ccx.ccxd.server import DaemonServer
from ccx.ccxd.state import StateManager
from ccx.ccxd.store import MemoryStore


@pytest.fixture
def runtime_dir(tmp_path: Path):
    return tmp_path


@pytest.fixture
def state_mgr():
    return StateManager(MemoryStore())


@pytest_asyncio.fixture
async def server(runtime_dir: Path, state_mgr: StateManager):
    srv = DaemonServer(state_mgr, runtime_dir=runtime_dir)
    await srv.start()
    yield srv
    await srv.stop()


async def _read_line(reader: asyncio.StreamReader, *, timeout: float = 2.0) -> bytes:
    return await asyncio.wait_for(reader.readline(), timeout=timeout)


@pytest.mark.asyncio
class TestDaemonServer:
    async def test_control_socket_exists_after_start(self, server: DaemonServer, runtime_dir: Path):
        assert (runtime_dir / "ccxd.sock").exists()

    async def test_hook_socket_exists_after_start(self, server: DaemonServer, runtime_dir: Path):
        assert (runtime_dir / "ccxd-hooks.sock").exists()

    async def test_query_rpc_round_trip(self, server: DaemonServer, runtime_dir: Path):
        reader, writer = await asyncio.open_unix_connection(str(runtime_dir / "ccxd.sock"))
        try:
            request = json.dumps({"id": 1, "method": "query", "params": {}}) + "\n"
            writer.write(request.encode())
            await writer.drain()
            line = await _read_line(reader)
            response = json.loads(line.decode().strip())
            assert response["id"] == 1
            assert response["result"]["protocol_version"] == 1
            assert response["result"]["sessions"] == []
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_subscribe_and_receive_event(self, server: DaemonServer, runtime_dir: Path, state_mgr: StateManager):
        reader, writer = await asyncio.open_unix_connection(str(runtime_dir / "ccxd.sock"))
        try:
            sub_req = json.dumps({"id": 1, "method": "subscribe", "params": {"events": ["session.*"]}}) + "\n"
            writer.write(sub_req.encode())
            await writer.drain()
            line = await _read_line(reader)
            response = json.loads(line.decode().strip())
            assert "sub_id" in response["result"]

            # Now broadcast an event
            await server.broadcast({"event": "session.added", "data": {"session_id": "test-1"}})

            # Read the broadcasted event
            line = await _read_line(reader)
            event = json.loads(line.decode().strip())
            assert event["event"] == "session.added"
            assert event["data"]["session_id"] == "test-1"
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_hook_dgram_received(self, server: DaemonServer, runtime_dir: Path, state_mgr: StateManager):
        # Send a DGRAM hook payload
        hook_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            payload = json.dumps({
                "event": "SessionStart",
                "payload": {"hook_event_name": "SessionStart", "session_id": "hook-ses", "cwd": "/test"},
            })
            hook_sock.sendto(payload.encode(), str(runtime_dir / "ccxd-hooks.sock"))
            # Give the event loop time to process
            await asyncio.sleep(0.1)
            # Verify state was mutated
            s = state_mgr.get("hook-ses")
            assert s is not None
            assert s.cwd == "/test"
        finally:
            hook_sock.close()

    async def test_sockets_cleaned_on_stop(self, runtime_dir: Path, state_mgr: StateManager):
        srv = DaemonServer(state_mgr, runtime_dir=runtime_dir)
        await srv.start()
        assert (runtime_dir / "ccxd.sock").exists()
        await srv.stop()
        assert not (runtime_dir / "ccxd.sock").exists()
        assert not (runtime_dir / "ccxd-hooks.sock").exists()

    async def test_payload_too_large_closes_connection(self, server: DaemonServer, runtime_dir: Path):
        reader, writer = await asyncio.open_unix_connection(str(runtime_dir / "ccxd.sock"))
        try:
            # Send a line > 1 MB
            huge = "x" * (1024 * 1024 + 1) + "\n"
            writer.write(huge.encode())
            await writer.drain()
            # Connection should be closed or error returned
            try:
                line = await _read_line(reader, timeout=2.0)
            except asyncio.IncompleteReadError:
                line = b""
            if line:
                response = json.loads(line.decode().strip())
                assert response.get("error", {}).get("code") == "payload_too_large"
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError):
                pass

    async def test_subscriber_queue_full_drops_subscriber(self, server: DaemonServer, runtime_dir: Path):
        # Connect and subscribe but don't read events after the response
        reader, writer = await asyncio.open_unix_connection(str(runtime_dir / "ccxd.sock"))
        try:
            sub_req = json.dumps({"id": 1, "method": "subscribe", "params": {"events": ["session.*"]}}) + "\n"
            writer.write(sub_req.encode())
            await writer.drain()
            # Read subscribe response
            await _read_line(reader)
            # Flood broadcasts without reading — should not block
            for i in range(300):
                await server.broadcast({"event": "session.updated", "data": {"session_id": f"s-{i}"}})
            # Server should not hang — the subscriber was dropped
            await asyncio.sleep(0.1)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError):
                pass
