"""Tests for ccx.ccxd.__main__ — entrypoint wiring."""
from __future__ import annotations

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
    async def test_shutdown_handler_sets_event(self):
        import asyncio
        from ccx.ccxd.__main__ import _create_shutdown_handler

        shutdown_event = asyncio.Event()
        handler = _create_shutdown_handler(shutdown_event)
        assert callable(handler)
        assert not shutdown_event.is_set()
        handler()
        assert shutdown_event.is_set()


def test_main_default_picks_sqlite_store(monkeypatch, tmp_path):
    """Without --memory-store, __main__ should pick SqliteStore."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from ccx.ccxd.__main__ import _select_store
    store = _select_store(memory=False)
    from ccx.ccxd.store import SqliteStore
    assert isinstance(store, SqliteStore)
    store.close()


def test_main_memory_store_flag(monkeypatch):
    from ccx.ccxd.__main__ import _select_store
    store = _select_store(memory=True)
    from ccx.ccxd.store import MemoryStore
    assert isinstance(store, MemoryStore)
