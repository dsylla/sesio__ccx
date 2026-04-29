"""ccx.ui — styled output + fatal-exit helpers.

Lifted from cli.py so other modules (monitor.py, future ones) can import
from a public surface instead of crossing `_`-prefixed names.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

import typer
from rich.console import Console

console = Console()


def step(msg: str) -> None:
    """Top-level step — `▶ msg`."""
    console.print(f"[blue]▶[/] {msg}")


def sub(msg: str) -> None:
    """Indented detail line — `  · msg`."""
    console.print(f"  [dim]·[/] {msg}")


def ok(msg: str) -> None:
    """Success line — `✓ msg`."""
    console.print(f"[green]✓[/] {msg}")


def die(msg: str) -> "typer.Exit":
    """Log + desktop-notify + exit 1.

    Notification is best-effort: skipped if `notify-send` is missing.
    """
    print(f"error: {msg}", file=sys.stderr, flush=True)
    if shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", "-u", "critical", "ccx error", msg],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    raise typer.Exit(code=1)
