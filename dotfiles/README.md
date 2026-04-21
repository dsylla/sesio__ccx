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
