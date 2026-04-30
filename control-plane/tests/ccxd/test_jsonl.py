"""Tests for ccx.ccxd.jsonl — incremental jsonl tailer."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from ccx.ccxd.jsonl import JsonlTailer, parse_deltas


def _entry(type_: str = "assistant", model: str = "claude-sonnet-4-20250514",
           tokens_in: int = 100, tokens_out: int = 50, msg_id: str = "msg_1",
           is_sidechain: bool = False, **extra) -> str:
    """Build a realistic jsonl entry."""
    entry = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "type": type_,
        "isSidechain": is_sidechain,
        "message": {
            "id": msg_id,
            "model": model,
            "usage": {
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        },
        **extra,
    }
    return json.dumps(entry)


def _ai_title_entry(title: str) -> str:
    return json.dumps({
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "type": "ai-title",
        "aiTitle": title,
    })


def _task_tool_use_entry(tool_use_id: str = "tu_1", description: str = "write tests") -> str:
    return json.dumps({
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "type": "assistant",
        "isSidechain": False,
        "message": {
            "id": "msg_tu",
            "content": [
                {"type": "tool_use", "id": tool_use_id, "name": "Task",
                 "input": {"description": description}}
            ],
        },
    })


class TestJsonlTailer:
    def test_initial_read_returns_all_content(self, tmp_path: Path):
        f = tmp_path / "session.jsonl"
        f.write_text(_entry() + "\n" + _entry(msg_id="msg_2") + "\n")
        tailer = JsonlTailer(f)
        lines = tailer.read_new()
        assert len(lines) == 2
        assert tailer.offset == f.stat().st_size

    def test_incremental_read_returns_only_new(self, tmp_path: Path):
        f = tmp_path / "session.jsonl"
        f.write_text(_entry() + "\n")
        tailer = JsonlTailer(f)
        tailer.read_new()
        # Append more
        with open(f, "a") as fh:
            fh.write(_entry(msg_id="msg_2") + "\n")
        lines = tailer.read_new()
        assert len(lines) == 1

    def test_skips_invalid_json(self, tmp_path: Path):
        f = tmp_path / "session.jsonl"
        f.write_text("not json\n" + _entry() + "\n")
        tailer = JsonlTailer(f)
        lines = tailer.read_new()
        assert len(lines) == 1  # only valid entry

    def test_does_not_advance_past_incomplete_line(self, tmp_path: Path):
        f = tmp_path / "session.jsonl"
        f.write_text(_entry() + "\n" + '{"incomplete":')
        tailer = JsonlTailer(f)
        lines = tailer.read_new()
        assert len(lines) == 1
        # Offset should stop before the incomplete line
        assert tailer.offset < f.stat().st_size

    def test_file_missing_returns_empty(self, tmp_path: Path):
        f = tmp_path / "nonexistent.jsonl"
        tailer = JsonlTailer(f)
        assert tailer.read_new() == []

    def test_start_from_offset(self, tmp_path: Path):
        f = tmp_path / "session.jsonl"
        line1 = _entry() + "\n"
        line2 = _entry(msg_id="msg_2") + "\n"
        f.write_text(line1 + line2)
        tailer = JsonlTailer(f, offset=len(line1.encode()))
        lines = tailer.read_new()
        assert len(lines) == 1


class TestParseDeltas:
    def test_extracts_tokens(self):
        entry = json.loads(_entry(tokens_in=200, tokens_out=80))
        deltas = parse_deltas(entry)
        assert deltas.get("tokens_in") == 200
        assert deltas.get("tokens_out") == 80

    def test_extracts_model(self):
        entry = json.loads(_entry(model="claude-opus-4-20250514"))
        deltas = parse_deltas(entry)
        assert deltas.get("model") == "claude-opus-4-20250514"

    def test_skips_sidechain(self):
        entry = json.loads(_entry(is_sidechain=True, tokens_in=999))
        deltas = parse_deltas(entry)
        assert deltas.get("tokens_in") is None

    def test_extracts_ai_title(self):
        entry = json.loads(_ai_title_entry("Implementing ccxd store"))
        deltas = parse_deltas(entry)
        assert deltas.get("summary") == "Implementing ccxd store"

    def test_extracts_task_dispatch(self):
        entry = json.loads(_task_tool_use_entry("tu_abc", "refactor module"))
        deltas = parse_deltas(entry)
        assert deltas["last_subagent"]["tool_use_id"] == "tu_abc"
        assert deltas["last_subagent"]["description"] == "refactor module"
