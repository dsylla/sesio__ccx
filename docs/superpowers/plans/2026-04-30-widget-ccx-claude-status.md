# Qtile `CcxClaudeStatusWidget` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Qtile bar widget that mirrors the existing local `ClaudeStatusWidget` (process count + tokens today) — but for sessions running on the ccx EC2 box, fetched via SSH-poll. Left-clicking the widget opens a small rofi menu with two actions: **Attach via SSH** (jumps straight into the remote tmux) or **Open monitor TUI** (spawns a local terminal running `ccxctl monitor tui --source ccx`).

**Architecture:** New widget `CcxClaudeStatusWidget` lives in `ssdd__qtile_widgets/ssdd_qtile_widgets/ccx_claude_status.py`. Polls every 30 s and renders `⬡ {n}  ⟨⟩ {tokens}` in the bar. **Critically: it does NOT run an independent SSH poll.** Instead, `fetch_remote_sessions()` in `ccx.py` is wrapped with a module-level 25 s TTL cache so the existing `CcxStatusWidget` and the new `CcxClaudeStatusWidget` share one SSH round-trip per ~30 s window. Without this, two widgets in the bar would each fire their own ssh — 4 polls/min for the same data — and drift visibly between paints. The cache also gets `ssh -o ControlMaster=auto -o ControlPersist=120` for the same multiplexing reason as Plan A. Click handler runs `rofi -dmenu` with two items (`Attach via SSH` | `Open monitor TUI`) and dispatches to either `ssh -t … tmux attach` or `<terminal> -e ccxctl monitor tui --source ccx`. SSH params (`ssh_user`, `hostname`, `ssh_key`) read environment variable defaults so the widget remains portable when shared via dotfiles.

**Tech Stack:** qtile ≥ 0.23, qtile_extras (already a dep), Python 3.11+, `pytest` + `unittest.mock`. `psutil` is **not** needed — the local widget uses it to count processes; the ccx version doesn't (process count comes from the SSH JSON).

**Note (cross-repo):** This plan modifies `/home/david/Work/ssdd/ssdd__qtile_widgets/` only. **No changes to `sesio__ccx`.** The data-source API (`ccxctl session list --json`) already exists at `control-plane/ccx/sessions.py:285-292` — verified before writing this plan.

**Prereqs:** Plan A (`ccxctl monitor tui`) must merge first — the "Open monitor TUI" menu item invokes it. If Plan A isn't merged yet, the menu item still works (it spawns a terminal that prints "no such command"); but the widget itself functions without it. Plan A also fixes the `parse_jsonl_tokens_today()` cache-token undercount that this widget inherits via `ccxctl session list --json`. Without that fix the bar's `⟨⟩ {tokens}` number is off by 50–100×.

---

## File Structure

```
ssdd__qtile_widgets/
├── ssdd_qtile_widgets/
│   ├── __init__.py                     # MODIFY: re-export CcxClaudeStatusWidget
│   ├── ccx.py                          # MODIFY: add TTL-cached fetch_remote_sessions_cached() + ControlPersist
│   ├── claude_status.py                # READ-ONLY: source of format_tokens()
│   └── ccx_claude_status.py            # CREATE
├── tests/
│   ├── test_ccx.py                     # MODIFY: add cache-behavior tests (or create if absent)
│   └── test_ccx_claude_status.py       # CREATE
```

---

### Task 1: TTL-cached `fetch_remote_sessions_cached()` in `ccx.py` (PREREQ)

Without this, both widgets ssh independently. With this, whichever fires first pays the SSH cost; the other gets a free read for ~25 s. Also adds `ControlMaster/ControlPersist` to the underlying call.

**Files:**
- Modify: `/home/david/Work/ssdd/ssdd__qtile_widgets/ssdd_qtile_widgets/ccx.py`
- Test:   `/home/david/Work/ssdd/ssdd__qtile_widgets/tests/test_ccx.py` (create if absent)

- [ ] **Step 1: Failing tests**

`tests/test_ccx.py` — append (or create with the necessary imports):
```python
import time
from unittest.mock import patch, MagicMock

from ssdd_qtile_widgets import ccx as mod


def test_fetch_remote_sessions_cached_returns_same_object_within_ttl(monkeypatch):
    calls = {"n": 0}
    def fake_fetch(*a, **kw):
        calls["n"] += 1
        return [{"slug": "x"}]
    monkeypatch.setattr(mod, "fetch_remote_sessions", fake_fetch)
    mod._FETCH_CACHE.clear()  # test hook — see implementation note

    a = mod.fetch_remote_sessions_cached("u", "h", "/k", ttl=25.0)
    b = mod.fetch_remote_sessions_cached("u", "h", "/k", ttl=25.0)
    assert a == b
    assert calls["n"] == 1


def test_fetch_remote_sessions_cached_refetches_after_ttl(monkeypatch):
    calls = {"n": 0}
    def fake_fetch(*a, **kw):
        calls["n"] += 1
        return [{"slug": str(calls["n"])}]
    monkeypatch.setattr(mod, "fetch_remote_sessions", fake_fetch)
    mod._FETCH_CACHE.clear()

    fake_now = [1000.0]
    monkeypatch.setattr(mod, "_now", lambda: fake_now[0])

    mod.fetch_remote_sessions_cached("u", "h", "/k", ttl=25.0)
    fake_now[0] = 1030.0  # advance past TTL
    mod.fetch_remote_sessions_cached("u", "h", "/k", ttl=25.0)
    assert calls["n"] == 2


def test_fetch_remote_sessions_uses_controlpersist_options(monkeypatch):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return MagicMock(returncode=0, stdout="[]", stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)
    mod.fetch_remote_sessions("u", "h", "/k")
    flat = " ".join(captured["cmd"])
    assert "ControlMaster=auto" in flat
    assert "ControlPersist=" in flat
```

- [ ] **Step 2: Run; expect FAIL**

Run: `cd /home/david/Work/ssdd/ssdd__qtile_widgets && python3 -m pytest tests/test_ccx.py -v`
Expected: 3 FAIL.

- [ ] **Step 3: Add the cache + ControlPersist to `ccx.py`**

Edit `/home/david/Work/ssdd/ssdd__qtile_widgets/ssdd_qtile_widgets/ccx.py`:

(a) **Inside `fetch_remote_sessions()`**, replace the SSH options block (currently `-o IdentitiesOnly=yes` etc.) with:
```python
"-o", "IdentitiesOnly=yes",
"-o", "ConnectTimeout=5",
"-o", "BatchMode=yes",
"-o", "StrictHostKeyChecking=accept-new",
"-o", "ControlMaster=auto",
"-o", "ControlPath=~/.ssh/cm-%r@%h:%p",
"-o", "ControlPersist=120",
"-o", "ServerAliveInterval=30",
"-o", "ServerAliveCountMax=2",
```

(b) **Add a TTL cache** (right above the `try:` block where `libqtile` is imported, so it lives at module scope):
```python
import time as _time

_FETCH_CACHE: dict[tuple[str, str, str], tuple[float, list[dict] | None]] = {}


def _now() -> float:
    """Indirection so tests can freeze time."""
    return _time.monotonic()


def fetch_remote_sessions_cached(
    ssh_user: str, hostname: str, ssh_key: str, *, ttl: float = 25.0,
) -> list[dict] | None:
    """Cache wrapper around fetch_remote_sessions. Module-level cache shared
    by every widget instance, keyed by (ssh_user, hostname, ssh_key).

    Returns the cached value when fresh; refetches when stale or absent.
    `None` from the underlying fetch is also cached briefly so a transient
    failure doesn't spam SSH retries every poll.
    """
    key = (ssh_user, hostname, ssh_key)
    now = _now()
    cached = _FETCH_CACHE.get(key)
    if cached and (now - cached[0]) < ttl:
        return cached[1]
    rows = fetch_remote_sessions(ssh_user, hostname, ssh_key)
    _FETCH_CACHE[key] = (now, rows)
    return rows
```

(c) **Update `CcxStatusWidget.poll()` (around `ccx.py:188-191`)** to use the cached helper:
```python
if self._last_state == "running":
    rows = fetch_remote_sessions_cached(self.ssh_user, self.hostname, self.ssh_key)
    if rows is not None:
        self._cached_sessions = rows
else:
    self._cached_sessions = []
```
…and the same for the `RELOAD_LABEL` branch in `on_left_click()` (around `ccx.py:239-241`) — replace the direct call with `fetch_remote_sessions_cached(...)`. (For "↻ reload sessions" the user explicitly wants a fresh fetch; pass `ttl=0` so the cache is bypassed: `fetch_remote_sessions_cached(self.ssh_user, self.hostname, self.ssh_key, ttl=0)`.)

- [ ] **Step 4: Run; expect PASS**

Run: `python3 -m pytest tests/test_ccx.py -v`
Expected: 3 PASS. Run any pre-existing tests in this file too — they should still pass since `fetch_remote_sessions` keeps the same signature and behavior.

- [ ] **Step 5: Commit**

Use `/commit` (in the `ssdd__qtile_widgets` repo):
- subject: `feat(qtile widgets/ccx): TTL-cached fetch + ssh ControlPersist`
- body: motivation (sibling-widget dedup + sshd journald reduction).

---

### Task 2: Audit existing widget code (post-cache, no edits)

**Files (read-only):**
- `/home/david/Work/ssdd/ssdd__qtile_widgets/ssdd_qtile_widgets/ccx.py` — `fetch_remote_sessions()`, **`fetch_remote_sessions_cached()` you just added in Task 1**, `_rofi_pick()` (method of `CcxStatusWidget` — we'll reproduce the pattern), `_open_terminal()`
- `/home/david/Work/ssdd/ssdd__qtile_widgets/ssdd_qtile_widgets/claude_status.py` — `format_tokens()` is a free function; reuse via import. Also note the `_show_popup` dunstify pattern in case we want a popup later (not in this plan).
- `/home/david/Work/ssdd/ssdd__qtile_widgets/ssdd_qtile_widgets/__init__.py` — established re-export pattern (try/except ImportError per widget)
- `/home/david/Work/ssdd/ssdd__qtile_widgets/tests/` — existing test layout for one of the widgets

- [ ] **Step 1: Confirm test framework + layout**

Run: `ls /home/david/Work/ssdd/ssdd__qtile_widgets/tests/`
Expected: directory listing including any `test_*.py` files. Note whether tests are flat in `tests/` or nested by module.

- [ ] **Step 2: Confirm `format_tokens` is importable as a free function**

Run: `cd /home/david/Work/ssdd/ssdd__qtile_widgets && python3 -c "from ssdd_qtile_widgets.claude_status import format_tokens; print(format_tokens(12345))"`
Expected: `12k`. If it errors on `boto3` import-time, abort and reconsider — but `claude_status.py:13-15` makes `boto3` optional, so it should import fine.

---

### Task 3: Pure helpers — `summarize_remote()`

**Files:**
- Create: `/home/david/Work/ssdd/ssdd__qtile_widgets/ssdd_qtile_widgets/ccx_claude_status.py`
- Test:   `/home/david/Work/ssdd/ssdd__qtile_widgets/tests/test_ccx_claude_status.py`

The widget's `poll()` is hard to test (qtile glue). Pull the math into a free function so tests are pure dict-in / string-out.

- [ ] **Step 1: Failing test**

`tests/test_ccx_claude_status.py`:
```python
"""Tests for CcxClaudeStatusWidget — pure helpers only."""
from __future__ import annotations

from ssdd_qtile_widgets import ccx_claude_status as mod


def _row(slug, tin=0, tout=0):
    return {
        "agent": "claude", "slug": slug, "cwd": f"/p/{slug}",
        "uptime_seconds": 60.0, "agent_pid": 1, "claude_pid": 1,
        "tokens_today": {"input": tin, "output": tout},
        "usage_today": {"input": tin, "output": tout, "available": True},
    }


def test_summarize_remote_zero_when_none():
    text = mod.summarize_remote(None, prefix="ccx ")
    # None == unreachable / not-yet-fetched; render an explicit error glyph
    assert "ccx" in text and "!" in text


def test_summarize_remote_empty_list():
    text = mod.summarize_remote([], prefix="ccx ")
    assert "0" in text  # no sessions
    assert "⟨⟩ -" in text or "⟨⟩ 0" in text


def test_summarize_remote_aggregates_tokens():
    rows = [_row("a", tin=500, tout=250), _row("b", tin=1500, tout=750)]
    text = mod.summarize_remote(rows, prefix="ccx ")
    assert "ccx ⬡ 2" in text
    # 500+1500+250+750 = 3000 → "3k" via format_tokens
    assert "3k" in text or "3000" in text
```

- [ ] **Step 2: Run; expect FAIL**

Run: `cd /home/david/Work/ssdd/ssdd__qtile_widgets && python3 -m pytest tests/test_ccx_claude_status.py -v`
Expected: import error / module not found.

- [ ] **Step 3: Implement `summarize_remote()`**

`ssdd_qtile_widgets/ccx_claude_status.py`:
```python
"""Qtile bar widget for ccx-side Claude Code sessions.

Mirrors `ClaudeStatusWidget` (process count + today's token total) but the
data comes from the ccx EC2 box via `ccxctl session list --json` over SSH.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

# `fetch_remote_sessions_cached` is imported lazily inside `poll()` to keep
# this module importable in test environments where libqtile isn't installed.
from ssdd_qtile_widgets.claude_status import format_tokens


_GLYPH_ERROR = "!"
_GLYPH_PROC = "⬡"
_GLYPH_TOK = "⟨⟩"


def summarize_remote(rows: list[dict] | None, *, prefix: str = "ccx ") -> str:
    """Compose the bar string from a fetch_remote_sessions() result.

    `None` means the SSH fetch failed — render a single error glyph so the
    user can tell "ccx unreachable" from "ccx idle".
    """
    if rows is None:
        return f"{prefix}{_GLYPH_ERROR}".rstrip()

    count = len(rows)
    total = 0
    for r in rows:
        toks = r.get("tokens_today") or {}
        total += int(toks.get("input", 0)) + int(toks.get("output", 0))

    if count == 0:
        return f"{prefix}{_GLYPH_PROC} 0  {_GLYPH_TOK} -".rstrip()
    return f"{prefix}{_GLYPH_PROC} {count}  {_GLYPH_TOK} {format_tokens(total)}".rstrip()
```

- [ ] **Step 4: Run tests; expect PASS**

Run: `python3 -m pytest tests/test_ccx_claude_status.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

Use `/commit`: subject `feat(qtile widgets): add summarize_remote() helper`.

---

### Task 4: The widget class + click menu

**Files:**
- Modify: `/home/david/Work/ssdd/ssdd__qtile_widgets/ssdd_qtile_widgets/ccx_claude_status.py`
- Modify: `/home/david/Work/ssdd/ssdd__qtile_widgets/ssdd_qtile_widgets/__init__.py`
- Test:   `/home/david/Work/ssdd/ssdd__qtile_widgets/tests/test_ccx_claude_status.py`

The widget itself is mostly glue; tests target the click-dispatch logic.

- [ ] **Step 1: Failing tests for click dispatch**

Append to `tests/test_ccx_claude_status.py`:
```python
from unittest.mock import MagicMock, patch


def _make_widget():
    """Construct the widget without going through qtile's bar machinery."""
    from ssdd_qtile_widgets.ccx_claude_status import CcxClaudeStatusWidget
    # InLoopPollText constructors call into qtile; bypass by instantiating then
    # setting attributes directly.
    w = CcxClaudeStatusWidget.__new__(CcxClaudeStatusWidget)
    w.ssh_user = "david"
    w.hostname = "ccx.dsylla.sesio.io"
    w.ssh_key = "~/.ssh/keys/dsylla-ccx"
    w.terminal = "alacritty"
    w.ccxctl_path = "~/.local/bin/ccxctl"
    w._last_unreachable = False  # tracks healthy→unreachable transitions
    return w


def test_left_click_attach_invokes_ssh_with_tmux():
    w = _make_widget()
    with patch.object(w, "_rofi_pick", return_value="Attach via SSH"), \
         patch("subprocess.Popen") as popen:
        w.on_left_click()
    popen.assert_called_once()
    cmd_str = " ".join(popen.call_args.args[0])
    assert "ssh" in cmd_str
    assert "david@ccx.dsylla.sesio.io" in cmd_str
    assert "tmux" in cmd_str and "attach" in cmd_str


def test_left_click_tui_invokes_terminal_with_ccxctl():
    w = _make_widget()
    with patch.object(w, "_rofi_pick", return_value="Open monitor TUI"), \
         patch("subprocess.Popen") as popen:
        w.on_left_click()
    popen.assert_called_once()
    cmd = popen.call_args.args[0]
    assert cmd[0] == "alacritty"
    cmd_str = " ".join(cmd)
    assert "ccxctl" in cmd_str
    assert "monitor tui --source ccx" in cmd_str


def test_left_click_cancel_does_nothing():
    w = _make_widget()
    with patch.object(w, "_rofi_pick", return_value=None), \
         patch("subprocess.Popen") as popen:
        w.on_left_click()
    popen.assert_not_called()


def test_poll_fires_notify_send_only_on_first_failure(monkeypatch):
    """Healthy → unreachable transition fires a single notify-send;
    sustained unreachable does NOT spam toasts every poll."""
    w = _make_widget()
    # First poll: unreachable → expect one notify-send.
    monkeypatch.setattr(
        "ssdd_qtile_widgets.ccx.fetch_remote_sessions_cached",
        lambda *a, **kw: None,
    )
    with patch("subprocess.Popen") as popen:
        w.poll()
        # Second consecutive poll, still unreachable — must not toast again.
        w.poll()
    assert popen.call_count == 1
    args = popen.call_args.args[0]
    assert args[0] == "notify-send"


def test_poll_resets_failure_state_on_recovery(monkeypatch):
    """After a recovery, a subsequent failure must toast again."""
    w = _make_widget()
    state = {"rows": None}
    monkeypatch.setattr(
        "ssdd_qtile_widgets.ccx.fetch_remote_sessions_cached",
        lambda *a, **kw: state["rows"],
    )
    with patch("subprocess.Popen") as popen:
        w.poll()              # fail → toast
        state["rows"] = []    # recover
        w.poll()              # ok → no toast
        state["rows"] = None  # fail again
        w.poll()              # toast again
    assert popen.call_count == 2
```

- [ ] **Step 2: Run; expect FAIL**

Run: `python3 -m pytest tests/test_ccx_claude_status.py -k "click" -v`
Expected: 3 FAIL — class / methods don't exist.

- [ ] **Step 3: Implement the widget**

Append to `ssdd_qtile_widgets/ccx_claude_status.py`:
```python
ATTACH_LABEL = "Attach via SSH"
TUI_LABEL    = "Open monitor TUI"


try:
    from libqtile.widget import base
    from qtile_extras.widget import add_decoration_support

    @add_decoration_support
    class CcxClaudeStatusWidget(base.InLoopPollText):
        """Qtile bar widget: ccx-side Claude Code session count + tokens today.

        Left-click → 2-item rofi: Attach (ssh+tmux) | Open monitor TUI (local
        terminal running `ccxctl monitor tui --source ccx`). Right-click is
        unbound (the local-host ClaudeStatusWidget owns the popup).

        Defaults read CCX_SSH_USER / CCX_HOSTNAME / CCX_SSH_KEY from the
        environment when set, so the same dotfile remains portable across
        machines that point at a different ccx box (or none).
        """

        defaults = [
            ("update_interval", 30, "Poll interval in seconds"),
            ("prefix", "ccx ", "Bar prefix string"),
            ("ssh_user", os.environ.get("CCX_SSH_USER", "david"),
                "SSH user (env: CCX_SSH_USER)"),
            ("hostname", os.environ.get("CCX_HOSTNAME", "ccx.dsylla.sesio.io"),
                "ccx host (env: CCX_HOSTNAME)"),
            ("ssh_key", os.environ.get("CCX_SSH_KEY", "~/.ssh/keys/dsylla-ccx"),
                "SSH key path (env: CCX_SSH_KEY)"),
            ("terminal", "alacritty", "Terminal emulator for spawned commands"),
            ("ccxctl_path", "~/.local/bin/ccxctl",
                "Path to local ccxctl (qtile's PATH rarely includes ~/.local/bin)"),
        ]

        def __init__(self, **config):
            base.InLoopPollText.__init__(self, default_text=f"{_GLYPH_ERROR}", **config)
            self.add_defaults(CcxClaudeStatusWidget.defaults)
            self.add_callbacks({"Button1": self.on_left_click})
            # Track last-fetch state so we only `notify-send` on the
            # transition healthy → unreachable, not on every poll.
            self._last_unreachable: bool = False

        @property
        def _ccxctl(self) -> str:
            return os.path.expanduser(self.ccxctl_path)

        def poll(self) -> str:
            from ssdd_qtile_widgets.ccx import fetch_remote_sessions_cached
            rows = fetch_remote_sessions_cached(
                self.ssh_user, self.hostname, self.ssh_key,
            )
            currently_unreachable = rows is None
            if currently_unreachable and not self._last_unreachable:
                # First failure since the last good fetch — one-shot toast.
                try:
                    subprocess.Popen(
                        ["notify-send", "-u", "low",
                         "ccx widget", "ssh fetch failed — widget showing !"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                except FileNotFoundError:
                    pass
            self._last_unreachable = currently_unreachable
            return summarize_remote(rows, prefix=self.prefix)

        def _rofi_pick(self, prompt: str, items: list[str]) -> str | None:
            try:
                r = subprocess.run(
                    ["rofi", "-dmenu", "-i", "-p", prompt],
                    input="\n".join(items), text=True,
                    capture_output=True, check=False, timeout=120,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return None
            if r.returncode != 0:
                return None
            return r.stdout.strip() or None

        def on_left_click(self) -> None:
            choice = self._rofi_pick("ccx claude:", [ATTACH_LABEL, TUI_LABEL])
            if choice is None:
                return
            if choice == ATTACH_LABEL:
                # Direct attach to the shared `ccx` tmux session on the EC2 box.
                cmd = [
                    "ssh", "-i", os.path.expanduser(self.ssh_key),
                    "-o", "IdentitiesOnly=yes",
                    "-o", "StrictHostKeyChecking=accept-new",
                    "-t",
                    f"{self.ssh_user}@{self.hostname}",
                    "tmux", "attach-session", "-t", "ccx",
                ]
                # Wrap in a terminal so the click spawns a window.
                subprocess.Popen(
                    [self.terminal, "-e", *cmd],
                    start_new_session=True,
                )
            elif choice == TUI_LABEL:
                # Local TUI scoped to the ccx source — runs locally on the
                # laptop and SSH-polls the EC2 box on its own.
                subprocess.Popen(
                    [self.terminal, "-e", "bash", "-lc",
                     f"{self._ccxctl} monitor tui --source ccx"],
                    start_new_session=True,
                )

except ImportError:
    pass  # qtile / qtile_extras absent in test env
```

- [ ] **Step 4: Run; expect PASS**

Run: `python3 -m pytest tests/test_ccx_claude_status.py -v`
Expected: 8 PASS (3 from Task 3 + 3 click-dispatch tests + 2 notify-send tests).

- [ ] **Step 5: Re-export from `__init__.py`**

Edit `/home/david/Work/ssdd/ssdd__qtile_widgets/ssdd_qtile_widgets/__init__.py` — append after the existing `ClaudeStatusWidget` block:
```python
try:
    from ssdd_qtile_widgets.ccx_claude_status import CcxClaudeStatusWidget
except ImportError:
    pass  # type: ignore[assignment]
```
…and add `"CcxClaudeStatusWidget"` to `__all__`.

- [ ] **Step 6: Verify the import resolves**

Run: `cd /home/david/Work/ssdd/ssdd__qtile_widgets && python3 -c "from ssdd_qtile_widgets import CcxClaudeStatusWidget; print(CcxClaudeStatusWidget.__name__)"`
Expected: `CcxClaudeStatusWidget`. (If it raises ImportError because libqtile isn't installed in the venv, that's fine — the try/except in __init__ swallows that. But the per-module test imports we wrote DO import the class for click tests; those tests only construct the class via `__new__`, sidestepping the qtile glue. If even that fails because the file's `try: from libqtile…` block didn't define the class, install qtile in the test venv: `uv add --dev qtile qtile-extras`.)

- [ ] **Step 7: Commit**

Use `/commit`: subject `feat(qtile widgets): add CcxClaudeStatusWidget`, body listing the bar format (`ccx ⬡ N  ⟨⟩ Tk`) and the two click actions.

---

### Task 5: Wire the widget into the qtile bar config

**Files:**
- Read-only audit, then a manual config edit. The qtile bar config likely lives at `~/.config/qtile/config.py` (not tracked in either repo). This task is a manual step the user does on their laptop after the widget package is published — no automation here.

- [ ] **Step 1: Locate the qtile config**

Run: `ls ~/.config/qtile/`
Expected: `config.py` and possibly other modules. If absent, the user isn't running qtile; skip this task.

- [ ] **Step 2: Append the widget to the bar**

Manually add (next to the existing `ClaudeStatusWidget` reference):
```python
from ssdd_qtile_widgets import CcxClaudeStatusWidget
# … inside the bar widgets list …
CcxClaudeStatusWidget(),  # uses defaults — david@ccx.dsylla.sesio.io, etc.
```

- [ ] **Step 3: Reload qtile**

Run: `qtile cmd-obj -o cmd -f reload_config`
Expected: bar reloads without error. The new widget should appear showing `ccx ⬡ N  ⟨⟩ Tk`. If unreachable, it shows `ccx !`.

- [ ] **Step 4: Click-test both menu items**

Click the widget → rofi shows `Attach via SSH | Open monitor TUI`.
- Pick "Attach via SSH" → an alacritty window opens, attached to the remote `ccx` tmux session.
- Pick "Open monitor TUI" → an alacritty window opens running the live TUI scoped to the ccx source.

- [ ] **Step 5: No commit** (manual user-config step).

---

## Self-Review Checklist (run before declaring done)

- [ ] `python3 -m pytest tests/test_ccx.py -v` passes (3 cache tests from Task 1)
- [ ] `python3 -m pytest tests/test_ccx_claude_status.py -v` passes (8 tests from Tasks 3+4)
- [ ] `python3 -c "from ssdd_qtile_widgets import CcxClaudeStatusWidget"` works
- [ ] No new lint errors (`ruff check ssdd_qtile_widgets tests` if ruff is configured)
- [ ] Widget renders `ccx !` (single error glyph) when SSH is down — not a Python traceback in the qtile log
- [ ] `summarize_remote(None)` and `summarize_remote([])` produce visually distinct strings (one shows `!`, one shows `0`)
- [ ] **SSH cache is shared with `CcxStatusWidget`**: with the bar showing both widgets, only **one** `ssh` process per ~30 s window appears in `journalctl --user -t ssh` on the laptop (or `Accepted publickey` lines on the ccx side). Two would mean the cache isn't wired in — go back to Task 1 Step 3(c).
- [ ] **Widget gracefully no-ops when `rofi` is missing**: rename `/usr/bin/rofi` temporarily (`sudo mv /usr/bin/rofi /usr/bin/rofi.bak`), click the widget — nothing should crash; click does nothing. Restore.
- [ ] **Spawned terminal survives qtile restart**: open the TUI via the widget, then `qtile cmd-obj -o cmd -f restart`. The TUI window stays open (because of `start_new_session=True`).
- [ ] **`notify-send` fires on first failure transition only, not every poll**: simulate by temporarily making `fetch_remote_sessions_cached` return `None`; confirm one toast appears, then no more for the next 30 s; restore.
- [ ] **Rate limits are intentionally absent**: the local `ClaudeStatusWidget` shows `5h:N% / 7d:N%`; this widget does not. The ccx box doesn't run the statusline hook that writes `~/.cache/claude_status/state.json`. **Plan-C deferred** — not an oversight.
- [ ] No new dependency on `psutil` (the local `ClaudeStatusWidget` uses it; the ccx variant doesn't need to)
- [ ] No changes to `sesio__ccx` were required (`ccxctl session list --json` was already in place)
