# Agent Monitor — operations notes

The dashboard is `hoangsonww/Claude-Code-Agent-Monitor`, MIT, vendored at a pinned tag and run as `agent-monitor.service` on the ccx EC2 host.

## Pinned version

Version source of truth:

```
ansible/roles/agent_monitor/defaults/main.yml
  agent_monitor_version: v1.1.0
```

Why pinned: upstream is a single-maintainer project and the `master` branch occasionally regresses. Pinning to a tag means the role is reproducible across re-provisions.

## Bumping

1. Identify the new tag at <https://github.com/hoangsonww/Claude-Code-Agent-Monitor/tags>.
2. Edit `agent_monitor_version` in `defaults/main.yml`.
3. Re-run the playbook on ccx (`ansible-pull` will run on the next boot, or run by hand from the host: `cd ~/sesio__ccx/ansible && ansible-playbook site.yml --tags agent_monitor` — note: tagging is not yet wired; until then, full playbook).
4. Verify: `ccxctl monitor status` → `active`, health `ok`.

What the bump does:
- The `git` task fetches the new ref and updates the working tree → `_agent_monitor_repo.changed = true`.
- That triggers `npm run setup` (rebuilds `node_modules` against any updated `package.json`) and `npm run build` (re-renders `client/dist`).
- The `restart agent-monitor` handler fires after both finish.

## Rollback

Revert the version bump in `defaults/main.yml` and re-run the playbook. The git task will check out the older ref; `node_modules` and `client/dist` are rebuilt against it. No data migration concerns — the SQLite schema lives at `/opt/agent-monitor/data/dashboard.db` and is preserved across version changes; if the new version's schema diverged forward and rollback breaks reads, delete the DB (it is monitoring exhaust, not user state).

## Manual verification

```bash
ssh david@ccx.dsylla.sesio.io
systemctl status agent-monitor
journalctl -u agent-monitor -n 100 --no-pager
curl -s http://127.0.0.1:4820/api/health | jq
```

From the laptop:

```bash
ccxctl monitor tunnel &     # foreground, but backgrounded with `&`
xdg-open http://localhost:4820
```
