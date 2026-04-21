# ccx — Dotfiles Seed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Seed `dotfiles/` in `sesio__ccx` with portable copies of the laptop's shell, editor, and Claude Code configuration so the Ansible `dotfiles` role can symlink them into `$HOME` on the coding station.

**Architecture:** Copy-and-redact. Start from the laptop's live files, strip or `#`-comment anything laptop-specific (GUI bindings, xrandr, screenshots path, credential-bearing configs), and land them under `dotfiles/` in this repo. No secrets committed. Safe audits between copy and commit.

**Tech Stack:** Plain files (zsh, bash, tmux, git, JSON, Markdown). Nothing to build.

---

## File Structure

```
sesio__ccx/
└── dotfiles/
    ├── README.md
    ├── .zshrc
    ├── .p10k.zsh
    ├── .tmux.conf
    ├── .gitconfig
    └── .claude/
        ├── settings.json
        ├── CLAUDE.md
        ├── skills/     # copied tree
        ├── commands/   # copied tree
        └── hooks/      # copied tree
```

**Excluded (must NOT be staged):** `.claude/credentials*`, `.claude/projects/`, `.claude/statsig/`, anything matching `secret`, `token`, `api_key`, `password`, `bearer`.

---

### Task 1: Scaffold `dotfiles/` + README

**Files:**
- Create: `dotfiles/README.md`

- [ ] **Step 1: Create directory**

Run: `mkdir -p /home/david/Work/sesio/sesio__ccx/dotfiles`
Expected: no output; directory exists.

- [ ] **Step 2: Write README.md**

File `/home/david/Work/sesio/sesio__ccx/dotfiles/README.md`:

```markdown
# ccx dotfiles

Portable copies of the laptop dotfiles consumed by the ccx Ansible
`dotfiles` role. On provisioning, the repo is cloned to
`/home/david/sesio__ccx` and every file in `dotfiles/` is symlinked into
`/home/david/`.

## What's here
- `.zshrc`, `.p10k.zsh`, `.tmux.conf`, `.gitconfig`
- `.claude/` — Claude Code settings, skills, commands, hooks

## What's NOT here
- `.claude/credentials*` (OAuth on first use)
- `.claude/projects/` (per-project memory is laptop-specific)
- `~/.aws/credentials` (instance uses its IAM role)
- Anything with tokens / secrets / passwords
```

- [ ] **Step 3: Verify**

Run: `cat /home/david/Work/sesio/sesio__ccx/dotfiles/README.md`
Expected: prints the content above.

---

### Task 2: Seed base shell dotfiles

**Files:**
- Create: `dotfiles/.zshrc`
- Create: `dotfiles/.p10k.zsh`
- Create: `dotfiles/.tmux.conf`
- Create: `dotfiles/.gitconfig`

- [ ] **Step 1: Copy from laptop**

```bash
cp /home/david/.zshrc      /home/david/Work/sesio/sesio__ccx/dotfiles/.zshrc
cp /home/david/.p10k.zsh   /home/david/Work/sesio/sesio__ccx/dotfiles/.p10k.zsh
cp /home/david/.tmux.conf  /home/david/Work/sesio/sesio__ccx/dotfiles/.tmux.conf
cp /home/david/.gitconfig  /home/david/Work/sesio/sesio__ccx/dotfiles/.gitconfig
```

- [ ] **Step 2: Audit for laptop-only references**

Run: `grep -En 'xrandr|xinput|i3|picom|Xresources|Screenshots|redshift|pactl|bluetoothctl|nm-applet|libreoffice' /home/david/Work/sesio/sesio__ccx/dotfiles/.zshrc /home/david/Work/sesio/sesio__ccx/dotfiles/.p10k.zsh /home/david/Work/sesio/sesio__ccx/dotfiles/.tmux.conf /home/david/Work/sesio/sesio__ccx/dotfiles/.gitconfig`

For each hit: if the line is a GUI-only alias/path, `#`-comment it (don't delete — easier to diff against the laptop later).

- [ ] **Step 3: Audit for secrets**

Run: `grep -Ein 'token|secret|api_key|password|bearer' /home/david/Work/sesio/sesio__ccx/dotfiles/.zshrc /home/david/Work/sesio/sesio__ccx/dotfiles/.gitconfig`
Expected: no hits. If any: replace with `${ENV_VAR_NAME}` references and document the var in README.

- [ ] **Step 4: Verify `.zshrc` parses**

Run: `zsh -n /home/david/Work/sesio/sesio__ccx/dotfiles/.zshrc`
Expected: no output (syntax OK). Non-zero exit = syntax error; fix before continuing.

- [ ] **Step 5: Verify `.tmux.conf` parses**

Run: `tmux -f /home/david/Work/sesio/sesio__ccx/dotfiles/.tmux.conf -L ccxtest new-session -d 'exit' ; tmux -L ccxtest kill-server 2>/dev/null || true`
Expected: session starts and exits cleanly. Config errors print on stderr.

- [ ] **Step 6: Verify `.gitconfig` parses**

Run: `git -c include.path=/home/david/Work/sesio/sesio__ccx/dotfiles/.gitconfig config --list | head`
Expected: prints keys without `fatal:` errors.

---

### Task 3: Seed Claude Code config (safe subset)

**Files:**
- Create: `dotfiles/.claude/settings.json`
- Create: `dotfiles/.claude/CLAUDE.md`
- Create: `dotfiles/.claude/skills/` (copied tree)
- Create: `dotfiles/.claude/commands/` (copied tree)
- Create: `dotfiles/.claude/hooks/` (copied tree)

- [ ] **Step 1: Create target directory**

Run: `mkdir -p /home/david/Work/sesio/sesio__ccx/dotfiles/.claude`

- [ ] **Step 2: Copy settings + CLAUDE.md**

```bash
cp /home/david/.claude/settings.json /home/david/Work/sesio/sesio__ccx/dotfiles/.claude/settings.json
cp /home/david/.claude/CLAUDE.md     /home/david/Work/sesio/sesio__ccx/dotfiles/.claude/CLAUDE.md
```

- [ ] **Step 3: Copy trees (skills / commands / hooks)**

```bash
cp -a /home/david/.claude/skills   /home/david/Work/sesio/sesio__ccx/dotfiles/.claude/skills
cp -a /home/david/.claude/commands /home/david/Work/sesio/sesio__ccx/dotfiles/.claude/commands
cp -a /home/david/.claude/hooks    /home/david/Work/sesio/sesio__ccx/dotfiles/.claude/hooks
```

- [ ] **Step 4: Strip OAuth / API tokens from settings.json**

Open `/home/david/Work/sesio/sesio__ccx/dotfiles/.claude/settings.json` and remove any of: `apiKey`, `oauthToken`, `refreshToken`, `accessToken`, `sessionToken`, or anything with a secret-shaped name.

Verify:
Run: `jq 'keys' /home/david/Work/sesio/sesio__ccx/dotfiles/.claude/settings.json`
Expected: a JSON list of keys; none look secret.

- [ ] **Step 5: Audit the .claude tree for stray secrets**

Run: `grep -rEin 'token|secret|api_key|password|bearer' /home/david/Work/sesio/sesio__ccx/dotfiles/.claude/ | grep -v -E '\.md:' | head -50`
Expected: any hits are clearly docs mentions (a skill README explaining how tokens work), never real credentials. If unsure, redact.

- [ ] **Step 6: Confirm exclusions didn't sneak in**

Run: `ls /home/david/Work/sesio/sesio__ccx/dotfiles/.claude/`
Expected: lists `settings.json`, `CLAUDE.md`, `skills`, `commands`, `hooks` — nothing else (no `projects`, no `credentials*`, no `statsig`).

---

### Task 4: Commit

- [ ] **Step 1: Stage**

Run: `cd /home/david/Work/sesio/sesio__ccx && git add dotfiles/`

- [ ] **Step 2: Review staged content one more time**

Run: `git status && git diff --cached --stat | tail -30`
Expected: only paths under `dotfiles/`; no `credentials*`, no `.claude/projects/`, no `.claude/statsig/`.

- [ ] **Step 3: Commit**

Invoke the `/commit` slash command. Suggested conventional message: `feat(dotfiles): seed portable laptop dotfiles for ccx`. Accept or refine what `/commit` proposes.

---

## Done when

1. `dotfiles/` exists and contains the files in **File Structure**.
2. None of the excluded paths are staged.
3. `zsh -n`, the `tmux -f … new-session -d 'exit'` smoke, and `git config --list` all parse against the new files without errors.
4. One commit added on the current branch.
