# `ccxd` — Per-Host Session Daemon — Design

**Date:** 2026-04-30
**Status:** approved (V1 in-memory; V2 SQLite-backed history is forward-compatible)
**Scope:** introduce a long-lived per-host daemon (`ccxd`) that maintains an authoritative in-memory index of running Claude Code sessions, ingests Claude Code hook events in real time, watches `~/.claude/projects/` via inotify, and serves a unix-socket NDJSON API for the TUI and the qtile widget. Replaces SSH-poll loops on the client side. Coexists with the existing `agent-monitor` web dashboard (different role).

## Goal

Every consumer of "claude session state" — `ccxctl monitor tui` (local + ccx), `CcxClaudeStatusWidget` in the qtile bar, and any future client — talks to one local daemon over a unix socket. The daemon is the single source of truth for **what claude processes are alive on this host, what they're working on right now, and how much context/tokens they've used today**. Remote access is just a forwarded unix socket over SSH.

The daemon must be:
- **Smart** — incremental jsonl reads (per-file byte offset), inotify (no polling), hooks for transient state.
- **Fast** — in-memory state; clients see sub-millisecond responses; no SSH-per-poll for remote.
- **Not wasteful** — zero work when nothing changes; one watcher per host shared by N clients; small RAM footprint.

## Non-goals (V1)

- **Persistence.** V1 is in-memory only. Daemon restart = state rebuild from /proc + jsonl bootstrap. SQLite-backed history is V2.
- **Codex agent support.** V1 watches `claude` only. The agent catalog (`ccx.agents`) already supports both, but expanding the daemon to codex is V2 work — codex's transcript shape isn't in `~/.claude/projects/`.
- **Multi-user.** Each user runs their own ccxd in their own `XDG_RUNTIME_DIR`. No system-wide daemon.
- **Replacing agent-monitor.** The Node service stays. Hooks fan out to both (settings.json gets one entry per consumer). Agent-monitor owns the web dashboard; ccxd owns the native clients.
- **Cross-host fanout.** ccxd doesn't proxy to other ccxds. Each TUI client connects to one local socket plus N forwarded sockets, fans out client-side.

## Background

`ccx.sessions.collect_sessions()` walks tmux windows in the shared `ccx` session and links each to a `claude` process. It's the source for `ccxctl session list --json`. Today's TUI (`ccx.monitor_tui.fetch_local()`, just shipped) merges `collect_sessions()` with `_scan_freestanding_claudes()` for any `claude` process not in the tmux session. That works but is poll-only and re-scans the world every 5 s.

Existing on EC2: `agent-monitor` (Node, port 4820, SQLite, web dashboard) ingests Claude Code hooks via `npm run install-hooks` → POST to `127.0.0.1:4820/api/hooks/event`. Reachable from the laptop via `ccxctl monitor open` (SSH tunnel + browser). It exists, it works, it stays. ccxd runs hooks in **parallel** with agent-monitor (settings.json gets one entry per consumer); ccxd does NOT subscribe to agent-monitor's HTTP stream because (a) the DGRAM hot path is ~10 µs/event vs ~50 ms for an HTTP shell-out, and (b) ccxd must keep working when agent-monitor is restarted, missing, or down.

**Existing primitives we reuse, not duplicate.** `ccx.sessions` already has `encode_project_dir` (just bug-fixed: `[/._]` → `-`), `parse_jsonl_tokens_today` (just bug-fixed: cache + dedup), `_project_jsonl_files`, `_process_uptime_seconds`, plus the cwd→jsonl mapping logic. ccxd's `discovery` and `jsonl` modules import these — they don't reimplement them. As part of this spec, the underscore-prefixed helpers in `ccx.sessions` are promoted to public names (`project_jsonl_files`, `process_uptime_seconds`) so ccxd doesn't have to reach into private API.

What's missing for the TUI / widget use case:
1. **Real-time subagent-in-flight signal** (PreToolUse/PostToolUse on the `Task` tool). Polling jsonls can't see this.
2. **Real-time "needs attention"** (Notification hook). Same reason.
3. **Cheap remote read.** SSH-per-poll burns TCP handshakes and journald lines.

A small native daemon shaped around the TUI/widget consumers fills that gap.

## Architecture

```
laptop                                                     ccx EC2
─────────                                                  ──────────
ccxd.service (systemd --user)                              ccxd.service (same)
├── /run/user/1000/ccxd.sock           NDJSON, query+sub   ├── … same …
└── /run/user/1000/ccxd-hooks.sock     DGRAM, ingest       └── …

ccxctl monitor tui ────► local ccxd via /run/user/1000/ccxd.sock
                  └───► remote ccxd via ssh -L /tmp/ccxd-ccx.sock:/run/user/1000/ccxd.sock

Claude Code hooks ─► local ccxd-hooks.sock (DGRAM, fire-and-forget) + agent-monitor's existing handler
inotify(~/.claude/projects/) ─► ccxd's own watcher (no external process)
```

One daemon per host. TUI is now thin: connect → `query state` → `subscribe`, redraw on each pushed event. No polling.

## Internal components (Python module layout)

Lives under `control-plane/ccx/ccxd/` (shares the uv venv with `ccxctl`):

```
control-plane/ccx/ccxd/
├── __main__.py        # `python -m ccx.ccxd` entrypoint; starts asyncio event loop
├── server.py          # asyncio sockets (control + hook); accepts clients, dispatches RPC,
│                      # owns the subscriber registry + broadcast (event bus folded in here)
├── state.py           # in-memory Sessions index; mutators are async to coordinate with broadcasts
├── store.py           # MemoryStore (V1); SqliteStore stub (V2 placeholder, raises NotImplementedError)
├── discovery.py       # /proc scan + cwd-to-session linkage via /proc/<pid>/fd/*
├── jsonl.py           # incremental tail of a jsonl: track byte offset, parse new lines, emit deltas
├── inotify.py         # asyncio wrapper around `inotify_simple` for ~/.claude/projects (dir-by-dir)
├── hooks.py           # parse incoming hook payloads (stdin JSON's hook_event_name), mutate state
└── api.py             # RPC method handlers (query/subscribe/unsubscribe), protocol_version exported
```

Nine modules. Each file ≤ 200 LOC. One responsibility, well-bounded. The split is friendly to per-file unit tests (each module mocks its collaborators). The earlier draft had a separate `events.py`; folding it into `server.py` is the architect's call — the subscriber registry and the connected-clients map are one-to-one, splitting them was ceremony.

## Data flow

**Inbound (state mutation sources):**

1. **inotify** event on `~/.claude/projects/<enc>/<sid>.jsonl` →
   `jsonl.read_new_bytes(path, offset)` returns parsed deltas: `assistant_turn`, `usage_delta`, `model_observed`, `summary`, `task_dispatched` (Task tool_use seen), `tool_use_completed`. Each delta mutates `state` and is broadcast.
2. **`ccxd-hooks.sock` DGRAM** read → parse `{event: "PreToolUse"|"PostToolUse"|"Stop"|"Notification"|"SessionStart"|...}` → mutate state (e.g. `subagent_in_flight = {…}` on `PreToolUse(Task)`; `attention_needed = true` on `Notification`; clear on `Stop` or next `UserPromptSubmit`) → broadcast.
3. **`/proc` walk on startup** → seed initial Sessions for already-running claudes; one-shot full read of each session's jsonl to populate `tokens_*`, `model`, `summary`; remember byte offsets for incremental updates.

**Outbound (clients):**

- Client `query state` → JSON snapshot of all Sessions.
- Client `subscribe events:["session.*"]` → daemon registers the client's stream; future state mutations push `{event, data}` lines to that client. Clients can `unsubscribe` or just close.

## State model

```python
@dataclass
class Session:
    session_id: str          # UUID; from the jsonl filename (top-level <enc>/<sid>.jsonl, NOT subagents/)
    cwd: str                 # canonical cwd of the live process
    pid: int | None          # /proc-linked via /proc/<pid>/fd/*, None if process has exited
    model: str | None        # last assistant.message.model on the MAIN thread (filter isSidechain==false)
    summary: str | None      # latest entry of type "ai-title" → field `aiTitle`; falls back to first
                             # user.message.content truncated to 80 chars. The /summary slash-command does
                             # NOT write a marker entry, so we read ai-title (Claude Code's auto-title)
                             # which IS persisted (~every Nth turn).
    tokens_in: int           # today (UTC): sum of input_tokens + cache_creation_input_tokens
                             # + cache_read_input_tokens, dedup'd by message.id, MAIN-thread only
    tokens_out: int          # today: sum of output_tokens, MAIN-thread only
    last_subagent: dict | None        # {tool_use_id, subagent_type, description, dispatched_at};
                                      # most recent Task tool_use in the transcript
    subagent_in_flight: dict | None   # set by PreToolUse(Task), cleared by PostToolUse(Task) MATCHING
                                      # tool_use_id, or by a 60s heartbeat task if the matching
                                      # PostToolUse never arrives. SubagentStop is a HEARTBEAT that
                                      # bumps last_activity_at but does NOT clear in_flight (subagents
                                      # may emit SubagentStop multiple times per Task).
    attention: dict | None            # {kind: "blocking"|"idle"|None, since: epoch}; set on Notification
                                      # — split by hook payload's notification_type:
                                      #   permission_prompt / elicitation_dialog → blocking
                                      #   idle_prompt → idle
                                      #   auth_success / elicitation_complete / elicitation_response → noise (ignored)
                                      # cleared on UserPromptSubmit or Stop.
    last_activity_at: float           # epoch — last hook or jsonl event
    started_at: float                 # process start_time from /proc/<pid>/stat
```

**Nested subagents.** Claude Code can dispatch a Task whose subagent dispatches another Task. The state model tracks **deepest in-flight only**: the outermost Task fires PreToolUse, the inner Task fires PreToolUse (overwrite `subagent_in_flight`), the inner PostToolUse clears it (because tool_use_id matches the inner), the outer PostToolUse arrives later and is a no-op (tool_use_id doesn't match what's in flight). The TUI shows "deepest active subagent" which is what the user actually wants. This is documented in `state.py`.

State lives behind a `Store` protocol (V1 = `MemoryStore` / dict-backed; V2 = `SqliteStore`). The TUI/widget never touches the store — only reads via the API.

## Wire protocol (NDJSON over `ccxd.sock`)

`SOCK_STREAM`. Each line = one JSON object. UTF-8. Max line length: 1 MB (server-enforced; longer lines closed with `payload_too_large`).

**Client → server (RPC):**
```json
{"id":1,"method":"query","params":{}}
{"id":2,"method":"subscribe","params":{"events":["session.*"]}}
{"id":3,"method":"unsubscribe","params":{"sub_id":"abc"}}
```

**Server → client:**
```json
{"id":1,"result":{"protocol_version":1,"sessions":[{ "session_id":"…", … }]}}
{"id":2,"result":{"sub_id":"abc"}}
{"event":"session.added","data":{"session_id":"…", … }}
{"event":"session.updated","data":{"session_id":"…","fields":{"tokens_in":12345}}}
{"event":"session.attention","data":{"session_id":"…","kind":"blocking"}}
{"event":"session.subagent_start","data":{"session_id":"…","tool_use_id":"…","subagent_type":"general-purpose","description":"…"}}
{"event":"session.subagent_end","data":{"session_id":"…","tool_use_id":"…"}}
{"event":"session.removed","data":{"session_id":"…"}}
```

Errors (server side):
```json
{"id":1,"error":{"code":"unknown_method","message":"…"}}
{"id":2,"error":{"code":"unknown_event_glob","message":"unknown events: ['nope.*']"}}
{"id":3,"error":{"code":"payload_too_large","message":"line exceeded 1 MB"}}
```

**Versioning.** `protocol_version: 1` is included in every `query` result (and any other result that carries a Session payload). Clients pin to a major version; if the server reports a higher version with breaking changes, clients can decide to fall back or error. Adding optional fields to existing events is backward-compatible (clients ignore unknown keys); removing or renaming fields requires `protocol_version: 2` and a parallel `query_v2` method retained alongside `query`.

Unknown event globs in `subscribe` produce `unknown_event_glob`. Unknown methods produce `unknown_method`. Server otherwise tolerates unknown JSON keys silently for forward compat.

## Hook integration

A small Python script `ccxd-hook` installed at `/usr/local/bin/ccxd-hook` (~30 lines). Claude Code passes the hook payload on stdin as a JSON object containing (per the Claude Code hooks contract): `session_id`, `transcript_path`, `cwd`, `hook_event_name`, plus event-specific fields (`tool_name`, `tool_input`, `tool_response`, `notification_type`, etc.). The script reads the whole blob, extracts what's needed, and forwards as a single DGRAM:

```python
#!/usr/bin/env python3
"""ccxd-hook — forward a Claude Code hook event to the local ccxd via DGRAM.

Invoked from ~/.claude/settings.json. Reads the hook payload from stdin
(a JSON object with `hook_event_name` and event-specific fields), wraps
it as {event, payload}, and fire-and-forgets to ccxd-hooks.sock.
Silently no-ops on any failure so Claude Code is never blocked.
"""
import json, os, socket, sys

raw = sys.stdin.read()
try:
    payload = json.loads(raw)
    event = payload.get("hook_event_name", "Unknown")
except (json.JSONDecodeError, ValueError):
    payload = {"raw": raw}
    event = "Unknown"

runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
sock_path = f"{runtime}/ccxd-hooks.sock"

try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    s.settimeout(0.05)  # 50ms cap so a wedged daemon never delays Claude Code
    s.sendto(json.dumps({"event": event, "payload": payload}).encode(), sock_path)
except (OSError, socket.timeout):
    pass  # daemon down, socket missing, kernel buffer full → best-effort
```

`settings.json` gets one entry per hook event we care about (PreToolUse, PostToolUse, SessionStart, Stop, SubagentStop, Notification, UserPromptSubmit), alongside agent-monitor's existing entries — they coexist. **Tool-specific hooks need a `matcher`** so they only fire for the right tool:

```json
"PreToolUse": [
  {"hooks": [{"type":"command","command":"node /opt/agent-monitor/scripts/hook-handler.js PreToolUse"}]},
  {"matcher": "Task", "hooks": [{"type":"command","command":"/usr/local/bin/ccxd-hook"}]}
]
```

Notes on the snippet:
- The argv-passed event-name from the older draft is gone — Claude Code passes the event name in stdin's `hook_event_name`. Keeping argv would create silent drift if the two ever disagree.
- The absolute path `/usr/local/bin/ccxd-hook` is required because Claude Code spawns hooks as non-login shells; `~/.local/bin` and `/usr/local/bin` aren't always on PATH (same gotcha we hit when wiring `ccxctl` for SSH).
- `matcher: "Task"` for PreToolUse/PostToolUse limits the fan-out — we only care about Task tool dispatches for subagent tracking. Stop / SubagentStop / Notification / SessionStart / UserPromptSubmit don't need a matcher; they fire once per event.

**DGRAM cost** ~10 µs per event vs ~50 ms for an HTTP curl shell-out. With many hook fires per second during active Claude Code use, this matters.

**DGRAM send semantics (correction).** On Linux `AF_UNIX SOCK_DGRAM`, a full receive buffer raises `ENOBUFS`, NOT silent drop. The `except OSError` catches it but the spec must be honest: the kernel briefly blocks on full buffer, hence the `settimeout(0.05)` so the worst case for Claude Code is a 50 ms hook stall, not unbounded. Single payloads exceeding `wmem_max` (~212 KB on default Linux) raise `EMSGSIZE` — `ccxd-hook` caps payload at 200 KB by truncating before send (Claude Code hook payloads in practice are 2-20 KB; the cap is defence-in-depth).

**Daemon side.** Bind with `0600` perms (belt-and-suspenders; `/run/user/<uid>` is already 0700). Set `SO_RCVBUF` to 1 MB at startup so a burst of PostToolUse fires from a busy session doesn't overflow at the default ~212 KB. Receive in a loop; parse; on bad JSON, log at WARN with rate-limiting (1/min) to prevent log flooding from a buggy hook.

**Hook-payload → Session linkage.** Every Claude Code hook payload carries `session_id`. The daemon parses this and looks up the Session by sid. If the sid isn't yet known (hook arrived before discovery completes), seed a stub Session (`session_id` + `cwd` from payload) and let the next inotify event enrich it.

## Discovery on startup

1. **Socket cleanup.** `unlink()` `$XDG_RUNTIME_DIR/ccxd.sock` and `$XDG_RUNTIME_DIR/ccxd-hooks.sock` if they exist (ignore ENOENT). After an unclean shutdown the files persist and `bind()` would fail with `EADDRINUSE`.

2. **/proc walk.** For every PID whose `/proc/<pid>/comm` is `claude`, read `/proc/<pid>/cwd`. Then walk `/proc/<pid>/fd/*`, resolve each symlink, find the one pointing at a `~/.claude/projects/<enc>/<sid>.jsonl` (top-level — NOT under a `subagents/` subdirectory). The basename of that file is the live session_id for this PID. **`/proc/<pid>/fd` is the canonical link**; mtime ordering on the directory is unreliable when:
   - Two `claude` instances run in the same cwd (mtime arbitrarily picks one).
   - A session has been idle (no recent turn) — mtime is older than another, recently-exited session's jsonl.

3. **One-shot full jsonl read.** For each discovered session, parse the top-level file to populate `tokens_*` (main thread only — filter `isSidechain == false` per entry; cumulative for today UTC; sums `input_tokens + cache_creation_input_tokens + cache_read_input_tokens` and dedups by `message.id`), `model`, `summary` (latest `type: "ai-title"` entry's `aiTitle`, falling back to first `user.message.content` truncated to 80 chars), `last_subagent` (most recent Task tool_use). Save the file's current byte offset for incremental updates.

4. **inotify subscribe.** Linux inotify is **not recursive** — we walk and add a watch per directory. On startup: walk `~/.claude/projects/` and add an `IN_CREATE | IN_MODIFY | IN_DELETE | IN_MOVED_TO` watch on each subdir (existing project dirs). Also add a watch on `~/.claude/projects/` itself for `IN_CREATE | IN_ISDIR` so new project subdirs (created when the user opens claude in a fresh cwd) get a watch added at runtime. **Subagent transcripts** live under `<sid>/subagents/agent-*.jsonl`; we don't watch those for `tokens_*` (sidechain billings are accounted separately) but we DO read them for "subagent details" if needed (V2). For V1 sidechain files are explicitly ignored.

5. **DGRAM bind.** Open `ccxd-hooks.sock` with `0600`. Set `SO_RCVBUF` to 1 MB. Start receiving.

6. **Control bind.** Open `ccxd.sock` with `0600`. `listen()`. Accept clients.

7. **systemd-notify.** With `Type=notify` (see Deployment), call `sd_notify("READY=1")` once steps 1-6 are done. Optional `WATCHDOG=1` heartbeat from a periodic task.

Steps 2-4 are idempotent — the daemon can be restarted at any time and will rebuild state cleanly. inotify queue overflow (`IN_Q_OVERFLOW`, wd=-1) triggers a re-walk + re-read from each session's **last saved byte offset** (NOT EOF — append-only jsonls mean nothing is lost, only delayed).

## Clients

### `ccxctl monitor tui` (TUI)

Replace today's polling loop with: connect → `query` → `subscribe` → redraw on each pushed event. The render layer (`build_panel` etc.) is unchanged — it still consumes a list of `SessionRow`. Only `run_tui` and the source list shrink.

Falls back to today's polling behavior if `$XDG_RUNTIME_DIR/ccxd.sock` doesn't exist. So the TUI keeps working pre-daemon.

### `ccxctl monitor tui --remote` (or `--source ccx`)

When the user wants the ccx host's view, the TUI:
1. Resolves the remote `XDG_RUNTIME_DIR` once (cached in `~/.config/ccx/hosts.toml` per host) — does NOT hardcode `/run/user/1000`. Default lookup: `ssh david@ccx 'echo "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"'`. Cached so subsequent runs skip the round-trip.
2. Cleans any stale `/tmp/ccxd-<host>.sock` (orphan from a prior `ssh` that died hard).
3. Opens a multiplexed background tunnel:
   ```
   ssh -M -S /tmp/ccxd-<host>-control \
       -L /tmp/ccxd-<host>.sock:<remote_runtime>/ccxd.sock \
       -o ControlPersist=60 \
       -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
       -fN david@<host>
   ```
4. Connects to `/tmp/ccxd-<host>.sock`. Same NDJSON protocol. Reconnects with exponential backoff (1s, 2s, 5s, 10s, 30s) on read errors.
5. On `q`, calls `ssh -S /tmp/ccxd-<host>-control -O exit` to release the tunnel; `unlink()` the local socket.

**OpenSSH floor.** Local-to-remote `unix-socket forwarding` requires OpenSSH ≥ 6.7 on the laptop client. Surfaces a clear error if `ssh -V` reports older.

If ccxd isn't on the remote yet, falls back to `bash -lc 'ccxctl session list --json'` over SSH (today's behavior). **The poll-fallback paths in the TUI are deprecated in V1**: they remain only for the rollout window. Removal target: once the `ccxd` ansible role is applied on both the laptop and the EC2 box and a tagged release confirms operation, `fetch_local()` and the SSH-poll branch in `monitor_tui` are removed in a follow-up commit.

### `CcxClaudeStatusWidget` (qtile bar)

`poll()` becomes a single `query` over the (cached) remote socket. Since the bar only repaints on its own timer (every 30 s), upgrading to push-`subscribe` is overkill for V1 — keep query-per-poll. Future: subscribe and update the bar in real time when "attention_needed" flips.

## Deployment

A new ansible role `ansible/roles/ccxd/`:

- Installs the daemon's Python package (uv tool from the repo) into a system-wide tool dir.
- Installs `/usr/local/bin/ccxd-hook` (the small dispatcher script).
- Drops `~/.config/systemd/user/ccxd.service` for the target user with the unit body below.
- `systemctl --user enable --now ccxd.service`.
- `loginctl enable-linger $USER` so the user instance keeps the daemon up across logout (matters on the EC2 box).
- Adds the `ccxd-hook` entries to `~/.claude/settings.json` (alongside the agent-monitor entries) with the right `matcher: "Task"` for tool-specific events.

**systemd user unit (`~/.config/systemd/user/ccxd.service`):**

```ini
[Unit]
Description=ccxd — Claude Code session daemon
After=default.target

[Service]
Type=notify                              ; daemon calls sd_notify(READY=1) when sockets are bound
ExecStart=/usr/local/bin/uv tool run ccxd
Restart=on-failure
RestartSec=2s
TimeoutStopSec=10s                       ; give time to drain subscribers + unlink sockets
Environment=PYTHONUNBUFFERED=1
Environment=CCXD_LOG_LEVEL=info          ; user-overridable; CLI also accepts --log-level

[Install]
WatchedBy=default.target
```

The role is applied on EC2 via the existing `site.yml` (added to the `roles:` list near the end, after `claude_code` so settings.json exists when we add hook entries) and on the laptop via a new top-level `ansible/laptop.yml` playbook (`hosts: localhost connection: local`) targeting only the `ccxd` role. A `Makefile` target `make ccxd-laptop` wraps the `ansible-playbook` invocation for ergonomics.

## Failure modes

| Scenario | Daemon behavior | TUI | Widget |
|---|---|---|---|
| Daemon not running | n/a | falls back to current poll (deprecated, V1 only) | falls back to current SSH poll |
| Hook socket full | `sendto` blocks ≤ 50ms (settimeout) then drops; never blocks Claude Code | inotify still picks up content | n/a |
| Hook socket missing | `sendto` raises ENOENT, caught silently | inotify still picks up content | n/a |
| jsonl line corrupted | line skipped; offset advanced past it; WARN-logged (1/min rate-limited) | continues from next valid line | n/a |
| inotify queue overflow (`IN_Q_OVERFLOW`) | re-walk dir, **re-read from each session's saved offset** (NOT EOF — append-only files lose nothing) | brief visual hiccup | n/a |
| SSH tunnel drops (remote) | n/a | reconnect with exponential backoff (1s, 2s, 5s, 10s, 30s); orphan local socket cleaned on next try | retry next poll |
| `ccxd-hook` script absent | hooks fire `command not found`, Claude Code logs and continues | n/a (degrades to inotify-only) | n/a |
| Process dies mid-jsonl-read | parse fails on truncated line; offset NOT advanced past it; retry on next event | retry next inotify event | n/a |
| Stale socket file from unclean shutdown | startup `unlink()` removes it before `bind()` (no EADDRINUSE) | n/a | n/a |
| Slow/dead subscriber | per-subscriber `asyncio.Queue(maxsize=256)`; `put_nowait` raises QueueFull → drop subscriber + log | client reconnects on next read | n/a |
| Subagent in-flight not cleared (PostToolUse dropped) | 60s heartbeat task clears stale `subagent_in_flight` | next render shows cleared state | n/a |
| Hook payload > 200 KB | `ccxd-hook` truncates before send; daemon logs WARN | n/a | n/a |
| Hook payload > 1 MB on control socket | server closes connection with `payload_too_large` error | TUI reconnects | n/a |

The daemon is opinionated about silence: any local error path that would block Claude Code or fill the user's terminal with noise gets logged at WARN to `journalctl --user -u ccxd` and the affected work is skipped/retried, never raised.

**Lifecycle / signal handling.** Daemon installs `loop.add_signal_handler(SIGTERM, _shutdown)` and `SIGINT`. `_shutdown` cancels the accept tasks, flushes pending broadcasts to subscribers (with a 2s budget), closes listening sockets, `unlink()`s both socket files, calls `sd_notify("STOPPING=1")`, and exits 0. systemd's `TimeoutStopSec=10s` is the hard ceiling. SIGKILL leaves orphan sockets which the next startup cleans up (Discovery step 1).

## Testing strategy

- **`state.py` and `jsonl.py`** are pure Python — full unit tests with `tmp_path`-built jsonls. The `_scan_freestanding_claudes` test pattern (fake `/proc` under `tmp_path`) generalises here for `discovery.py`.
- **`server.py`** — asyncio test harness; spin daemon as a fixture in a `tmp_path`-rooted XDG_RUNTIME_DIR; connect from the test with raw `socket`; assert RPC round-trips and event delivery.
- **`hooks.py`** — send fake DGRAMs to a test daemon; assert state transitions on the in-memory store.
- **`events.py`** — register N subscribers, broadcast, assert all receive in order, dropped subscribers don't block others.
- **`store.py` `MemoryStore`** — basic CRUD; `SqliteStore` placeholder must raise `NotImplementedError` clearly.
- **End-to-end** — spawn ccxd as a subprocess; fire a synthetic hook via `ccxd-hook` (also under test); query state; verify. Also verify the daemon starts and stops cleanly (no orphan sockets).

Coverage target: ≥ 85% line coverage on `ccx/ccxd/` (measured by `pytest --cov=ccx.ccxd`); the daemon's startup path is covered end-to-end; every public function in every module has at least one direct test.

## Storage abstraction (V1 → V2 forward path)

```python
from typing import Protocol

class Store(Protocol):
    def upsert(self, session: Session) -> None: ...
    def remove(self, session_id: str) -> None: ...
    def get(self, session_id: str) -> Session | None: ...
    def all(self) -> list[Session]: ...
    def count_active(self) -> int: ...                                  # widget fast-path
    def closed_today(self, since_epoch: float) -> list[Session]: ...    # for V2 history; V1 returns []
    def tokens_for_period(self, start: float, end: float) -> dict: ... # V2 reporting; V1 returns {}
```

V1: `MemoryStore` is a `dict[str, Session]`. `count_active` returns `len(...)`. `closed_today` and `tokens_for_period` return empty/zero. The daemon constructor takes `Store` as a dependency. Single line in `__main__.py` swaps stores.

V2: `SqliteStore` writes upserts/removes through to SQLite at `$XDG_DATA_HOME/ccxd/state.db`. Schema migration runs at startup. Reads still satisfied from a hot in-memory cache; only `closed_today()` and `tokens_for_period()` (history queries) hit disk.

No code outside `store.py` cares about the storage layer.

## Open questions / future work

- **Codex agent support.** Codex transcripts don't live under `~/.claude/projects/`. Either teach the daemon a second discovery source (codex's own state dir, `ccx.agents` already has the spec), or run a second daemon (`codexd`). V2 decision.
- **Subagent-in-flight cleanup.** A `PreToolUse(Task)` not followed by `PostToolUse` within ~60s should be cleared by a heartbeat task to avoid stale "subagent running" indicators if a hook is dropped. (Specced in the state model as V1 requirement.)
- **Push to widget.** The qtile widget could `subscribe` and react to `session.attention` instantly, e.g. flashing the bar. Worth the wire-up cost? Decide once the V1 widget query path is in place.
- **`/api/sessions` HTTP shim on agent-monitor.** Could give the laptop ccxd a unified read path for the EC2 host without SSH-forwarded sockets. Adds coupling to agent-monitor; not pursuing for V1.
- **History query API.** `closed_today`, `tokens_for_period(start, end)`, etc. Ships with V2.
- **Multiple TUI clients on one host.** Per-subscriber `asyncio.Queue(maxsize=256)` pattern; `put_nowait` → QueueFull → drop subscriber + log. Prevents a dead/slow client from stalling the broadcast.
- **Rate-limit data (5h/7d windows).** Hooks don't carry this. Options: (a) ccxd inotify-watches `~/.cache/claude_status/state.json` (same mechanism) and includes `rate_limits` in `query` result; or (b) TUI reads the file client-side (today's behavior). V1 picks (b) — rate-limit data stays client-side in the TUI. Daemon doesn't own it yet.
- **`ccxctl monitor daemon status/logs/restart`** subcommands. V1 is pure-systemd (`systemctl --user status/logs/restart ccxd`). Adding ccxctl wrappers for ergonomics is V1+ — nice-to-have after the daemon is stable.
- **≥4 hosts.** The per-client N-socket fan-out model works for 2-3 hosts. Beyond that, consider a meta-aggregator (`ccxd-hub`) so the TUI connects to one local socket that fans out.
- **Poll fallback removal.** Once ccxd is ansible-deployed on both hosts and a tagged release confirms operation, remove `fetch_local()` and the SSH-poll branch from `monitor_tui`. Removal target: one release after ccxd is stable.
