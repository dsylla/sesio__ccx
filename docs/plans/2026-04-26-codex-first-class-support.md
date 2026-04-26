# Codex First-Class Support Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `ccx` provision, configure, manage, and display Codex as a first-class agent alongside Claude Code.

**Architecture:** Add a small agent catalog to the control plane, then route session launch, process discovery, session listing, and MOTD rendering through it. Add Codex-specific Ansible roles for CLI installation, config bootstrapping, MCP setup, and verification while preserving all existing Claude behavior.

**Tech Stack:** Python 3.11, Typer, pytest, Ansible, asdf Node, npm global packages, Codex CLI `0.125.0+`, tmux.

---

## Task 1: Add The Agent Catalog

**Files:**
- Create: `control-plane/ccx/agents.py`
- Modify: `control-plane/tests/test_sessions.py`

**Step 1: Write failing tests for the catalog**

Append these tests to `control-plane/tests/test_sessions.py`:

```python
def test_agent_catalog_contains_claude_and_codex():
    from ccx.agents import AGENTS, DEFAULT_AGENT, get_agent

    assert DEFAULT_AGENT == "claude"
    assert get_agent("claude").command == "claude"
    assert get_agent("codex").command == "codex"
    assert set(AGENTS) >= {"claude", "codex"}


def test_agent_window_names_round_trip_and_legacy_claude():
    from ccx.agents import split_window_name, window_name

    assert window_name("codex", "sesio__ccx") == "codex:sesio__ccx"
    assert split_window_name("codex:sesio__ccx") == ("codex", "sesio__ccx")
    assert split_window_name("sesio__ccx") == ("claude", "sesio__ccx")
```

**Step 2: Run the focused tests**

Run:

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane
/usr/bin/uv run pytest tests/test_sessions.py::test_agent_catalog_contains_claude_and_codex tests/test_sessions.py::test_agent_window_names_round_trip_and_legacy_claude -q
```

Expected: fail with `ModuleNotFoundError: No module named 'ccx.agents'`.

**Step 3: Implement the catalog**

Create `control-plane/ccx/agents.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    name: str
    command: str
    process_names: tuple[str, ...]
    config_root: str
    usage_source: str | None = None


DEFAULT_AGENT = "claude"

AGENTS: dict[str, AgentSpec] = {
    "claude": AgentSpec(
        name="claude",
        command="claude",
        process_names=("claude",),
        config_root="~/.claude",
        usage_source="~/.claude/projects",
    ),
    "codex": AgentSpec(
        name="codex",
        command="codex",
        process_names=("codex",),
        config_root="~/.codex",
        usage_source=None,
    ),
}


def get_agent(name: str) -> AgentSpec:
    try:
        return AGENTS[name]
    except KeyError as exc:
        choices = ", ".join(sorted(AGENTS))
        raise ValueError(f"unknown agent {name!r}; expected one of: {choices}") from exc


def window_name(agent_name: str, slug: str) -> str:
    get_agent(agent_name)
    return f"{agent_name}:{slug}"


def split_window_name(name: str) -> tuple[str, str]:
    if ":" not in name:
        return DEFAULT_AGENT, name
    agent_name, slug = name.split(":", 1)
    get_agent(agent_name)
    return agent_name, slug
```

**Step 4: Verify tests pass**

Run:

```bash
/usr/bin/uv run pytest tests/test_sessions.py::test_agent_catalog_contains_claude_and_codex tests/test_sessions.py::test_agent_window_names_round_trip_and_legacy_claude -q
```

Expected: both tests pass.

**Step 5: Commit**

```bash
git add control-plane/ccx/agents.py control-plane/tests/test_sessions.py
git commit -m "feat(control-plane): add supported agent catalog"
```

## Task 2: Make Session Collection Agent-Aware

**Files:**
- Modify: `control-plane/ccx/sessions.py`
- Modify: `control-plane/tests/test_sessions.py`

**Step 1: Write failing tests for agent-aware process discovery and mixed collection**

Add tests to `control-plane/tests/test_sessions.py`:

```python
def test_find_agent_pid_accepts_codex_process(tmp_path, monkeypatch):
    from ccx.agents import get_agent
    from ccx.sessions import find_agent_pid

    proc = tmp_path / "proc"
    (proc / "100/task/100").mkdir(parents=True)
    (proc / "100/task/100/children").write_text("101 ")
    (proc / "100/comm").write_text("bash\n")
    (proc / "101/task/101").mkdir(parents=True)
    (proc / "101/task/101/children").write_text("")
    (proc / "101/comm").write_text("codex\n")
    monkeypatch.setattr("ccx.sessions._PROC", str(proc))

    assert find_agent_pid(100, get_agent("codex")) == 101


def test_collect_sessions_reports_agent_and_legacy_claude(tmp_path, monkeypatch):
    from ccx.sessions import collect_sessions

    proc = tmp_path / "proc"
    for pid, comm in [(42, "bash"), (102, "claude"), (43, "bash"), (103, "codex")]:
        (proc / f"{pid}/task/{pid}").mkdir(parents=True)
        (proc / f"{pid}/comm").write_text(f"{comm}\n")
    (proc / "42/task/42/children").write_text("102 ")
    (proc / "43/task/43/children").write_text("103 ")
    (proc / "102/task/102").mkdir(parents=True)
    (proc / "102/task/102/children").write_text("")
    (proc / "102/stat").write_text("102 (claude) S " + "0 " * 18 + "50000 " + "0 " * 30)
    (proc / "103/task/103").mkdir(parents=True)
    (proc / "103/task/103/children").write_text("")
    (proc / "103/stat").write_text("103 (codex) S " + "0 " * 18 + "60000 " + "0 " * 30)

    monkeypatch.setattr("ccx.sessions._PROC", str(proc))
    monkeypatch.setattr("ccx.sessions._NOW_FN", lambda: 1700)
    monkeypatch.setattr("ccx.sessions._BOOT_FN", lambda: 1000)
    monkeypatch.setattr("ccx.sessions._CLAUDE_PROJECTS_DIR", str(tmp_path / "not-there"))

    with patch("ccx.sessions.tmux_list_windows", return_value=[
        {"slug": "legacy", "activity": 1, "cwd": "/work/legacy", "pane_pid": 42},
        {"slug": "codex:modern", "activity": 2, "cwd": "/work/modern", "pane_pid": 43},
    ]):
        rows = collect_sessions()

    assert rows[0]["agent"] == "claude"
    assert rows[0]["slug"] == "legacy"
    assert rows[0]["agent_pid"] == 102
    assert rows[1]["agent"] == "codex"
    assert rows[1]["slug"] == "modern"
    assert rows[1]["agent_pid"] == 103
    assert rows[1]["usage_today"]["available"] is False
```

**Step 2: Run the tests**

Run:

```bash
/usr/bin/uv run pytest tests/test_sessions.py::test_find_agent_pid_accepts_codex_process tests/test_sessions.py::test_collect_sessions_reports_agent_and_legacy_claude -q
```

Expected: fail because `find_agent_pid` and new row fields do not exist yet.

**Step 3: Refactor `sessions.py`**

In `control-plane/ccx/sessions.py`:

- Import `AgentSpec`, `get_agent`, and `split_window_name`.
- Replace `find_claude_pid` internals with `find_agent_pid(pane_pid, agent)`.
- Keep `find_claude_pid` as a compatibility wrapper.
- Update `collect_sessions()` to parse agent-prefixed tmux window names and emit new fields.

Core implementation shape:

```python
from ccx.agents import AgentSpec, get_agent, split_window_name


def find_agent_pid(pane_pid: int, agent: AgentSpec) -> int | None:
    to_visit = [pane_pid]
    seen: set[int] = set()
    while to_visit:
        pid = to_visit.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            with open(f"{_PROC}/{pid}/comm") as f:
                comm = f.read().strip()
            if comm in agent.process_names:
                return pid
        except (FileNotFoundError, PermissionError):
            pass
        try:
            tasks_dir = f"{_PROC}/{pid}/task"
            for tid in os.listdir(tasks_dir):
                try:
                    with open(f"{tasks_dir}/{tid}/children") as f:
                        to_visit.extend(int(child) for child in f.read().split())
                except (FileNotFoundError, PermissionError, ValueError):
                    continue
        except (FileNotFoundError, PermissionError):
            continue
    return None


def find_claude_pid(pane_pid: int) -> int | None:
    return find_agent_pid(pane_pid, get_agent("claude"))
```

Usage helper:

```python
def _usage_for_agent(agent_name: str, cwd: str) -> dict[str, int | bool]:
    if agent_name != "claude":
        return {"input": 0, "output": 0, "available": False}
    tk = parse_jsonl_tokens_today(_project_jsonl_files(cwd))
    return {**tk, "available": True}
```

`collect_sessions()` row shape:

```python
agent_name, bare_slug = split_window_name(row["slug"])
agent = get_agent(agent_name)
agent_pid = find_agent_pid(row["pane_pid"], agent)
usage = _usage_for_agent(agent.name, row["cwd"])
out.append({
    "agent": agent.name,
    "slug": bare_slug,
    "window": row["slug"],
    "cwd": row["cwd"],
    "pane_pid": row["pane_pid"],
    "agent_pid": agent_pid,
    "claude_pid": agent_pid if agent.name == "claude" else None,
    "uptime_seconds": _process_uptime_seconds(agent_pid) if agent_pid else None,
    "usage_today": usage,
    "tokens_today": {"input": int(usage["input"]), "output": int(usage["output"])},
})
```

**Step 4: Verify focused tests pass**

Run:

```bash
/usr/bin/uv run pytest tests/test_sessions.py::test_find_agent_pid_accepts_codex_process tests/test_sessions.py::test_collect_sessions_reports_agent_and_legacy_claude -q
```

Expected: pass.

**Step 5: Run all session tests**

Run:

```bash
/usr/bin/uv run pytest tests/test_sessions.py -q
```

Expected: all session tests pass after updating old assertions from `claude_pid` to the compatibility or new `agent_pid` field where appropriate.

**Step 6: Commit**

```bash
git add control-plane/ccx/sessions.py control-plane/tests/test_sessions.py
git commit -m "feat(sessions): collect tmux sessions by agent"
```

## Task 3: Add Agent Selection To Session Commands

**Files:**
- Modify: `control-plane/ccx/sessions.py`
- Modify: `control-plane/tests/test_sessions.py`
- Modify: `control-plane/tests/test_cli.py`

**Step 1: Write failing launch and table tests**

Add tests:

```python
def test_session_launch_codex_starts_codex_window(tmp_path):
    from ccx.sessions import app

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if "has-session" in argv:
            return _mock_run(returncode=1)
        return _mock_run(returncode=0)

    with patch("ccx.sessions.subprocess.run", side_effect=fake_run):
        result = CliRunner().invoke(app, ["launch", "--agent", "codex", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    new_window = next(c for c in calls if "new-window" in c)
    assert "codex:{}".format(tmp_path.name.lower()) in new_window
    assert new_window[-1] == "codex"


def test_session_list_table_includes_agent():
    from ccx.sessions import app

    row = {
        "agent": "codex",
        "slug": "ccx",
        "window": "codex:ccx",
        "cwd": "/work/ccx",
        "pane_pid": 42,
        "agent_pid": 102,
        "claude_pid": None,
        "uptime_seconds": 120.0,
        "usage_today": {"input": 0, "output": 0, "available": False},
        "tokens_today": {"input": 0, "output": 0},
    }
    with patch("ccx.sessions.collect_sessions", return_value=[row]):
        result = CliRunner().invoke(app, ["list"])

    assert result.exit_code == 0
    assert "AGENT" in result.stdout
    assert "codex" in result.stdout
    assert "ccx" in result.stdout
```

Update `test_ssh_default_uses_tmux` only if the CLI help string changes; `ccxctl ssh` should still attach to the shared tmux session, not launch either agent.

**Step 2: Run focused tests**

Run:

```bash
/usr/bin/uv run pytest tests/test_sessions.py::test_session_launch_codex_starts_codex_window tests/test_sessions.py::test_session_list_table_includes_agent -q
```

Expected: fail because `--agent` is missing and table output has no `AGENT` column.

**Step 3: Implement agent selection**

In `sessions.py`:

- Add `agent_name` option to `cmd_launch`.
- Use `window_name(agent.name, slug(path))` for new windows.
- Start `agent.command` instead of hard-coded `claude`.
- Update `cmd_list` table columns to include `AGENT` and use `agent_pid`.
- Add `--agent` option to `attach` and `kill` for plain slugs.
- Accept explicit `agent:slug` targets for attach and kill.

Helper shape:

```python
from ccx.agents import DEFAULT_AGENT, get_agent, window_name


def _resolve_window_target(slug_: str, agent_name: str | None = None) -> str:
    if ":" in slug_:
        return slug_
    if agent_name:
        return window_name(agent_name, slug_)
    if tmux_has_window(slug_):
        return slug_
    return window_name(DEFAULT_AGENT, slug_)
```

Launch shape:

```python
def _tmux_new_window(agent: AgentSpec, tmux_window: str, cwd: str) -> None:
    subprocess.run(
        ["tmux", "new-window", "-t", SESSION_NAME, "-n", tmux_window, "-c", cwd, "--", agent.command],
        capture_output=True,
        check=False,
        timeout=5,
    )
```

Typer option shape:

```python
agent_name: str = typer.Option(DEFAULT_AGENT, "--agent", "-a", help="Agent to launch.")
```

Wrap `get_agent()` errors:

```python
try:
    agent = get_agent(agent_name)
except ValueError as exc:
    raise typer.BadParameter(str(exc)) from exc
```

**Step 4: Verify focused tests pass**

Run:

```bash
/usr/bin/uv run pytest tests/test_sessions.py::test_session_launch_codex_starts_codex_window tests/test_sessions.py::test_session_list_table_includes_agent -q
```

Expected: pass.

**Step 5: Run control-plane tests**

Run:

```bash
/usr/bin/uv run pytest -q
```

Expected: all tests pass.

**Step 6: Commit**

```bash
git add control-plane/ccx/sessions.py control-plane/tests/test_sessions.py control-plane/tests/test_cli.py
git commit -m "feat(sessions): launch selectable coding agents"
```

## Task 4: Render Multi-Agent MOTD

**Files:**
- Modify: `control-plane/ccx/motd.py`
- Modify: `control-plane/tests/test_motd.py`

**Step 1: Write failing MOTD tests**

Add tests to `control-plane/tests/test_motd.py`:

```python
def test_render_motd_shows_mixed_agent_sessions():
    from ccx.motd import render_motd

    system = {"hostname": "ccx", "uptime": "1h", "cpu_pct": 5, "ram_pct": 10,
              "disk_used": "10G", "disk_total": "100G", "disk_pct": 10}
    instance = {"instance_id": "i-abc", "instance_type": "t4g.xlarge",
                "region": "eu-west-1", "az": "eu-west-1a",
                "public_ip": "1.2.3.4", "public_hostname": "h.example.com"}
    sessions = {"sessions": [
        {"agent": "claude", "slug": "api", "cwd": "/work/api", "uptime_seconds": 60,
         "usage_today": {"input": 10, "output": 5, "available": True},
         "tokens_today": {"input": 10, "output": 5}},
        {"agent": "codex", "slug": "ui", "cwd": "/work/ui", "uptime_seconds": 120,
         "usage_today": {"input": 0, "output": 0, "available": False},
         "tokens_today": {"input": 0, "output": 0}},
    ]}
    usage = {"today": {"input": 10, "output": 5, "total": 15}}
    services = {"services": [("docker", "active")]}
    dotfiles = {"sesio__ccx": {"sha": "abc1234", "behind": 0}}

    out = render_motd(system, instance, sessions, usage, services, dotfiles)

    assert "Claude Code X" not in out
    assert "claude" in out
    assert "codex" in out
    assert "usage -" in out
```

**Step 2: Run the focused test**

Run:

```bash
/usr/bin/uv run pytest tests/test_motd.py::test_render_motd_shows_mixed_agent_sessions -q
```

Expected: fail because the renderer does not include agent names and still has Claude-only branding.

**Step 3: Update MOTD rendering**

In `control-plane/ccx/motd.py`:

- Change `LOGO` subtitle from `Claude Code X` to neutral `ccx coding station`.
- Render session rows with `agent` and `slug`.
- Show usage numbers only when `usage_today.available` is true.
- Keep `collect_usage()` Claude-only until Codex has a stable local usage source.

Session row shape:

```python
usage = s.get("usage_today") or s.get("tokens_today") or {}
if usage.get("available", True):
    usage_part = (
        f"in {C.BOLD}{usage.get('input', 0)}{C.RESET}  "
        f"out {C.BOLD}{usage.get('output', 0)}{C.RESET}"
    )
else:
    usage_part = "usage -"
ses_body.append(
    f"{C.GREEN}●{C.RESET} {C.BOLD}{s.get('agent', 'claude'):<6}{C.RESET} "
    f"{C.BOLD}{s['slug']:<10}{C.RESET} {C.DIM}{s['cwd']}{C.RESET}   "
    f"up {C.BOLD}{up}{C.RESET}   {usage_part}"
)
```

**Step 4: Verify MOTD tests**

Run:

```bash
/usr/bin/uv run pytest tests/test_motd.py -q
```

Expected: all MOTD tests pass.

**Step 5: Run full control-plane tests**

Run:

```bash
/usr/bin/uv run pytest -q
```

Expected: all tests pass.

**Step 6: Commit**

```bash
git add control-plane/ccx/motd.py control-plane/tests/test_motd.py
git commit -m "feat(motd): show multi-agent coding sessions"
```

## Task 5: Install And Bootstrap Codex

**Files:**
- Create: `ansible/roles/codex_code/tasks/main.yml`
- Create: `ansible/roles/codex_config/tasks/main.yml`
- Create: `dotfiles/.codex/config.toml`
- Modify: `ansible/site.yml`
- Modify: `ansible/roles/verify/tasks/main.yml`

**Step 1: Add failing Ansible expectations**

Update `ansible/site.yml` to include the intended roles first, then run syntax check before creating role files:

```yaml
    - claude_code
    - codex_code
    - codex_config
    - claude_plugins
```

Run:

```bash
cd /home/david/Work/sesio/sesio__ccx
/usr/bin/uv run --with ansible -- ansible-playbook --syntax-check ansible/site.yml
```

Expected: fail because the new roles do not exist.

**Step 2: Create `codex_code` role**

Create `ansible/roles/codex_code/tasks/main.yml`:

```yaml
---
- name: Install @openai/codex globally via asdf Node
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.shell: |
    source "{{ target_home }}/.asdf/asdf.sh"
    npm install -g @openai/codex
  args:
    executable: /bin/bash
  register: _codex_install
  changed_when: "'added' in _codex_install.stdout or 'updated' in _codex_install.stdout or 'changed' in _codex_install.stdout"
```

**Step 3: Add baseline Codex config**

Create `dotfiles/.codex/config.toml`:

```toml
# Baseline ccx Codex config. MCP servers are managed by the codex_mcp role.
model = "gpt-5.5"
sandbox_mode = "workspace-write"
approval_policy = "on-request"

[shell_environment_policy]
inherit = "all"
```

Create `ansible/roles/codex_config/tasks/main.yml`:

```yaml
---
- name: Ensure ~/.codex exists
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.file:
    path: "{{ target_home }}/.codex"
    state: directory
    mode: "0700"

- name: Install baseline Codex config if absent
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.copy:
    src: "{{ repo_clone_path }}/dotfiles/.codex/config.toml"
    dest: "{{ target_home }}/.codex/config.toml"
    remote_src: true
    force: false
    mode: "0600"
```

Do not touch the untracked repository-root `.codex` file unless the user explicitly approves removing it.

**Step 4: Verify Codex in Ansible**

In `ansible/roles/verify/tasks/main.yml`, add after the Claude check:

```yaml
- name: Verify codex cli
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.shell: |
    source "{{ target_home }}/.asdf/asdf.sh"
    codex --version
  args:
    executable: /bin/bash
  register: _v_codex
  changed_when: false
```

Add to the provision marker:

```yaml
      codex:        {{ _v_codex.stdout | trim }}
```

**Step 5: Run syntax check**

Run:

```bash
/usr/bin/uv run --with ansible -- ansible-playbook --syntax-check ansible/site.yml
```

Expected: pass.

**Step 6: Commit**

```bash
git add ansible/site.yml ansible/roles/codex_code/tasks/main.yml ansible/roles/codex_config/tasks/main.yml ansible/roles/verify/tasks/main.yml dotfiles/.codex/config.toml
git commit -m "feat(ansible): install and verify codex cli"
```

## Task 6: Add Codex MCP Provisioning

**Files:**
- Create: `ansible/group_vars/all/mcp.yml`
- Create: `ansible/roles/codex_mcp/tasks/main.yml`
- Modify: `ansible/roles/claude_plugins/vars/main.yml`
- Modify: `ansible/site.yml`
- Modify: `ansible/roles/verify/tasks/main.yml`

**Step 1: Move MCP catalog to shared group vars**

Create `ansible/group_vars/all/mcp.yml` with the existing `mcp_stdio_servers` and `mcp_http_servers` from `ansible/roles/claude_plugins/vars/main.yml`.

Then remove those variables from `ansible/roles/claude_plugins/vars/main.yml` or leave only role-specific comments there. The Claude role should still read the same variable names from group vars.

**Step 2: Verify Claude role still parses**

Run:

```bash
/usr/bin/uv run --with ansible -- ansible-playbook --syntax-check ansible/site.yml
```

Expected: pass.

**Step 3: Add `codex_mcp` role to the site**

In `ansible/site.yml`, add `codex_mcp` after `codex_config` and before `rtk`:

```yaml
    - codex_mcp
```

Run syntax check.

Expected: fail until the role exists.

**Step 4: Create Codex MCP role**

Create `ansible/roles/codex_mcp/tasks/main.yml`:

```yaml
---
- name: Probe each Codex stdio MCP for existence
  become_user: "{{ target_user }}"
  become: true
  environment:
    PATH: "{{ target_home }}/.asdf/shims:/usr/local/bin:/usr/bin:/bin"
  ansible.builtin.command: "codex mcp get {{ item.name }}"
  loop: "{{ mcp_stdio_servers }}"
  loop_control:
    label: "{{ item.name }}"
  register: _codex_mcp_probe
  changed_when: false
  failed_when: false

- name: Install missing Codex stdio MCP servers
  become_user: "{{ target_user }}"
  become: true
  environment:
    PATH: "{{ target_home }}/.asdf/shims:/usr/local/bin:/usr/bin:/bin"
  ansible.builtin.command:
    cmd: >-
      codex mcp add
      {% for env in item.item.config.env | default({}) | dict2items %}
      --env {{ env.key }}={{ env.value }}
      {% endfor %}
      {{ item.item.name }} -- {{ item.item.config.command }}
      {{ item.item.config.args | default([]) | join(' ') }}
  loop: "{{ _codex_mcp_probe.results }}"
  loop_control:
    label: "{{ item.item.name }}"
  when: item.rc != 0

- name: Probe each Codex HTTP MCP for existence
  become_user: "{{ target_user }}"
  become: true
  environment:
    PATH: "{{ target_home }}/.asdf/shims:/usr/local/bin:/usr/bin:/bin"
  ansible.builtin.command: "codex mcp get {{ item.name }}"
  loop: "{{ mcp_http_servers }}"
  loop_control:
    label: "{{ item.name }}"
  register: _codex_mcp_http_probe
  changed_when: false
  failed_when: false

- name: Install missing Codex HTTP MCP servers
  become_user: "{{ target_user }}"
  become: true
  environment:
    PATH: "{{ target_home }}/.asdf/shims:/usr/local/bin:/usr/bin:/bin"
  ansible.builtin.command:
    cmd: "codex mcp add --url {{ item.item.url }} {{ item.item.name }}"
  loop: "{{ _codex_mcp_http_probe.results }}"
  loop_control:
    label: "{{ item.item.name }}"
  when: item.rc != 0
```

If `ansible-lint` objects to folded command templating, replace the install commands with a small checked-in helper script in `ansible/roles/codex_mcp/files/codex_mcp_add.py` that constructs argv safely from JSON.

**Step 5: Verify Codex MCP listing**

In `ansible/roles/verify/tasks/main.yml`, add:

```yaml
- name: Verify codex MCP list
  become_user: "{{ target_user }}"
  become: true
  environment:
    PATH: "{{ target_home }}/.asdf/shims:/usr/local/bin:/usr/bin:/bin"
  ansible.builtin.command: codex mcp list
  register: _v_codex_mcp
  changed_when: false
```

Add to the provision marker:

```yaml
      codex-mcp:    {{ (_v_codex_mcp.rc == 0) | ternary('ok', 'failed') }}
```

**Step 6: Run validation**

Run:

```bash
/usr/bin/uv run --with ansible -- ansible-playbook --syntax-check ansible/site.yml
/usr/bin/uv run --with ansible-lint -- ansible-lint ansible/site.yml
```

Expected: both pass, or lint gives an actionable complaint to fix before committing.

**Step 7: Commit**

```bash
git add ansible/site.yml ansible/group_vars/all/mcp.yml ansible/roles/claude_plugins/vars/main.yml ansible/roles/codex_mcp/tasks/main.yml ansible/roles/verify/tasks/main.yml
git commit -m "feat(ansible): configure codex mcp servers"
```

## Task 7: Update Documentation

**Files:**
- Modify: `README.md`
- Modify: `control-plane/README.md`
- Modify: `dotfiles/README.md`
- Modify: `terraform/README.md`

**Step 1: Update top-level README**

Replace the placeholder in `README.md` with:

````markdown
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
````

**Step 2: Update control-plane docs**

In `control-plane/README.md`, update session wording from Claude-only to agent-aware:

```markdown
| `ccxctl session launch --agent AGENT --dir DIR` | create a tmux window running `claude` or `codex` |
| `ccxctl session list` | list active agent sessions, uptime, and usage when available |
```

**Step 3: Update dotfiles docs**

In `dotfiles/README.md`, add a `Codex` section:

```markdown
## Codex

Codex config is provisioned separately under `~/.codex`.
The baseline `config.toml` is copied on first provisioning only, because
`codex mcp add` mutates the file when installing MCP servers.
Credentials, history, session databases, and auth files are excluded.
```

**Step 4: Update Terraform smoke docs**

In `terraform/README.md`, update the smoke test text to mention both `claude` and `codex` versions in `/var/log/ccx-provision-ok`.

**Step 5: Commit**

```bash
git add README.md control-plane/README.md dotfiles/README.md terraform/README.md
git commit -m "docs: document first-class codex support"
```

## Task 8: Final Verification

**Files:**
- No file edits unless verification reveals a defect.

**Step 1: Run control-plane tests**

Run:

```bash
cd /home/david/Work/sesio/sesio__ccx/control-plane
/usr/bin/uv run pytest -q
```

Expected: all tests pass.

**Step 2: Run Ansible syntax check**

Run:

```bash
cd /home/david/Work/sesio/sesio__ccx
/usr/bin/uv run --with ansible -- ansible-playbook --syntax-check ansible/site.yml
```

Expected: syntax check passes.

**Step 3: Run Ansible lint**

Run:

```bash
/usr/bin/uv run --with ansible-lint -- ansible-lint ansible/site.yml
```

Expected: lint passes.

**Step 4: Run Terraform formatting and validation if Terraform is initialized**

Run:

```bash
cd /home/david/Work/sesio/sesio__ccx/terraform
terraform fmt -check -recursive
terraform validate
```

Expected: both pass. If `terraform validate` fails because providers are not initialized, run `terraform init` only with user approval because it may use network access.

**Step 5: Check working tree**

Run:

```bash
cd /home/david/Work/sesio/sesio__ccx
git status --short
```

Expected: only the pre-existing untracked root `.codex` file remains, unless the user approved handling it.

**Step 6: Final commit if verification fixes were needed**

If verification required fixes:

```bash
git add <fixed files>
git commit -m "fix(codex): address verification issues"
```

If no fixes were needed, do not create an empty commit.
