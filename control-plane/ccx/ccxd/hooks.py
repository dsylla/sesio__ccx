"""Hook payload parsing and state mutation.

Receives parsed DGRAM payloads from the hook socket, maps them to state
transitions, and returns a list of broadcast events for subscribers.

Hook payload structure (from ccxd-hook script):
  {"event": "<hook_event_name>", "payload": {<full stdin JSON from Claude Code>}}

The payload's `hook_event_name` is authoritative (NOT argv). Supported events:
  PreToolUse, PostToolUse, SessionStart, Stop, SubagentStop,
  Notification, UserPromptSubmit
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ccx.ccxd.state import Session, StateManager

# Notification types that set attention
_BLOCKING_NOTIFICATIONS = {"permission_prompt", "elicitation_dialog"}
_IDLE_NOTIFICATIONS = {"idle_prompt"}
# Noise — explicitly ignored
_NOISE_NOTIFICATIONS = {
    "auth_success", "elicitation_complete", "elicitation_response",
}


def parse_hook_payload(raw: dict) -> dict:
    """Normalize a raw DGRAM message into a flat working dict."""
    event = raw.get("event", "Unknown")
    payload = raw.get("payload") or {}
    # Prefer hook_event_name from the payload (authoritative)
    event = payload.get("hook_event_name", event)
    return {
        "event": event,
        "session_id": payload.get("session_id", ""),
        "cwd": payload.get("cwd", ""),
        "tool_name": payload.get("tool_name", ""),
        "tool_input": payload.get("tool_input") or {},
        "notification_type": payload.get("notification_type", ""),
        "payload": payload,
    }


def handle_hook(mgr: "StateManager", raw: dict) -> list[dict]:
    """Process a hook event, mutate state, return broadcast events.

    Returns a list of event dicts: [{"event": "session.xxx", "data": {...}}]
    """
    parsed = parse_hook_payload(raw)
    event = parsed["event"]
    sid = parsed["session_id"]
    if not sid:
        return []

    now = time.time()
    broadcast: list[dict] = []

    session = mgr.get(sid)

    # SessionStart: seed a stub session if we don't know about it yet
    if event == "SessionStart":
        if session is None:
            from ccx.ccxd.state import Session
            new_session = Session(
                session_id=sid,
                cwd=parsed["cwd"],
                pid=None,
                model=None,
                summary=None,
                tokens_in=0,
                tokens_out=0,
                last_subagent=None,
                subagent_in_flight=None,
                attention=None,
                last_activity_at=now,
                started_at=now,
            )
            mgr.upsert(new_session)
            broadcast.append({
                "event": "session.added",
                "data": new_session.to_dict(),
            })
        else:
            mgr.update_fields(sid, last_activity_at=now)
        return broadcast

    # All other events require an existing session
    if session is None:
        # Seed stub (hook arrived before discovery)
        from ccx.ccxd.state import Session
        session = Session(
            session_id=sid, cwd=parsed["cwd"], pid=None,
            model=None, summary=None, tokens_in=0, tokens_out=0,
            last_subagent=None, subagent_in_flight=None,
            attention=None, last_activity_at=now, started_at=now,
        )
        mgr.upsert(session)
        broadcast.append({"event": "session.added", "data": session.to_dict()})

    # Always bump last_activity_at
    mgr.update_fields(sid, last_activity_at=now)

    if event == "PreToolUse" and parsed["tool_name"] == "Task":
        tool_input = parsed["tool_input"]
        tool_use_id = tool_input.get("tool_use_id", "")
        description = tool_input.get("description", "")
        in_flight = {
            "tool_use_id": tool_use_id,
            "subagent_type": "general-purpose",
            "description": description,
            "dispatched_at": now,
        }
        mgr.update_fields(sid, subagent_in_flight=in_flight, last_subagent=in_flight)
        broadcast.append({
            "event": "session.subagent_start",
            "data": {"session_id": sid, "tool_use_id": tool_use_id,
                     "subagent_type": "general-purpose", "description": description},
        })

    elif event == "PostToolUse" and parsed["tool_name"] == "Task":
        tool_use_id = parsed["tool_input"].get("tool_use_id", "")
        current = mgr.get(sid)
        if current and current.subagent_in_flight:
            if current.subagent_in_flight.get("tool_use_id") == tool_use_id:
                mgr.update_fields(sid, subagent_in_flight=None)
                broadcast.append({
                    "event": "session.subagent_end",
                    "data": {"session_id": sid, "tool_use_id": tool_use_id},
                })

    elif event == "Notification":
        ntype = parsed["notification_type"]
        if ntype in _BLOCKING_NOTIFICATIONS:
            attention = {"kind": "blocking", "since": now}
            mgr.update_fields(sid, attention=attention)
            broadcast.append({
                "event": "session.attention",
                "data": {"session_id": sid, "kind": "blocking"},
            })
        elif ntype in _IDLE_NOTIFICATIONS:
            attention = {"kind": "idle", "since": now}
            mgr.update_fields(sid, attention=attention)
            broadcast.append({
                "event": "session.attention",
                "data": {"session_id": sid, "kind": "idle"},
            })
        # Noise notifications: ignored, no broadcast

    elif event in ("Stop", "UserPromptSubmit"):
        mgr.update_fields(sid, attention=None)

    # SubagentStop: bumps last_activity_at (already done above) but does
    # NOT clear subagent_in_flight — subagents may emit SubagentStop
    # multiple times per Task.

    return broadcast
