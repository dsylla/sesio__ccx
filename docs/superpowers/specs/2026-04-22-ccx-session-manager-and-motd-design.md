# ccx — Session Manager + MOTD — Design

**Date:** 2026-04-22
**Codename:** `ccxctl session` + `ccxctl motd`
**Author:** David Sylla

## 1. Goal

Two new cohesive pieces for the ccx coding station (`ccx.dsylla.sesio.io`):

1. **Session manager (`ccxctl session …`)** — a project-anchored, tmux-backed wrapper for Claude Code sessions running on the instance. One canonical `claude` per working directory, persistent across SSH disconnects, queryable + attachable from a single CLI.
2. **MOTD (`ccxctl motd`)** — an ANSI-boxed login banner in the spirit of `sesio__motd`, showing system health, instance metadata, active claude sessions, today's token usage, service status, and dotfiles drift.

Both ship as new sub-commands of the existing `ccx-cli` Python package (`control-plane/ccx/`). No new binaries, no new repos, no long-running daemon.

## 2. Design decisions (resolved up-front)

| Question | Resolution |
|---|---|
| What is "a session"? | A tmux window inside a shared session `ccx`, keyed by a slug derived from the working directory. One canonical claude per cwd: `launch` attaches if already open. |
| Daemon or pure CLI? | **Pure CLI.** Every state query hits `tmux list-windows` live (~10 ms). No systemd unit, no socket, no cached state file. |
| Where does it live? | Subcommand of `ccxctl` in the existing `control-plane/ccx/` package. Reuses venv + pyproject. |
| tmux layout | One shared session `ccx`, one window per project. `tmux attach -t ccx` flips windows with `C-b n/p/w`. |
| Token usage in v1? | **In.** Per-session and global, parsed from `~/.claude/projects/<encoded>/*.jsonl`. |
| Auto-attach on SSH login? | `ccxctl ssh` default is `tmux new-session -A -s ccx` on the remote; `ccxctl ssh --raw` for a plain shell. |
| MOTD stack | Pure stdlib (no boto3). EC2 metadata via IMDSv2, session data via in-process call to the session-list logic. |

## 3. File layout (additions only)

```
control-plane/
├── ccx/
│   ├── cli.py                # (existing) top-level typer app — add `session` and `motd` subapps
│   ├── sessions.py           # NEW: tmux/claude/token logic; pure functions + typer Sub-app
│   ├── motd.py               # NEW: stdlib collectors + ANSI renderer; typer command
│   └── …                     # existing modules untouched
└── tests/
    ├── test_sessions.py      # NEW: slug, jsonl parsing, list formatting, mocks for tmux
    └── test_motd.py          # NEW: collectors with /proc fixtures, renderer golden-test

ansible/
└── roles/
    └── motd/                 # NEW: drops /etc/update-motd.d/10-ccx, disables Debian defaults
        ├── tasks/main.yml
        └── files/10-ccx      # the hook script
```

## 4. `ccxctl session` subcommand

### 4.1 Subcommands

```text
ccxctl session launch [--dir PATH]    # create-or-attach window for PATH (default: cwd)
ccxctl session list    [--json]       # table (default) or JSON of windows
ccxctl session attach  [SLUG]         # attach tmux session ccx, select window SLUG (default: MRU)
ccxctl session kill    SLUG           # kill window
ccxctl session menu                   # rofi picker (launch/attach/kill)
```

### 4.2 Slug rule

```
slug(path) = basename(path).lower().replace(/[^a-z0-9_-]/, "-").collapse("-+", "-")
```

If two directories produce the same basename, the second gets a parent-disambiguator: `ccx_sesio` vs `ccx_ssdd`. The slug is deterministic — re-running `launch` from the same directory reliably attaches.

### 4.3 tmux commands wrapped

| Logical op | Shell equivalent |
|---|---|
| ensure session | `tmux new-session -d -s ccx` (ignore if exists) |
| ensure window | `tmux has-session -t ccx:<slug>` → create with `tmux new-window -t ccx -n <slug> -c <path> -- claude` |
| list | `tmux list-windows -t ccx -F '#{window_name}|#{window_activity}|#{pane_current_path}|#{pane_pid}'` |
| attach | `exec tmux attach-session -t ccx \; select-window -t <slug>` |
| kill | `tmux kill-window -t ccx:<slug>` |

### 4.4 Enriched `list` output

Each row combines tmux state with:

- **claude pid** — first `claude` descendant of the window's `pane_pid` (via `/proc/<pid>/task/*/children`)
- **uptime** — `now - stat.start_time(pane_pid)`
- **today's tokens** — sum `input_tokens + output_tokens` from `~/.claude/projects/<encoded>/*.jsonl` lines where `timestamp` is today (UTC); encoded-path = claude-code's convention (`-home-david-Work-sesio-ccx` → `/home/david/Work/sesio/ccx`).

JSON schema:
```json
[
  {
    "slug": "ccx",
    "cwd":  "/home/david/Work/sesio/sesio__ccx",
    "pane_pid": 12345,
    "claude_pid": 12389,
    "uptime_seconds": 3821,
    "tokens_today": {"input": 54210, "output": 8123}
  }
]
```

### 4.5 `ccxctl ssh` changes

- Default: `ssh user@host -t -- tmux new-session -A -s ccx` (`-t` forces TTY, `-A` attach-or-create).
- `ccxctl ssh -R` / `--raw`: skip tmux, plain shell.
- `ccxctl ssh -- <args>`: extra args to `ssh` pass through.

## 5. `ccxctl motd`

### 5.1 Renderer

Port `sesio__motd`'s helpers verbatim:

- `C` ANSI class, `_visible_len`, `_wrap_field`, `_row`, `_full_row`, `_box_top/_box_mid/_box_mid_right/_box_bottom`, terminal-width-adaptive `LEFT_W/RIGHT_W/FULL_W`.
- Logo: keep it tight — an ASCII-art `ccx` or borrow the sesiO glyph. Final text decided at implementation.

### 5.2 Collectors

Each returns `Optional[dict]`; `None` → panel renders "unavailable". Run in parallel via `ThreadPoolExecutor(max_workers=N)` with a **5 s global** deadline and a **3 s per-subprocess** cap.

| Panel | Keys | Source |
|---|---|---|
| SYSTEM | `hostname, uptime, cpu_pct, ram_pct, disk_used, disk_total, disk_pct` | `/proc/uptime`, `/proc/stat`, `/proc/meminfo`, `shutil.disk_usage` |
| INSTANCE | `instance_id, instance_type, region, az, public_ip, public_hostname` | IMDSv2 (`curl -H X-aws-ec2-metadata-token …`) |
| SESSIONS | `[{slug, cwd, uptime, tokens_today}…]` | call into `sessions.collect_sessions()` directly — same code path as `session list` |
| USAGE | `today: {input, output, total}, 7d: {…}` | aggregate across all projects |
| SERVICES | `[(name, state)…]` for `docker, ssh, fail2ban, unattended-upgrades` | `systemctl is-active` |
| DOTFILES | `{sesio__ccx: {sha, behind}, claude-config: {sha, behind}}` | `git rev-parse --short HEAD`, `git rev-list --count HEAD..origin/main` |

### 5.3 Install wiring (ansible `motd` role)

- **New role `motd`** in `ansible/roles/motd/`; site.yml appends it after `verify`.
- Disable Debian's default update-motd scripts (they print uptime/kernel we already cover):
  ```yaml
  - name: Remove default /etc/update-motd.d entries
    ansible.builtin.file:
      path: "/etc/update-motd.d/{{ item }}"
      state: absent
    loop: [10-uname, 50-motd-news, 90-updates-available]
  ```
- Drop `/etc/update-motd.d/10-ccx` (mode 0755):
  ```bash
  #!/bin/sh
  # Runs as the logging-in user at PAM's motd stage.
  exec "$HOME/.local/bin/ccxctl" motd 2>/dev/null || true
  ```
  Symlink in `~/.local/bin/ccxctl` resolves to the control-plane shim → `uv run ccxctl motd`. The shim's `uv run` cache is warm after first login (no repeated sync on subsequent logins).

### 5.4 Performance budget

- All collectors in parallel, global 5 s hard cap.
- IMDSv2 call: ~10 ms on the instance (169.254.169.254 local).
- Token JSONL parse: line-scan today's files only — for a typical day this is < 50 ms.
- Git status: subprocess + network-free (`rev-list --count` is local).
- Target total: **< 200 ms** cold first-login, **< 100 ms** warm.

## 6. Testing

### 6.1 `test_sessions.py`

- Pure-function tests for `slug()`, `_parse_today_tokens(jsonl_lines)`, `_find_claude_pid(pane_pid)`, `_encode_project_dir(path)`.
- `list` command with `subprocess.run` mocked to replay canned `tmux list-windows` output.
- `launch` mocked: assert it calls `tmux has-session` then the right `new-window` command.
- Typer CliRunner for argument parsing / `--help`.

### 6.2 `test_motd.py`

- Each collector with its data source stubbed (`/proc/*` read via `tmp_path` fixtures, `subprocess.run` mocked for `systemctl` / `git`, `urllib` mocked for IMDS).
- Renderer golden-test: feed known collector outputs → assert exact string.
- Timeout test: one collector sleeps 10 s, verify global 5 s cap kicks in and renders "unavailable".

## 7. Non-goals (v1)

- **Idle-session detection / auto-stop.** The instance is manually stopped via `ccxctl stop`.
- **Token quota enforcement.** Display only. Anthropic's rate limits still apply server-side.
- **Web UI.** CLI + motd are enough.
- **Session templates / per-project tmux configs.** Users can decorate their own `.tmux.conf`.
- **Windowing inside each project (multiple panes).** Users split panes themselves once attached.

## 8. Interaction points with existing code

- `ccxctl ssh` behavior change — add `--raw` flag; default path now goes through tmux.
- `ccx/cli.py` — add `app.add_typer(session_app, name="session")` and `app.command("motd")(motd_main)`.
- `ansible/site.yml` — append `- motd` to the roles list.
- `ansible/roles/verify/tasks/main.yml` — add `ccxctl motd` smoke run (exits 0, writes something) to the marker output.
- `sesio__ccx/dotfiles/.tmux.conf` — no changes required; `ccx` tmux session uses the user's existing config.

## 9. Success criteria

v1 is done when:

1. `ccxctl ssh` lands in the shared tmux session `ccx` with the MRU window (or an empty pane when none exist).
2. `ccxctl ssh --raw` yields a plain shell for scripting.
3. `ccxctl session launch --dir ~/Work/sesio/sesio__ccx` opens a window, starts `claude` in it, stamps the cwd. Running it again attaches.
4. `ccxctl session list` shows the launched window with claude pid, uptime, and today's token totals.
5. `ccxctl session attach ccx` re-enters the running claude from a detached shell.
6. `ccxctl session kill ccx` cleanly removes the window.
7. On SSH login, `/etc/motd` (or the MOTD phase) prints the ccxctl-generated banner in under 500 ms.
8. Motd shows: correct uptime/CPU/RAM/disk, correct EC2 type/region/AZ, live session list matching step 4, aggregated token totals, service dots for docker/ssh/fail2ban, short SHAs for `sesio__ccx` and `claude-config` with drift indicator.
9. Ansible playbook still passes `make check`; `verify` role's marker includes `ccxctl motd` smoke.
