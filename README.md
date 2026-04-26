# sesio__ccx

`ccx` is a remote EC2 coding station with a local control plane, Ansible provisioning, dotfiles, and tmux-backed coding-agent sessions.

Supported first-class agents:

- Claude Code
- Codex

The control plane can launch persistent agent sessions in tmux:

```bash
ccxctl session launch --agent claude --dir ~/Work/sesio/sesio__ccx
ccxctl session launch --agent codex --dir ~/Work/sesio/sesio__ccx
ccxctl session list
```
