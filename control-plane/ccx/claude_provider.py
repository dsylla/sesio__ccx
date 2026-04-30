"""Switch Claude Code between Anthropic subscription and AWS Bedrock.

Three CLI entry points (`claude-bedrock`, `claude-sub`, `claude-provider`) read
and write `~/.claude/settings.json`'s `env` block. Other env keys are preserved.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

BEDROCK_ENV: dict[str, str] = {
    "CLAUDE_CODE_USE_BEDROCK": "1",
    "AWS_REGION": "eu-west-1",
    "AWS_PROFILE": "sesio__euwest1",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "eu.anthropic.claude-opus-4-6-v1[1m]",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "eu.anthropic.claude-sonnet-4-5-20250929-v1:0[1m]",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
}

BEDROCK_DEFAULT_MODEL = "opus"
SUBSCRIPTION_DEFAULT_MODEL = "claude-opus-4-7"


def _load() -> dict:
    return json.loads(SETTINGS_PATH.read_text())


def _save(data: dict) -> None:
    SETTINGS_PATH.write_text(json.dumps(data, indent=2) + "\n")


def _refuse_if_in_session() -> None:
    if os.environ.get("CLAUDECODE") == "1" and "--force" not in sys.argv:
        sys.stderr.write(
            "refusing: CLAUDECODE=1 detected — running this from inside a live\n"
            "claude session leaks env vars into the running process and wedges it.\n"
            "/exit first, then run from a regular shell. Pass --force to override.\n"
        )
        sys.exit(2)


def bedrock() -> None:
    _refuse_if_in_session()
    data = _load()
    env = data.setdefault("env", {})
    env.update(BEDROCK_ENV)
    data["model"] = BEDROCK_DEFAULT_MODEL
    _save(data)
    print(
        f"Claude Code → AWS Bedrock ({BEDROCK_ENV['AWS_REGION']}, model={BEDROCK_DEFAULT_MODEL})"
    )


def subscription() -> None:
    _refuse_if_in_session()
    data = _load()
    env = data.get("env", {})
    for key in BEDROCK_ENV:
        env.pop(key, None)
    data["env"] = env
    data["model"] = SUBSCRIPTION_DEFAULT_MODEL
    _save(data)
    print(f"Claude Code → Anthropic Subscription (model={SUBSCRIPTION_DEFAULT_MODEL})")


def provider() -> None:
    data = _load()
    env = data.get("env", {})
    model = data.get("model", "?")
    if env.get("CLAUDE_CODE_USE_BEDROCK"):
        region = env.get("AWS_REGION", "?")
        profile = env.get("AWS_PROFILE", "?")
        print(f"Active: AWS Bedrock ({region}, profile={profile}, model={model})")
    else:
        print(f"Active: Anthropic Subscription (model={model})")
