"""DGRAM bridge: claude-code hook -> ccxd-hooks.sock.

Invoked by Claude Code as `python -m ccx.ccxd.hook_emitter <EventName>`.
Reads stdin (Claude's hook payload JSON), wraps it in
`{"event": <EventName>, "payload": <stdin>}`, and sendto's the daemon's
DGRAM socket at $XDG_RUNTIME_DIR/ccxd-hooks.sock with a 50ms send
timeout. Any failure (no socket, no daemon, garbage JSON, OS error)
exits 0 silently — Claude Code must never be blocked or surfaced an
error from a hook the user didn't configure themselves.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

_TIMEOUT = 0.05  # 50 ms — spec'd hot-path budget


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python -m ccx.ccxd.hook_emitter <EventName>", file=sys.stderr)
        return 2
    event = sys.argv[1]

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0  # silent drop — claude-code must never surface this

    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    sock_path = runtime_dir / "ccxd-hooks.sock"

    msg = json.dumps({"event": event, "payload": payload}).encode()
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.settimeout(_TIMEOUT)
        s.sendto(msg, str(sock_path))
        s.close()
    except OSError:
        return 0  # silent drop
    return 0


if __name__ == "__main__":
    sys.exit(main())
