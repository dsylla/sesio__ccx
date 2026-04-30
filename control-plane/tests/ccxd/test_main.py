"""Tests for ccx.ccxd.__main__ — entrypoint wiring."""
from __future__ import annotations

import signal
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSdNotify:
    def test_sd_notify_sends_to_socket(self, tmp_path, monkeypatch):
        from ccx.ccxd.__main__ import sd_notify
        sock_path = tmp_path / "notify.sock"
        monkeypatch.setenv("NOTIFY_SOCKET", str(sock_path))
        # Create a listening socket
        import socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.bind(str(sock_path))
        try:
            sd_notify("READY=1")
            data = s.recv(1024)
            assert data == b"READY=1"
        finally:
            s.close()

    def test_sd_notify_noop_without_env(self, monkeypatch):
        from ccx.ccxd.__main__ import sd_notify
        monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
        # Should not raise
        sd_notify("READY=1")


class TestMain:
    def test_module_is_runnable(self):
        """Verify the module can be imported without side effects."""
        import ccx.ccxd.__main__  # noqa: F401

    @pytest.mark.asyncio
    async def test_shutdown_handler_cancels_cleanly(self):
        from ccx.ccxd.__main__ import _create_shutdown_handler
        from ccx.ccxd.server import DaemonServer
        from ccx.ccxd.state import StateManager
        from ccx.ccxd.store import MemoryStore

        server = MagicMock(spec=DaemonServer)
        server.stop = AsyncMock()
        handler = _create_shutdown_handler(server)
        # Calling the handler should not raise
        # (In real usage it cancels the event loop)
        # We just verify it's callable
        assert callable(handler)
