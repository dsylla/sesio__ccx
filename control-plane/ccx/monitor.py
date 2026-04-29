"""ccxctl monitor — manage the agent-monitor systemd service via SSH."""
from __future__ import annotations

import json
import os
import subprocess

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
    r = subprocess.run(
        _ssh_base() + ["bash", "-c", remote],
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
