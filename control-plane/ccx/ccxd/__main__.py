"""ccxd entrypoint — `python -m ccx.ccxd`.

Wires together: discovery, inotify watcher, server (control + hook sockets),
and the asyncio event loop. Handles SIGTERM/SIGINT for clean shutdown.
Calls sd_notify(READY=1) once sockets are bound.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import socket
import sys
import time
from pathlib import Path

from ccx.ccxd.discovery import discover_sessions
from ccx.ccxd.server import DaemonServer
from ccx.ccxd.state import StateManager
from ccx.ccxd.store import MemoryStore
from ccx.ccxd.tailer import TailerRegistry

log = logging.getLogger("ccxd")

_STALE_SUBAGENT_TIMEOUT = 60.0  # seconds before clearing stale in-flight


def sd_notify(state: str) -> None:
    """Send a systemd notification. No-op if NOTIFY_SOCKET is unset."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.connect(addr)
        sock.sendall(state.encode())
        sock.close()
    except OSError:
        pass


def _select_store(memory: bool):
    if memory:
        return MemoryStore()
    from ccx.ccxd.store import SqliteStore
    data_home = Path(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")))
    return SqliteStore(data_home / "ccxd" / "state.db")


def _create_shutdown_handler(shutdown_event: asyncio.Event):
    """Return a signal callback that sets the shutdown event."""
    def handler():
        log.info("shutdown signal received, draining...")
        shutdown_event.set()
    return handler


async def _subagent_heartbeat(state_mgr: StateManager) -> None:
    """Periodic task: clear stale subagent_in_flight entries (>60s)."""
    while True:
        await asyncio.sleep(15)
        now = time.time()
        for session in state_mgr.all():
            if session.subagent_in_flight:
                dispatched = session.subagent_in_flight.get("dispatched_at", 0)
                if now - dispatched > _STALE_SUBAGENT_TIMEOUT:
                    state_mgr.update_fields(
                        session.session_id, subagent_in_flight=None
                    )
                    log.debug("cleared stale subagent for %s", session.session_id)


async def _run(args: argparse.Namespace) -> None:
    """Main async entry: discover, bind, serve."""
    store = _select_store(args.memory_store)
    state_mgr = StateManager(store)

    # Discovery: seed state from running processes
    if not os.environ.get("CCXD_SKIP_DISCOVERY"):
        log.info("discovering existing claude sessions...")
        for session in discover_sessions():
            state_mgr.upsert(session)
        log.info("discovered %d session(s)", state_mgr.count_active())
    else:
        log.info("discovery skipped (CCXD_SKIP_DISCOVERY set)")

    # Start server
    runtime_dir = Path(
        os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    )
    server = DaemonServer(state_mgr, runtime_dir=runtime_dir)
    await server.start()
    log.info("sockets bound in %s", runtime_dir)

    # Install signal handlers
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()
    shutdown_handler = _create_shutdown_handler(shutdown_event)
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown_handler)

    # Start heartbeat task
    heartbeat = asyncio.create_task(_subagent_heartbeat(state_mgr))

    # Notify systemd we're ready
    sd_notify("READY=1")
    log.info("ccxd ready (pid=%d)", os.getpid())

    # inotify watcher (best-effort — continues without it)
    if not os.environ.get("CCXD_SKIP_DISCOVERY"):
        try:
            from ccx.ccxd.inotify import InotifyWatcher

            projects_dir = Path(os.path.expanduser("~/.claude/projects"))
            if projects_dir.is_dir():
                watcher = InotifyWatcher(projects_dir)
                tailer_registry = TailerRegistry(state_mgr)
                log.info("inotify watching %s", projects_dir)
                # Register fd with event loop
                loop.add_reader(
                    watcher.fd,
                    lambda: _process_inotify(watcher, state_mgr, server, tailer_registry),
                )
            else:
                log.warning("projects dir not found: %s", projects_dir)
        except ImportError:
            log.warning("inotify_simple not available; file watching disabled")

    # Run until signal
    await shutdown_event.wait()

    # Graceful shutdown: drain subscribers, close sockets, notify systemd
    sd_notify("STOPPING=1")
    await asyncio.sleep(2.0)
    await server.stop()
    heartbeat.cancel()
    try:
        await heartbeat
    except asyncio.CancelledError:
        pass


def _process_inotify(watcher, state_mgr, server, tailer_registry) -> None:
    """Callback for inotify fd readable — process events."""
    events = watcher.read_events()
    if not events:
        return

    # Handle overflow
    if watcher.is_overflow(events):
        log.warning("inotify overflow — re-discovering sessions")
        from ccx.ccxd.discovery import discover_sessions
        for session in discover_sessions():
            state_mgr.upsert(session)
        return

    # Handle new subdirs
    watcher.handle_new_subdirs(events)

    # Handle file modifications
    from inotify_simple import flags as iflags
    loop = asyncio.get_event_loop()
    for event in events:
        if event.mask & iflags.MODIFY:
            path = watcher.resolve_event_path(event)
            if not path or path.suffix != ".jsonl" or "subagents" in path.parts:
                continue
            session_id = path.stem
            for ev in tailer_registry.apply(path, session_id):
                loop.create_task(server.broadcast(ev))


def main() -> None:
    parser = argparse.ArgumentParser(description="ccxd — Claude Code session daemon")
    parser.add_argument(
        "--log-level", default=os.environ.get("CCXD_LOG_LEVEL", "info"),
        choices=["debug", "info", "warning", "error"],
    )
    parser.add_argument(
        "--memory-store", action="store_true",
        help="Use in-memory store (V1 behavior; non-persistent).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
