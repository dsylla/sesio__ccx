"""Catalog of supported coding agents (Claude, Codex, ...)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    name: str
    command: str
    process_names: tuple[str, ...]
    config_root: str
    usage_source: str | None = None


DEFAULT_AGENT = "claude"

AGENTS: dict[str, AgentSpec] = {
    "claude": AgentSpec(
        name="claude",
        command="claude",
        process_names=("claude",),
        config_root="~/.claude",
        usage_source="~/.claude/projects",
    ),
    "codex": AgentSpec(
        name="codex",
        command="codex",
        process_names=("codex",),
        config_root="~/.codex",
        usage_source=None,
    ),
}


def get_agent(name: str) -> AgentSpec:
    try:
        return AGENTS[name]
    except KeyError as exc:
        choices = ", ".join(sorted(AGENTS))
        raise ValueError(f"unknown agent {name!r}; expected one of: {choices}") from exc


def window_name(agent_name: str, slug: str) -> str:
    get_agent(agent_name)
    return f"{agent_name}:{slug}"


def split_window_name(name: str) -> tuple[str, str]:
    if ":" not in name:
        return DEFAULT_AGENT, name
    agent_name, slug = name.split(":", 1)
    get_agent(agent_name)
    return agent_name, slug
