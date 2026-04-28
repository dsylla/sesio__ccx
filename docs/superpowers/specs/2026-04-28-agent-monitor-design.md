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
- In `NODE_ENV=production`, also serves the prebuilt React client from `client/dist`. Without that build, `localhost:4820` is API-only.
- Provides `npm run install-hooks` to add hook entries to `~/.claude/settings.json` that exec `node /opt/agent-monitor/scripts/hook-handler.js <event>`. The handler reads JSON from stdin and POSTs to `127.0.0.1:4820/api/hooks/event`. Designed to fail silently (exits 0) on connect-refused so Claude Code is never blocked.
- Health endpoint: `GET /api/health` → `{"status":"ok","timestamp":"..."}`.

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

- Source: `/opt/agent-monitor`, owned by `david`. Cloned by Ansible, pinned to `v1.1.0`.
- Service: `agent-monitor.service`, `User=david`, `Type=simple`, `ExecStart=/bin/bash -lc 'source ~/.asdf/asdf.sh && exec npm start'`, `Environment=NODE_ENV=production DASHBOARD_PORT=4820`, `Restart=on-failure`.
- Logs: journald, no separate logfile.
- DB: SQLite, default location under the install dir / `~/.claude` (whatever the upstream chooses; we don't override).

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

**`tasks/main.yml`** flow (idempotent):

1. `ansible.builtin.file`: ensure `{{ agent_monitor_install_dir }}` exists, owner `{{ target_user }}`, group `{{ target_user }}`, mode `0755`.
2. `ansible.builtin.git`: clone `{{ agent_monitor_repo }}` to install dir, `version: "{{ agent_monitor_version }}"`, `update: yes`. Register `_repo`. Notifies the `restart agent-monitor` handler when the SHA changes.
3. `ansible.builtin.shell` `npm run setup` as `{{ target_user }}`, asdf sourced. `creates: {{ install_dir }}/node_modules`. Always re-run when `_repo.changed`.
4. `ansible.builtin.shell` `npm run build` as `{{ target_user }}`, asdf sourced. `creates: {{ install_dir }}/client/dist/index.html`. Always re-run when `_repo.changed`.
5. `ansible.builtin.template`: render `agent-monitor.service.j2` to `/etc/systemd/system/agent-monitor.service`, mode `0644`. Notifies `daemon-reload + restart`.
6. `ansible.builtin.systemd`: `name=agent-monitor enabled=yes state=started daemon_reload=yes`.
7. `ansible.builtin.shell` `npm run install-hooks` as `{{ target_user }}`, asdf sourced. Idempotent — the script always rewrites `~/.claude/settings.json` (with the same bytes after the first run), so the script's stdout reports `Installed: N new, updated: M existing`. Use `changed_when: "'Installed: 0 ' not in result.stdout"` so only first-run-style installs show as changed; subsequent runs (which only "update" idempotently) report unchanged.

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
Environment=NODE_ENV=production
Environment=DASHBOARD_PORT={{ agent_monitor_port }}
ExecStart=/bin/bash -lc 'source {{ target_home }}/.asdf/asdf.sh && exec npm start'
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**`handlers/main.yml`:** one handler — `daemon-reload + restart agent-monitor`.

### 2. Site wiring

Insert `agent_monitor` after `claude_code` in `ansible/site.yml`:

```yaml
roles:
  - ...
  - claude_code
  - agent_monitor
  - codex_code
  - codex_config
  - codex_mcp
  - claude_plugins
  - ...
```

Order rationale: `claude_code` must run first (we need `claude` installed before hooks make sense). `agent_monitor` is independent of `claude_plugins` (both touch `settings.json` but in non-overlapping keys: plugins under `mcpServers`, hooks under `hooks`). Order between them is safe either way; placed before plugins for grouping with `claude_code`.

### 3. ccxctl subcommand: `ccxctl monitor`

New module `control-plane/ccx/monitor.py`. Registered in `cli.py` like `sessions`:

```python
from ccx.monitor import app as _monitor_app
app.add_typer(_monitor_app, name="monitor", help="Manage the Claude Code agent monitor service.")
```

Surface:

| Command | Behaviour |
|---|---|
| `ccxctl monitor status` | Two SSH calls: `systemctl is-active agent-monitor` and `curl -fsS http://127.0.0.1:4820/api/health`. Print styled output via `_step` / `_ok` / `die`. Exit 0 only if both succeed and the health JSON has `status == "ok"`. |
| `ccxctl monitor tunnel` | `os.execvp("ssh", [..., "-N", "-L", "4820:127.0.0.1:4820", f"{ssh_user}@{hostname}"])` — opens the tunnel in the foreground and blocks until Ctrl-C. |
| `ccxctl monitor tunnel --print` / `-p` | Print the equivalent ssh command and exit 0; do not exec. |
| `ccxctl monitor logs` | `os.execvp("ssh", [..., "-t", host, "journalctl -u agent-monitor"])`. |
| `ccxctl monitor logs --follow` / `-f` | Same with `journalctl -u agent-monitor -f`. |

All commands re-use `CFG.ssh_user`, `CFG.ssh_key`, `CFG.hostname` from `ccx.cli`. No new config surface. SSH options match the existing `ssh()` and `_ssh_exec()`: `-i $key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new`.

### 4. Verification (`ansible/roles/verify`)

Append three checks (and corresponding `provision-ok` marker lines):

```yaml
- name: Verify agent-monitor service is active
  ansible.builtin.command: systemctl is-active agent-monitor
  register: _v_agent_monitor
  changed_when: false

- name: Verify /api/health responds
  ansible.builtin.uri:
    url: http://127.0.0.1:4820/api/health
    return_content: yes
  register: _v_agent_monitor_health
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
| `test_status_active_and_healthy` | systemctl→`active`, curl→`{"status":"ok"}`, exit 0, both lines in stdout. |
| `test_status_systemd_inactive_exits_nonzero` | systemctl→`inactive` (rc=3); exit != 0; error references unit name. |
| `test_status_health_endpoint_unreachable` | systemctl `active`, curl rc=7; exit != 0; error mentions `/api/health`. |
| `test_status_invalid_health_json` | curl rc=0 but body is `not-json`; exit != 0; message mentions parse failure. |
| `test_tunnel_default_execs_ssh_with_L_flag` | Patch `os.execvp`; argv contains `-L`, `4820:127.0.0.1:4820`, `-N`, configured `david@ccx.dsylla.sesio.io`. |
| `test_tunnel_print_outputs_command_no_exec` | `--print` → exit 0, stdout contains `ssh -L 4820:127.0.0.1:4820`, `os.execvp` NOT called. |
| `test_logs_no_follow_execs_journalctl` | argv ends with `journalctl -u agent-monitor` (no `-f`). |
| `test_logs_follow_adds_f_flag` | argv contains `-f`. |
| `test_status_uses_configured_host_and_user` | `monkeypatch.setenv` + module reload; SSH args use those values. |

Helpers (`_mock_run`) copied from `test_sessions.py` for consistency.

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
- **vscode-extension subdir.** `npm run setup` also runs `(cd vscode-extension && npm install)` — dead weight on a headless server (~30s extra install once per version bump). Accepted; not patched.
- **Wildcard bind on 4820.** Server binds `0.0.0.0:4820` with no host-override mechanism upstream. We do not patch — the EC2 SG never opens 4820, so it's unreachable externally. Confirmed acceptable trade-off rather than maintaining an upstream patch.
- **Hooks fail silently if the service is down.** `hook-handler.js` exits 0 on connect-refused. Disabling the role + service therefore degrades cleanly without breaking Claude Code sessions.
