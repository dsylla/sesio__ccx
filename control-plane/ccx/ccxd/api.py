"""RPC method handlers for the ccxd control socket.

Handles: query, subscribe, unsubscribe.
Returns response dicts ready for JSON serialization + newline framing.

Wire protocol: NDJSON over SOCK_STREAM. Each line = one JSON object.
Client -> server: {"id": N, "method": "...", "params": {...}}
Server -> client: {"id": N, "result": {...}} or {"id": N, "error": {...}}
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ccx.ccxd.state import StateManager

PROTOCOL_VERSION = 1

# Valid event globs for subscribe
_VALID_EVENT_GLOBS = {
    "session.*",
    "session.added",
    "session.updated",
    "session.removed",
    "session.attention",
    "session.subagent_start",
    "session.subagent_end",
}

# Track active subscriptions (maps sub_id -> event globs)
_subscriptions: dict[str, list[str]] = {}


def handle_rpc(mgr: "StateManager", msg: dict) -> dict:
    """Dispatch an RPC message to the appropriate handler.

    Returns a response dict with either 'result' or 'error'.
    """
    msg_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params") or {}

    if method == "query":
        return _handle_query(msg_id, mgr)
    elif method == "subscribe":
        return _handle_subscribe(msg_id, params)
    elif method == "unsubscribe":
        return _handle_unsubscribe(msg_id, params)
    elif method == "history.closed_today":
        return _handle_history_closed_today(msg_id, mgr, params)
    elif method == "history.tokens_for_period":
        return _handle_history_tokens_for_period(msg_id, mgr, params)
    else:
        return {
            "id": msg_id,
            "error": {"code": "unknown_method", "message": f"unknown method: {method}"},
        }


def _handle_query(msg_id, mgr: "StateManager") -> dict:
    return {
        "id": msg_id,
        "result": {
            "protocol_version": PROTOCOL_VERSION,
            "sessions": mgr.snapshot(),
        },
    }


def _handle_subscribe(msg_id, params: dict) -> dict:
    event_globs = params.get("events") or []
    # Validate all globs
    invalid = [g for g in event_globs if g not in _VALID_EVENT_GLOBS]
    if invalid:
        return {
            "id": msg_id,
            "error": {
                "code": "unknown_event_glob",
                "message": f"unknown events: {invalid}",
            },
        }
    sub_id = str(uuid.uuid4())
    _subscriptions[sub_id] = event_globs
    return {"id": msg_id, "result": {"sub_id": sub_id}}


def _handle_unsubscribe(msg_id, params: dict) -> dict:
    sub_id = params.get("sub_id", "")
    _subscriptions.pop(sub_id, None)
    return {"id": msg_id, "result": {"ok": True}}


def _handle_history_closed_today(msg_id, mgr: "StateManager", params: dict) -> dict:
    since = params.get("since_epoch")
    if not isinstance(since, (int, float)):
        return {
            "id": msg_id,
            "error": {"code": "invalid_params",
                      "message": "history.closed_today requires numeric 'since_epoch'"},
        }
    sessions = mgr.store.closed_today(float(since))
    return {
        "id": msg_id,
        "result": {"sessions": [s.__dict__ for s in sessions]},
    }


def _handle_history_tokens_for_period(msg_id, mgr: "StateManager", params: dict) -> dict:
    start = params.get("start")
    end = params.get("end")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return {
            "id": msg_id,
            "error": {"code": "invalid_params",
                      "message": "history.tokens_for_period requires numeric 'start' and 'end'"},
        }
    return {"id": msg_id, "result": mgr.store.tokens_for_period(float(start), float(end))}


def matches_subscription(event_name: str, event_globs: list[str]) -> bool:
    """Check if an event name matches any of the subscription's globs."""
    for glob in event_globs:
        if glob == "session.*" and event_name.startswith("session."):
            return True
        if glob == event_name:
            return True
    return False
