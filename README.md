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

## Agent Monitor

[`hoangsonww/Claude-Code-Agent-Monitor`](https://github.com/hoangsonww/Claude-Code-Agent-Monitor) (pinned to `v1.1.0`) runs as the `agent-monitor.service` systemd unit on the ccx host, listening on `127.0.0.1:4820`. The EC2 security group does not open 4820; access from the laptop is via SSH tunnel only.

```bash
ccxctl monitor status     # is the service active + /api/health ok?
ccxctl monitor tunnel     # forward localhost:4820 → ccx 127.0.0.1:4820
ccxctl monitor logs -f    # tail journald
```

Visit `http://localhost:4820` in a browser while the tunnel is open.

To disable: comment out `agent_monitor` in `ansible/site.yml`, then `sudo systemctl disable --now agent-monitor` on the host. Hooks fail silently when the service is down (`hook-handler.js` exits 0 on connect-refused), so Claude Code sessions are never blocked.

To bump the version: edit `agent_monitor_version` in `ansible/roles/agent_monitor/defaults/main.yml` and re-run the playbook. See `docs/agent-monitor.md`.

### Smoke checklist

- [ ] On host: `systemctl is-active agent-monitor` → `active`
- [ ] On host: `curl http://127.0.0.1:4820/api/health` → `{"status":"ok",…}`
- [ ] From laptop: `ccxctl monitor tunnel` → opens; `http://localhost:4820` loads the React UI
- [ ] Triggering any Claude Code event makes it appear in the dashboard
- [ ] `ccxctl monitor logs -f` streams journald output
- [ ] `sudo systemctl restart agent-monitor` → still healthy
```
