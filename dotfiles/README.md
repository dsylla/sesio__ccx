# ccx dotfiles

Portable copies of the laptop's shell, editor, git, and Claude Code
configuration, consumed by the ccx Ansible `dotfiles` role on provisioning.

## Two sources of truth

The laptop's `~/.claude/` has some real files and some symlinks into
`~/claude-config/` (a separate private repo —
`git@github.com:dsylla/claude-config.git`). The server mirrors that split.

**From this repo (`sesio__ccx/dotfiles/`):**
- `.zshrc`, `.p10k.zsh`, `.tmux.conf`, `.gitconfig`
- `.claude/settings.json`, `.claude/RTK.md`
- `.claude/commands/`, `.claude/hooks/` — real directories, edited here

**From `dsylla/claude-config` (cloned to `~/claude-config/` on the server):**
- `~/.claude/CLAUDE.md` → `~/claude-config/CLAUDE.md` (symlink)
- `~/.claude/skills/`   → `~/claude-config/skills/`  (symlink)

The Ansible `dotfiles` role fetches a read-only deploy key from SSM Parameter
Store (`/ccx/claude_config_deploy_key`), clones `claude-config`, and creates
the two symlinks. Keeping `~/.claude/{CLAUDE.md,skills}` sourced from the
claude-config repo means the server picks up new skills / CLAUDE.md edits by
`git pull` rather than re-copying into this repo.

**Explicitly excluded:** `.claude/credentials*`, `.claude/projects/`,
`.claude/statsig/`, `~/.aws/credentials`, anything with tokens or passwords.
Claude Code and AWS both authenticate on first use — no static secrets at
rest in this repo.
