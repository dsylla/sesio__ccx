# Agent Monitor Integration — Design

**Date:** 2026-04-28
**Status:** approved
**Scope:** add `hoangsonww/Claude-Code-Agent-Monitor` v1.1.0 to the ccx EC2 host as a supervised systemd service, wire it to Claude Code hooks, and manage its lifecycle through `ccxctl`.

## Goal

On the ccx EC2 host: run the agent-monitor dashboard as a systemd-supervised Node service bound to the local host, with Claude Code hooks pre-wired so every Claude session forwards events to it. Access from the laptop via SSH tunnel only — no public ingress. Lifecycle (status, logs, tunnel) is managed through a new `ccxctl monitor` subcommand group.

## Non-goals

- Multi-user support beyond the ccx user (`david`).
- Migrating any existing local Claude Code session DB into the dashboard.
- Public TLS / HTTPS ingress (Caddy, ACME, public DNS). Deferred.
- Bundling the second tool the user mentioned (`onikan27/claude-code-monitor`) — incompatible (macOS/AppleScript-only, headless ccx host cannot run it; user's laptop is also Linux).

## Background

ccx is an Ansible-provisioned EC2 host that runs Claude Code + Codex agents inside tmux windows in a shared session, managed by `ccxctl` (Python/Typer CLI in `control-plane/ccx/`). Existing agent-related Ansible roles: `claude_code`, `claude_plugins`, `codex_code`. Site playbook: `ansible/site.yml`.

The agent-monitor (`hoangsonww/Claude-Code-Agent-Monitor`, MIT, v1.1.0) is a Node app that:

- Listens on `0.0.0.0:4820` (default) — `server.listen(port)` with no host argument; no `DASHBOARD_HOST` env var. We accept the wildcard bind because the EC2 security group blocks 4820 anyway.
- In `NODE_ENV=production`, also serves the prebuilt React client from `client/dist`. Without that build, `localhost:4820` is API-only. (Note: the server itself defaults `NODE_ENV=production` if unset, so the explicit env in the unit file is belt-and-suspenders.)
- Provides `npm run install-hooks` to add hook entries to `~/.claude/settings.json` that exec `node "/opt/agent-monitor/scripts/hook-handler.js" <EventName>`. The handler reads JSON from stdin and POSTs to `127.0.0.1:<CLAUDE_DASHBOARD_PORT or 4820>/api/hooks/event`. Designed to fail silently (exits 0) on connect-refused so Claude Code is never blocked.
- The server *also* invokes `installHooks(silent=true)` on every startup, so the Ansible install-hooks task is only meaningful as bootstrap (so hooks exist before the first restart) and as a verifiable signal during provisioning.
- Health endpoint: `GET /api/health` → `{"status":"ok","timestamp":"..."}`.
- SQLite DB lives at `/opt/agent-monitor/data/dashboard.db` (overridable via `DASHBOARD_DB_PATH`). The directory is gitignored so it survives `git pull` and `npm install`. A `git clean -fdx` or role uninstall would delete it — durability is "best effort, OK to lose"; not promoted to `/var/lib` for this reason (the data is monitoring exhaust, not user state).

## Architecture

```
laptop                                EC2 ccx host
─────────                             ────────────────────────────────
ccxctl monitor tunnel ──ssh -L───→ 127.0.0.1:4820  ◄── agent-monitor.service
                                                       (npm start, NODE_ENV=production)
                                                       │
browser → http://localhost:4820                        │  ingests via POST /api/hooks/event
                                                       │
                                       claude (hook) ──┘
                                       (~/.claude/settings.json command type)
```

- Source: `/opt/agent-monitor`, owned by `david`. Cloned by Ansible, pinned to `v1.1.0`. All Ansible tasks in the role run with `become: true` + `become_user: "{{ target_user }}"` so the tree stays user-owned and `npm` operations don't run as root.
- Service: `agent-monitor.service`, `User=david`, `Type=simple`. ExecStart pattern matches `claude_plugins`: `Environment=PATH={{ target_home }}/.asdf/shims:/usr/local/bin:/usr/bin:/bin` and `ExecStart={{ target_home }}/.asdf/shims/npm start`. Plus `Environment=NODE_ENV=production DASHBOARD_PORT=4820`, `Restart=on-failure`, `RestartSec=5`, `WorkingDirectory={{ install_dir }}`.
- Logs: journald, no separate logfile.
- DB: SQLite at `{{ install_dir }}/data/dashboard.db` (upstream default). Not relocated; see DB note above.

## Components

### 1. Ansible role: `ansible/roles/agent_monitor/`

```
agent_monitor/
├── defaults/main.yml
├── handlers/main.yml
├── tasks/main.yml
└── templates/agent-monitor.service.j2
```

**`defaults/main.yml`:**

```yaml
agent_monitor_version: v1.1.0
agent_monitor_repo: https://github.com/hoangsonww/Claude-Code-Agent-Monitor.git
agent_monitor_install_dir: /opt/agent-monitor
agent_monitor_port: 4820
```

**`tasks/main.yml`** flow (all tasks `become: true` + `become_user: "{{ target_user }}"` unless noted; all `shell:` tasks `args: executable: /bin/bash`):

1. `ansible.builtin.file` (root, no become_user): ensure `{{ agent_monitor_install_dir }}` exists, owner `{{ target_user }}`, group `{{ target_user }}`, mode `0755`.
2. `ansible.builtin.git`: clone `{{ agent_monitor_repo }}` to install dir, `version: "{{ agent_monitor_version }}"`, `update: yes`. `register: _repo`. Notifies the `restart agent-monitor` handler when the SHA changes.
3. `ansible.builtin.shell` `source ~/.asdf/asdf.sh && npm run setup` (asdf sourced). Run when **either** `node_modules` is missing **or** `_repo.changed`:
   ```yaml
   - ansible.builtin.stat: path={{ install_dir }}/node_modules
     register: _node_modules
   - ansible.builtin.shell: source ~/.asdf/asdf.sh && npm run setup
     args: { chdir: "{{ install_dir }}", executable: /bin/bash }
     when: not _node_modules.stat.exists or _repo.changed
   ```
4. Same shape for `npm run build` (gated on `client/dist/index.html` existence OR `_repo.changed`).
5. `ansible.builtin.template`: render `agent-monitor.service.j2` to `/etc/systemd/system/agent-monitor.service`, mode `0644` (root, no become_user). Notifies `daemon-reload` and `restart agent-monitor`.
6. `ansible.builtin.systemd` (root, no become_user): `name=agent-monitor enabled=yes state=started daemon_reload=yes`.
7. `ansible.builtin.shell` `source ~/.asdf/asdf.sh && npm run install-hooks` (chdir install_dir, asdf sourced). **Bootstrap-only**: the server itself re-runs install-hooks on every startup (`installHooks(true)` in `server/index.js`), so this Ansible task only matters before the service has ever started. Use `changed_when: false` and don't try to parse stdout — the script always rewrites the same bytes after the first run, and we have a separate verify task that asserts the hook entries are present.

**`templates/agent-monitor.service.j2`:**

```ini
[Unit]
Description=Claude Code Agent Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={{ target_user }}
Group={{ target_user }}
WorkingDirectory={{ agent_monitor_install_dir }}
Environment=PATH={{ target_home }}/.asdf/shims:/usr/local/bin:/usr/bin:/bin
Environment=NODE_ENV=production
Environment=DASHBOARD_PORT={{ agent_monitor_port }}
ExecStart={{ target_home }}/.asdf/shims/npm start
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

(PATH-via-env + direct shim invocation matches the existing `claude_plugins` role pattern; cleaner than re-sourcing `asdf.sh` from a login shell.)

**`handlers/main.yml`:** two focused handlers — (a) `daemon-reload`, (b) `restart agent-monitor` (listen-on `daemon-reload` so the daemon picks up unit changes before restart).

### 2. Site wiring

Insert `agent_monitor` **after `claude_plugins`** in `ansible/site.yml` so install-hooks is the last writer to `~/.claude/settings.json` and (defensively) `~/.claude.json`:

```yaml
roles:
  - ...
  - claude_code
  - codex_code
  - codex_config
  - codex_mcp
  - claude_plugins
  - agent_monitor   # ← here, after claude_plugins
  - rtk
  - ...
```

Order rationale: `claude_code` provisions Node + the `claude` binary; `claude_plugins` installs MCP servers under user scope (touches both `~/.claude/settings.json` and `~/.claude.json`); `agent_monitor` then adds hook entries to `~/.claude/settings.json`. Although the JSON keys touched are non-overlapping (`hooks` vs `mcpServers`), running last avoids any read-modify-write race on the same file across roles.

### 3. ccxctl subcommand: `ccxctl monitor`

**Refactor first — extract UI helpers.** Before adding `monitor.py`, move `console`, `_step`, `_sub`, `_ok`, `die` from `ccx/cli.py` into a new `ccx/ui.py` (renamed without underscores: `step`, `sub`, `ok`, `die`, `console`). Have `cli.py` re-export them from `ccx.ui` so existing callers don't break. `monitor.py` then imports cleanly from `ccx.ui`. This avoids cross-module private-name imports (the existing `from ccx.cli import pick_menu` precedent uses a public name; we shouldn't extend the `_`-prefixed cross-module pattern).

**Module wiring.** New module `control-plane/ccx/monitor.py`. Registered in `cli.py` immediately after the existing `_sessions_app` block:

```python
from ccx.monitor import app as _monitor_app
app.add_typer(_monitor_app, name="monitor", help="Manage the Claude Code agent monitor service.")
```

**`CFG` access pattern.** `monitor.py` imports `from ccx import cli` and references `cli.CFG.hostname` / `cli.CFG.ssh_user` / `cli.CFG.ssh_key` lazily inside command bodies (not at module top). Tests then use `monkeypatch.setattr("ccx.cli.CFG", Config(...))` — same idiom as `test_sessions.py` uses for `_PROC` and `_NOW_FN`. **No `importlib.reload`.**

**Surface:**

| Command | Behaviour |
|---|---|
| `ccxctl monitor status` | Single combined SSH call: `bash -c 'systemctl is-active agent-monitor; printf "@@@\n"; curl -fsS http://127.0.0.1:4820/api/health'`. Split on the sentinel, parse health JSON. Print styled output via `step`/`sub`/`ok`/`die` (from `ccx.ui`). Failure cases — exits non-zero on each: (a) SSH itself fails (rc=255 → "ssh failed: <stderr>"); (b) systemctl `inactive`/`failed`/`unknown`; (c) `/api/health` rc≠0 (unreachable); (d) health JSON unparseable; (e) `health.status != "ok"` (e.g. `"degraded"`, `"starting"` — surface the actual value). |
| `ccxctl monitor tunnel` | `os.execvp("ssh", [..., "-N", "-L", "4820:127.0.0.1:4820", f"{ssh_user}@{hostname}"])` — opens the tunnel in the foreground and blocks. SIGINT propagates to ssh and exits cleanly (no Python-side handling needed; matches `_ssh_raw()`). No background mode by design. |
| `ccxctl monitor tunnel --print` / `-p` | Print the equivalent ssh command and exit 0; do not exec. |
| `ccxctl monitor logs` | `os.execvp("ssh", [..., host, "journalctl -u agent-monitor --no-pager"])`. **No `-t`** for the non-follow path — pseudo-TTY is unnecessary and `--no-pager` prevents `less` from being invoked over SSH. |
| `ccxctl monitor logs --follow` / `-f` | `os.execvp("ssh", [..., "-t", host, "journalctl -u agent-monitor -f"])` — TTY *is* needed here so the user's Ctrl-C kills the remote `journalctl -f` cleanly. |

All commands re-use `cli.CFG.ssh_user`, `cli.CFG.ssh_key`, `cli.CFG.hostname`. No new config surface. SSH options match the existing `ssh()` and `_ssh_exec()`: `-i $key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new`.

### 4. Verification (`ansible/roles/verify`)

Append three checks (and corresponding `provision-ok` marker lines):

```yaml
- name: Verify agent-monitor service is active
  ansible.builtin.command: systemctl is-active agent-monitor
  register: _v_agent_monitor
  changed_when: false

- name: Verify /api/health responds (retry — listener may take a few seconds after systemd state=started)
  ansible.builtin.uri:
    url: http://127.0.0.1:4820/api/health
    return_content: yes
  register: _v_agent_monitor_health
  retries: 10
  delay: 2
  until: _v_agent_monitor_health.status == 200
  changed_when: false

- name: Verify Node version is >= 22 (better-sqlite3 falls back to node:sqlite)
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.shell: |
    source "{{ target_home }}/.asdf/asdf.sh"
    node -e 'process.exit(parseInt(process.versions.node.split(".")[0],10) >= 22 ? 0 : 1)'
  args: { executable: /bin/bash }
  register: _v_node_22
  changed_when: false

- name: Verify Claude Code hooks reference hook-handler.js
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.shell: |
    grep -q hook-handler.js {{ target_home }}/.claude/settings.json
  changed_when: false
```

Marker additions (in `provision-ok`):

```yaml
agent-monitor: {{ _v_agent_monitor.stdout | trim }}
agent-health:  {{ (_v_agent_monitor_health.json.status == 'ok') | ternary('ok', 'failed') }}
agent-hooks:   ok   # task fails the playbook if the grep doesn't match
```

### 5. Tests — `control-plane/tests/test_monitor.py`

Style: mirror `test_sessions.py`. Mock `subprocess.run`, patch `os.execvp`, drive the CLI with `typer.testing.CliRunner`. No real SSH or systemd.

| Test | Pins down |
|---|---|
| `test_monitor_help_lists_subcommands` | `CliRunner().invoke(app, ["monitor","--help"])` → exit 0, output mentions `status`, `tunnel`, `logs`. Catches registration breakage. |
| `test_status_active_and_healthy` | combined ssh stdout has `active\n@@@\n{"status":"ok",...}`, exit 0, both lines in styled stdout. |
| `test_status_systemd_inactive_exits_nonzero` | stdout starts with `inactive\n@@@\n…`; exit != 0; error references unit name. |
| `test_status_health_endpoint_unreachable` | systemctl `active`, curl rc≠0 → no JSON after sentinel; exit != 0; error mentions `/api/health`. |
| `test_status_invalid_health_json` | JSON segment is `not-json`; exit != 0; message mentions parse failure. |
| `test_status_health_status_not_ok` | health JSON `{"status":"degraded"}`; exit != 0; message surfaces `"degraded"`. |
| `test_status_ssh_failure_rc_255` | `subprocess.run` returns rc=255 with stderr `Connection refused`; exit != 0; message starts "ssh failed:". |
| `test_tunnel_default_execs_ssh_with_L_flag` | Patch `os.execvp`; argv contains `-L`, `4820:127.0.0.1:4820`, `-N`, configured `david@ccx.dsylla.sesio.io`. |
| `test_tunnel_print_outputs_command_no_exec` | `--print` → exit 0, stdout contains `ssh -L 4820:127.0.0.1:4820`, `os.execvp` NOT called. |
| `test_logs_no_follow_omits_t_flag_and_uses_no_pager` | argv has `journalctl -u agent-monitor --no-pager` and no `-t`. |
| `test_logs_follow_adds_f_and_t_flags` | argv contains `-t` and `journalctl -u agent-monitor -f`. |
| `test_status_uses_configured_host_and_user` | `monkeypatch.setattr("ccx.cli.CFG", Config(hostname=..., ssh_user=...))`; assert SSH args use those values. **No `importlib.reload`**. |

Helpers: lift `_mock_run` from `test_sessions.py` to `tests/conftest.py` (deduplicate; both files use it). Optional follow-up — only do this if it's a same-PR change rather than churning unrelated tests.

### 6. Docs

- **Top-level `README.md`** — add an "Agent Monitor" section: what it is, how to access via tunnel, the three `ccxctl monitor` commands, how to disable.
- **`control-plane/README.md`** — extend the subcommand table with the three new commands.
- **`docs/agent-monitor.md`** (new) — version-pinning policy: where the version lives, how to bump (edit `agent_monitor_version` and re-run the playbook), how to verify (`ccxctl monitor status`), how to roll back.

## Smoke checklist (added to top-level README)

- `systemctl is-active agent-monitor` → `active`
- On host: `curl http://127.0.0.1:4820/api/health` → `{"status":"ok",...}`
- From laptop: `ccxctl monitor tunnel` → opens; visit `http://localhost:4820` → React UI loads.
- Trigger any Claude Code event → it appears in the dashboard.
- `ccxctl monitor logs -f` → streams journald output.
- `systemctl restart agent-monitor` → still healthy.

## Known assumptions / explicit non-issues

- **Hook command PATH dependency.** Claude Code spawns hook commands inheriting its own env. Because `claude` itself is launched via asdf node, hook commands have asdf shims on PATH automatically. We rely on that. If a future change runs `claude` outside an asdf shell, hooks would silently break.
- **Wildcard bind on 4820.** Server binds `0.0.0.0:4820` with no host-override mechanism upstream. We do not patch — the EC2 SG never opens 4820, so it's unreachable externally. Confirmed acceptable trade-off rather than maintaining an upstream patch.
- **Hooks fail silently if the service is down.** `hook-handler.js` exits 0 on connect-refused. Disabling the role + service therefore degrades cleanly without breaking Claude Code sessions.
- **Server auto-installs hooks on every start.** `installHooks(true)` runs at process startup, so `~/.claude/settings.json` is rewritten on each `systemctl restart`. The Ansible install-hooks task is bootstrap-only.
- **Server imports `~/.claude/projects/` history on first start** and runs a periodic stale-session sweep. First boot may take noticeably longer if the user has a large transcript history.
- **Hook handler env var name.** The hook handler reads `CLAUDE_DASHBOARD_PORT` (not `DASHBOARD_PORT`) to choose its target port. Today this doesn't matter because both default to 4820. If the port is ever changed, **both** env vars must be set — the unit file's `DASHBOARD_PORT` does not propagate to Claude Code's hook spawns.
- **Node ≥22 is a hard floor.** Upstream's `better-sqlite3` is in `optionalDependencies`; if its native build fails, the runtime falls back to `node:sqlite`, which requires Node 22+. Verify task asserts this; asdf's "latest nodejs" satisfies it as of 2026-04-28.
