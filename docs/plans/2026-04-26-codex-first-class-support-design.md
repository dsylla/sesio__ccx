# First-Class Codex Support Design

Date: 2026-04-26

## Goal

Make `ccx` a first-class multi-agent coding station. Claude Code remains supported, while Codex gets equivalent provisioning, configuration, MCP setup, session management, MOTD visibility, and verification.

The approved direction is side-by-side support with a shared agent abstraction. This avoids breaking existing Claude workflows while removing the current assumption that every coding session, config file, and usage source belongs to Claude.

## Current State

The project is still Claude-centric:

- Ansible installs `@anthropic-ai/claude-code` and Claude plugins, but does not install `@openai/codex`.
- MCP setup is driven through `claude mcp ...` and stores state under Claude's config files.
- Dotfiles assemble `~/.claude` from this repo plus the private `claude-config` repo.
- `ccxctl session` launches `claude`, searches only for a `claude` process, and reads usage from `~/.claude/projects`.
- `ccxctl motd` brands the host as "Claude Code X" and reports Claude sessions, Claude usage, and `claude-config` drift.

## Approach

Introduce an agent catalog with one entry per supported client:

- `claude`: command `claude`, config root `~/.claude`, process names including `claude`, usage source `~/.claude/projects`.
- `codex`: command `codex`, config root `~/.codex`, process names including `codex`, usage source added only if Codex exposes a stable local log format.

The catalog should be used by provisioning and the control plane instead of scattering string literals across roles and Python modules. Claude remains the default launch agent initially to preserve existing behavior. Codex is selectable with `--agent codex`.

## Components

### Ansible

Add roles for Codex:

- `codex_code`: install `@openai/codex` globally through the existing asdf Node setup.
- `codex_config`: provision `~/.codex/config.toml`, skills/plugins where supported, and any project-level Codex files.
- `codex_mcp`: install MCP servers with `codex mcp add`, using the same MCP catalog where possible.

Keep the existing Claude roles, but rename or wrap role intent over time so `claude_plugins` does not become the shared place for non-Claude MCP data.

Add verification that checks:

- `codex --version`.
- `codex mcp list` exits successfully after configuration.
- Existing Claude checks still pass.

### Dotfiles

Keep `~/.claude` provisioning as-is for now. Add a parallel `~/.codex` path:

- `dotfiles/.codex/config.toml` for Codex defaults, sandbox policy, MCP references, and trusted project roots.
- Optional Codex skills under a dedicated Codex-compatible location if the installed CLI supports local skills on the target version.
- No credentials, session databases, auth files, or history files in the repo.

The current untracked root `.codex` file must be removed or replaced with a directory before adding committed Codex dotfiles.

### Control Plane

Generalize `ccx.sessions` from Claude sessions to agent sessions:

- `ccxctl session launch --agent claude|codex --dir PATH`.
- Existing `ccxctl session launch --dir PATH` keeps launching Claude unless we intentionally change the default later.
- Session rows include `agent`, `slug`, `cwd`, `pane_pid`, `agent_pid`, `uptime_seconds`, and `usage_today`.
- Tmux windows should include the agent in the window name or metadata to avoid collisions when Claude and Codex are launched for the same project.

Example naming:

- `claude:sesio__ccx`
- `codex:sesio__ccx`

Process discovery should accept the configured process names for the selected agent instead of checking only `comm == "claude"`.

### MOTD

Update the banner and sections to present the box as a multi-agent coding station:

- Replace "Claude Code X" wording with neutral `ccx` branding.
- Show active sessions grouped by agent.
- Show Claude usage from the existing JSONL parser.
- Show Codex usage only when there is a stable local source; otherwise show session uptime and process state without inventing token accounting.
- Show drift for `sesio__ccx`, `claude-config`, and any Codex config source that becomes a real repo.

## Data Flow

Provisioning installs both CLIs through asdf Node. Dotfiles create separate home config trees for Claude and Codex. MCP roles use the shared MCP catalog to configure each client through its own CLI. `ccxctl session launch` starts the requested agent inside the shared `ccx` tmux session. `ccxctl session list` reads tmux windows, infers or reads the agent, finds the matching child process, and attaches usage data when available. MOTD calls the same session collection path so login output matches `ccxctl session list`.

## Error Handling

- Missing Codex binary should fail `verify` clearly after the Codex role runs.
- Failed Codex MCP installation should identify the MCP name and command, not silently continue.
- Missing usage logs should return zero or unavailable usage, not fail session listing or MOTD.
- Unknown agent names should produce a CLI validation error.
- Existing Claude commands and sessions should remain compatible during the transition.

## Testing

Control-plane tests should cover:

- Agent catalog entries for Claude and Codex.
- `launch --agent codex` starts `codex` in tmux.
- Claude launch behavior remains backward compatible.
- Session listing reports `agent` and `agent_pid`.
- Same project can have both Claude and Codex windows without slug collision.
- MOTD renders mixed Claude and Codex sessions.
- Usage remains unavailable or zero for Codex until a stable parser exists.

Ansible validation should cover:

- Syntax check for new roles.
- Idempotent installed-version probes.
- `codex mcp list` in verify.
- Existing Claude verification unchanged.

## Non-Goals

- Do not migrate Claude hooks to Codex unless Codex exposes equivalent hook semantics.
- Do not copy Claude credentials, sessions, stats, or auth files.
- Do not rename the whole project or remove Claude support.
- Do not implement Codex token accounting without confirming Codex's current local data format.

## Open Questions

- Should Codex become the default session agent after both clients are stable, or should Claude remain the default indefinitely?
- Should `claude-config` content be split into a neutral private config repo later, or should Codex use a new separate source?
- Which Codex plugin marketplace entries, if any, should be installed on the station?
