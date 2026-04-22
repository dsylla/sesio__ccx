"""ccxctl session — tmux-backed project-anchored claude session manager."""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path


def slug(path: str) -> str:
    """Slugify a filesystem path for use as a tmux window name."""
    import os
    base = os.path.basename(os.path.abspath(path))
    s = base.lower()
    s = re.sub(r"[^a-z0-9_-]", "-", s)
    s = re.sub(r"-+", "-", s)
    return s


def encode_project_dir(path: str) -> str:
    """Claude Code's on-disk convention for per-project dirs: `/` → `-`."""
    import os
    abs_path = os.path.abspath(path)
    # Leading slash becomes a leading dash, other slashes too.
    return abs_path.replace("/", "-")


def parse_jsonl_tokens_today(jsonl_files: list[Path]) -> dict[str, int]:
    """Sum input/output tokens for today (UTC) across the given jsonl files.

    Tolerates non-JSON lines, missing keys, and missing files.
    """
    today = _dt.datetime.now(_dt.timezone.utc).date()
    total_in = 0
    total_out = 0
    for f in jsonl_files:
        try:
            with open(f) as fh:
                for line in fh:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = entry.get("timestamp", "")
                    try:
                        entry_date = _dt.datetime.fromisoformat(
                            ts.replace("Z", "+00:00")
                        ).astimezone(_dt.timezone.utc).date()
                    except (ValueError, TypeError):
                        continue
                    if entry_date != today:
                        continue
                    usage = (entry.get("message") or {}).get("usage") or {}
                    total_in += int(usage.get("input_tokens") or 0)
                    total_out += int(usage.get("output_tokens") or 0)
        except FileNotFoundError:
            continue
    return {"input": total_in, "output": total_out}
