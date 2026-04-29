"""ccxctl monitor — manage the agent-monitor systemd service via SSH."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import typer

from ccx import cli  # lazy CFG access — see CFG access pattern in design
from ccx.ui import die, ok, step, sub

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Manage the Claude Code agent monitor service.",
)

UNIT = "agent-monitor"
PORT = 4820
SENTINEL = "@@@"
DASHBOARD_URL = f"http://localhost:{PORT}"

# Background-tunnel pidfile lives in $XDG_RUNTIME_DIR (cleared at logout — same
# lifetime as the persistent notification id). Falls back to /tmp.
_TUNNEL_PIDFILE = Path(
    os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
) / "ccx-monitor-tunnel.pid"


def _tunnel_pid() -> int | None:
    """Return the pid of the running tunnel if alive, else None (cleans stale file)."""
    try:
        pid = int(_TUNNEL_PIDFILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    try:
        os.kill(pid, 0)
        return pid
    except (ProcessLookupError, PermissionError):
        try:
            _TUNNEL_PIDFILE.unlink()
        except FileNotFoundError:
            pass
        return None


def _ssh_base() -> list[str]:
    """SSH argv prefix matching cli.ssh()'s flags."""
    return [
        "ssh",
        "-i", str(cli.CFG.ssh_key),
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{cli.CFG.ssh_user}@{cli.CFG.hostname}",
    ]


@app.command("status")
def cmd_status() -> None:
    """systemctl is-active + curl /api/health, single SSH round-trip."""
    remote = (
        f"systemctl is-active {UNIT}; "
        f"printf '{SENTINEL}\\n'; "
        f"curl -fsS http://127.0.0.1:{PORT}/api/health"
    )
    # Pass `remote` as a single SSH argument so the remote shell parses the
    # `;`-chained statements as a whole. With ["bash", "-c", remote] ssh would
    # join the trailing argv with spaces and only `bash -c` the first word.
    r = subprocess.run(
        _ssh_base() + [remote],
        capture_output=True, text=True, check=False,
    )
    if r.returncode == 255:
        die(f"ssh failed: {r.stderr.strip() or 'no stderr'}")

    if SENTINEL not in r.stdout:
        die(f"unexpected ssh output (no sentinel): {r.stdout!r}")
    systemd_part, _, health_part = r.stdout.partition(f"\n{SENTINEL}\n")
    systemd_state = systemd_part.strip()

    step(f"systemd: [bold]{systemd_state}[/]")
    if systemd_state != "active":
        die(f"unit {UNIT} is {systemd_state!r}")

    health_raw = health_part.strip()
    if not health_raw:
        die(f"/api/health unreachable on the host")
    try:
        health = json.loads(health_raw)
    except json.JSONDecodeError as exc:
        die(f"could not parse /api/health JSON: {exc}")
    sub(f"health: {health!r}")
    if health.get("status") != "ok":
        die(f"health status {health.get('status')!r} (expected 'ok')")

    ok(f"{UNIT} active and healthy on port {PORT}")


@app.command("tunnel")
def cmd_tunnel(
    print_only: bool = typer.Option(
        False, "--print", "-p", help="Print the ssh command instead of running it.",
    ),
) -> None:
    """Open an SSH tunnel forwarding localhost:4820 → ccx 127.0.0.1:4820 (foreground)."""
    forward = f"{PORT}:127.0.0.1:{PORT}"
    argv = [
        "ssh",
        "-i", str(cli.CFG.ssh_key),
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-N", "-L", forward,
        f"{cli.CFG.ssh_user}@{cli.CFG.hostname}",
    ]
    if print_only:
        # Print without -i/-o noise (just the actionable part the user copies).
        print(f"ssh -L {forward} -N {cli.CFG.ssh_user}@{cli.CFG.hostname}")
        return
    os.execvp("ssh", argv)


@app.command("open")
def cmd_open(
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Start the tunnel only; don't open the browser.",
    ),
) -> None:
    """Start a detached SSH tunnel (idempotent) and open the dashboard in a browser."""
    pid = _tunnel_pid()
    if pid is None:
        forward = f"{PORT}:127.0.0.1:{PORT}"
        argv = [
            "ssh",
            "-i", str(cli.CFG.ssh_key),
            "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ExitOnForwardFailure=yes",
            "-N", "-L", forward,
            f"{cli.CFG.ssh_user}@{cli.CFG.hostname}",
        ]
        # start_new_session detaches from this session so closing the parent
        # shell doesn't kill the tunnel.
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _TUNNEL_PIDFILE.write_text(str(proc.pid))
        # Wait briefly for the listener to accept connections (ssh forks
        # fast, but the channel takes ~200-500ms to open). Cap at 5 s.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                die(f"ssh exited rc={proc.returncode} during tunnel setup")
            try:
                import socket
                with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            die(f"tunnel did not become reachable at localhost:{PORT}")
        step(f"tunnel up (pid {proc.pid})")
    else:
        sub(f"tunnel already running (pid {pid})")

    if no_browser:
        ok(f"tunnel ready — visit {DASHBOARD_URL}")
        return

    opener = shutil.which("xdg-open") or shutil.which("open")
    if not opener:
        ok(f"tunnel ready — visit {DASHBOARD_URL} (no xdg-open found)")
        return
    subprocess.Popen(
        [opener, DASHBOARD_URL],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    ok(f"opened {DASHBOARD_URL}")


@app.command("close")
def cmd_close() -> None:
    """Kill the detached SSH tunnel started by `ccxctl monitor open`."""
    pid = _tunnel_pid()
    if pid is None:
        sub("no tunnel running")
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        _TUNNEL_PIDFILE.unlink()
    except FileNotFoundError:
        pass
    ok(f"tunnel closed (pid {pid})")


@app.command("logs")
def cmd_logs(
    follow: bool = typer.Option(
        False, "--follow", "-f", help="Stream logs (allocates a TTY).",
    ),
) -> None:
    """Tail the agent-monitor systemd journal over SSH."""
    base = [
        "ssh",
        "-i", str(cli.CFG.ssh_key),
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=accept-new",
    ]
    if follow:
        base.append("-t")
        remote = f"journalctl -u {UNIT} -f"
    else:
        # No `-t`: avoids spurious pseudo-TTY allocation; --no-pager prevents
        # less from being invoked on the remote end.
        remote = f"journalctl -u {UNIT} --no-pager"
    argv = base + [f"{cli.CFG.ssh_user}@{cli.CFG.hostname}", remote]
    os.execvp("ssh", argv)
