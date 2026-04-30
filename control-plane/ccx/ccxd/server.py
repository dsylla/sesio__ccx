"""Asyncio socket server — control (STREAM) + hook (DGRAM).

Owns the subscriber registry and broadcast. Each connected client that
calls `subscribe` gets an asyncio.Queue(maxsize=256). Events are pushed
via put_nowait; QueueFull drops the subscriber (logged, not fatal).

Max line length on control socket: 1 MB. Longer lines trigger a
`payload_too_large` error and connection close.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from pathlib import Path
from typing import TYPE_CHECKING

from ccx.ccxd.api import handle_rpc, matches_subscription
from ccx.ccxd.hooks import handle_hook

if TYPE_CHECKING:
    from ccx.ccxd.state import StateManager

log = logging.getLogger(__name__)

_MAX_LINE = 1024 * 1024  # 1 MB
_SUBSCRIBER_QUEUE_SIZE = 256


class DaemonServer:
    """The ccxd network layer — binds sockets, dispatches RPCs, broadcasts."""

    def __init__(self, state_mgr: "StateManager", *, runtime_dir: Path | None = None) -> None:
        self._state_mgr = state_mgr
        self._runtime_dir = runtime_dir or Path(
            os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        )
        self._control_path = self._runtime_dir / "ccxd.sock"
        self._hook_path = self._runtime_dir / "ccxd-hooks.sock"
        self._server: asyncio.Server | None = None
        self._hook_transport: asyncio.DatagramTransport | None = None
        self._subscribers: dict[asyncio.Queue, list[str]] = {}
        self._client_tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        """Bind sockets and start accepting connections."""
        # Clean up stale sockets
        for p in (self._control_path, self._hook_path):
            try:
                p.unlink()
            except FileNotFoundError:
                pass

        # Control socket (STREAM) — bump reader buffer above _MAX_LINE so we
        # can detect oversized lines in our handler and return
        # payload_too_large, rather than readline() raising LimitOverrunError.
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self._control_path),
            limit=_MAX_LINE * 2,
        )
        os.chmod(self._control_path, 0o600)

        # Hook socket (DGRAM)
        loop = asyncio.get_running_loop()
        hook_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        hook_sock.bind(str(self._hook_path))
        os.chmod(self._hook_path, 0o600)
        # Set receive buffer to 1 MB
        hook_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        hook_sock.setblocking(False)

        self._hook_transport, _ = await loop.create_datagram_endpoint(
            lambda: _HookProtocol(self),
            sock=hook_sock,
        )

    async def stop(self) -> None:
        """Drain subscribers, close sockets, unlink files."""
        # Cancel client tasks
        for task in self._client_tasks:
            task.cancel()
        if self._client_tasks:
            await asyncio.gather(*self._client_tasks, return_exceptions=True)
        self._client_tasks.clear()

        # Close control server
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        # Close hook transport
        if self._hook_transport:
            self._hook_transport.close()

        # Unlink sockets
        for p in (self._control_path, self._hook_path):
            try:
                p.unlink()
            except FileNotFoundError:
                pass

        self._subscribers.clear()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handle a single control socket client connection."""
        task = asyncio.current_task()
        if task:
            self._client_tasks.add(task)
        queue: asyncio.Queue | None = None
        sender_task: asyncio.Task | None = None
        try:
            while True:
                try:
                    line = await reader.readline()
                except ValueError:
                    # readline() exceeded its internal buffer limit
                    error = json.dumps({"error": {"code": "payload_too_large",
                                                  "message": "line exceeded 1 MB"}})
                    writer.write((error + "\n").encode())
                    await writer.drain()
                    break
                if not line:
                    break  # client disconnected
                if len(line) > _MAX_LINE:
                    error = json.dumps({"error": {"code": "payload_too_large",
                                                  "message": "line exceeded 1 MB"}})
                    writer.write((error + "\n").encode())
                    await writer.drain()
                    break
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                response = handle_rpc(self._state_mgr, msg)
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()

                # If this was a subscribe, register the queue
                if msg.get("method") == "subscribe" and "result" in response:
                    event_globs = (msg.get("params") or {}).get("events", [])
                    queue = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_SIZE)
                    self._subscribers[queue] = event_globs
                    sender_task = asyncio.create_task(
                        self._send_events(queue, writer)
                    )
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            if queue and queue in self._subscribers:
                del self._subscribers[queue]
            if sender_task:
                sender_task.cancel()
                try:
                    await sender_task
                except asyncio.CancelledError:
                    pass
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ConnectionResetError):
                pass
            if task:
                self._client_tasks.discard(task)

    async def _send_events(self, queue: asyncio.Queue, writer: asyncio.StreamWriter) -> None:
        """Drain the subscriber queue and send events to the client."""
        try:
            while True:
                event = await queue.get()
                line = json.dumps(event) + "\n"
                writer.write(line.encode())
                await writer.drain()
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass

    async def broadcast(self, event: dict) -> None:
        """Push an event to all matching subscribers."""
        event_name = event.get("event", "")
        to_drop: list[asyncio.Queue] = []
        for queue, globs in list(self._subscribers.items()):
            if not matches_subscription(event_name, globs):
                continue
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("subscriber queue full, dropping subscriber")
                to_drop.append(queue)
        for q in to_drop:
            self._subscribers.pop(q, None)

    def handle_hook_datagram(self, data: bytes) -> None:
        """Process a received DGRAM hook payload (called from protocol)."""
        try:
            raw = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.warning("invalid hook datagram (bad JSON)")
            return
        events = handle_hook(self._state_mgr, raw)
        # Schedule broadcasts
        for event in events:
            asyncio.create_task(self.broadcast(event))


class _HookProtocol(asyncio.DatagramProtocol):
    """Datagram protocol for the hook socket."""

    def __init__(self, server: DaemonServer) -> None:
        self._server = server

    def datagram_received(self, data: bytes, addr) -> None:
        self._server.handle_hook_datagram(data)

    def error_received(self, exc: Exception) -> None:
        log.warning("hook socket error: %s", exc)
