"""Incremental jsonl tailer — byte-offset-based, append-only reads.

Tracks the last-read byte offset per file. On each `read_new()` call,
seeks to the saved offset, reads new bytes, splits into lines, parses
each as JSON. Incomplete trailing lines (no newline yet) are NOT consumed
— the offset stays before them so the next call picks them up once the
write completes.

`parse_deltas(entry)` extracts state-relevant fields from a single parsed
jsonl entry. Returns a dict of fields to update on the Session. Empty dict
means the entry is irrelevant (sidechain, no usage, etc.).
"""
from __future__ import annotations

import json
import time
from pathlib import Path


class JsonlTailer:
    """Track byte offset and yield new parsed entries from a jsonl file."""

    def __init__(self, path: Path, offset: int = 0) -> None:
        self.path = path
        self.offset = offset

    def read_new(self) -> list[dict]:
        """Read new complete lines from the saved offset. Returns parsed dicts."""
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.offset)
                raw = fh.read()
        except (FileNotFoundError, PermissionError):
            return []

        if not raw:
            return []

        entries: list[dict] = []
        consumed = 0
        for line in raw.splitlines(keepends=True):
            if not line.endswith(b"\n"):
                # Trailing incomplete line — don't consume it.
                break
            consumed += len(line)
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entries.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue  # skip corrupt lines, advance past them
        self.offset += consumed
        return entries


def parse_deltas(entry: dict) -> dict:
    """Extract state-relevant fields from a parsed jsonl entry.

    Returns a dict of Session fields to update. Empty dict = irrelevant entry.
    Filters out sidechain entries (subagent billing tracked separately).
    """
    deltas: dict = {}

    # ai-title entries carry the session summary
    if entry.get("type") == "ai-title":
        title = entry.get("aiTitle")
        if title:
            deltas["summary"] = str(title)
        return deltas

    # Skip sidechain entries — those are subagent transcripts
    if entry.get("isSidechain"):
        return deltas

    msg = entry.get("message") or {}

    # Model from assistant messages
    model = msg.get("model")
    if model:
        deltas["model"] = model

    # Token usage
    usage = msg.get("usage") or {}
    input_tokens = (
        int(usage.get("input_tokens") or 0)
        + int(usage.get("cache_creation_input_tokens") or 0)
        + int(usage.get("cache_read_input_tokens") or 0)
    )
    output_tokens = int(usage.get("output_tokens") or 0)
    if input_tokens or output_tokens:
        deltas["tokens_in"] = input_tokens
        deltas["tokens_out"] = output_tokens

    # Task tool_use dispatch detection
    content = msg.get("content") or []
    for block in content:
        if (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("name") == "Task"
        ):
            deltas["last_subagent"] = {
                "tool_use_id": block.get("id", ""),
                "subagent_type": "general-purpose",
                "description": (block.get("input") or {}).get("description", ""),
                "dispatched_at": time.time(),
            }
            break

    return deltas
