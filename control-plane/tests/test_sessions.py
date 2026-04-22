from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest


def test_slug_basic():
    from ccx.sessions import slug
    assert slug("/home/david/Work/sesio/sesio__ccx") == "sesio__ccx"


def test_slug_special_chars():
    from ccx.sessions import slug
    assert slug("/home/david/Work/My Project!") == "my-project-"


def test_slug_lower_collapse_dashes():
    from ccx.sessions import slug
    assert slug("/tmp/A  B  C") == "a-b-c"


def test_encode_project_dir():
    """Claude Code's convention: /home/david/x/y -> -home-david-x-y"""
    from ccx.sessions import encode_project_dir
    assert encode_project_dir("/home/david/Work/sesio/ccx") == "-home-david-Work-sesio-ccx"


def test_parse_jsonl_tokens_today_sums_today(tmp_path: Path):
    from ccx.sessions import parse_jsonl_tokens_today
    today = dt.datetime.now(dt.timezone.utc).isoformat()
    yesterday = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat()
    f = tmp_path / "log.jsonl"
    f.write_text(
        json.dumps({"timestamp": today,     "message": {"usage": {"input_tokens": 100, "output_tokens": 50}}}) + "\n"
        + json.dumps({"timestamp": today,   "message": {"usage": {"input_tokens": 7,   "output_tokens": 3}}})  + "\n"
        + json.dumps({"timestamp": yesterday,"message": {"usage": {"input_tokens": 999, "output_tokens": 999}}}) + "\n"
    )
    assert parse_jsonl_tokens_today([f]) == {"input": 107, "output": 53}


def test_parse_jsonl_tokens_today_handles_missing_keys(tmp_path: Path):
    from ccx.sessions import parse_jsonl_tokens_today
    today = dt.datetime.now(dt.timezone.utc).isoformat()
    f = tmp_path / "log.jsonl"
    f.write_text(
        json.dumps({"timestamp": today}) + "\n"
        + "not json\n"
        + json.dumps({"timestamp": today, "message": {"usage": {"input_tokens": 5, "output_tokens": 2}}}) + "\n"
    )
    assert parse_jsonl_tokens_today([f]) == {"input": 5, "output": 2}


def test_parse_jsonl_tokens_today_no_files():
    from ccx.sessions import parse_jsonl_tokens_today
    assert parse_jsonl_tokens_today([]) == {"input": 0, "output": 0}
