# ccxctl + dm-ccx

Bash control plane for the ccx coding station. Reads instance ID from
`~/.config/ccx/instance_id` (written by `terraform apply`).

## Install

```bash
ln -sf $(pwd)/bin/ccxctl    ~/.local/bin/ccxctl
ln -sf $(pwd)/bin/dm-ccx ~/.local/bin/dm-ccx
```

## Subcommands

| Command | Purpose |
|---|---|
| `ccxctl status` | state, type, ip, uptime |
| `ccxctl start` | start instance, update DNS, wait |
| `ccxctl stop` | stop instance, wait |
| `ccxctl ssh` | ssh to the hostname (passes args through) |
| `ccxctl refresh-sg` | update SG ingress to current public /32 |
| `ccxctl refresh-dns` | force-update Route 53 A record to instance's public IP |
| `ccxctl resize [TYPE]` | change instance type (stopped only) |
| `ccxctl grow-home [GB]` | grow /home volume + resize2fs via ssh |
| `ccxctl grow-root [GB]` | grow root volume + growpart + resize2fs via ssh |
| `ccxctl snapshot [NOTE]` | snapshot home volume, tagged |
| `ccxctl menu` | state-aware dmenu, re-execs the chosen subcommand |
| `ccxctl session launch --agent AGENT --dir DIR` | create a tmux window running `claude` or `codex` |
| `ccxctl session list` | list active agent sessions, uptime, and usage when available |

## DNS note

v1 has no EIP (sesio account's region quota is full). `ccxctl start` updates
the `ccx.dsylla.sesio.io` A record after every start, and `refresh-dns`
force-updates it on demand.

## Smoke checklist

- [ ] `ccxctl status` prints a sane line
- [ ] `ccxctl start` → `status` shows `running` + DNS resolves to current IP
- [ ] `ccxctl ssh true` exits 0
- [ ] `ccxctl refresh-sg` updates SG when public IP changes
- [ ] `ccxctl snapshot "smoke"` creates a snapshot
- [ ] `ccxctl stop` → `status` shows `stopped`
- [ ] `ccxctl resize t4g.large` works when stopped; back to xlarge after

## Tests

```bash
bats tests/ccxctl.bats
```
