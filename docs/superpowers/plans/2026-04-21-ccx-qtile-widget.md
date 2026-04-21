# ccx — Qtile Widget (CcxStatusWidget) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `CcxStatusWidget` to `ssdd__qtile_widgets` that shows the ccx instance state + uptime in the qtile bar; click-to-open the `ccxctl menu`.

**Architecture:** Mirror the existing `ClaudeSessionWidget` pattern — subclass `InLoopPollText`, poll every 30 s, render compact glyph+uptime, wrap AWS calls in try/except so a transient failure shows `ccx !` instead of crashing the bar. Conditional import in `__init__.py` so the widgets package still loads on machines without `boto3`.

**Tech Stack:** qtile ≥ 0.23, `boto3` (optional extra), Python 3.11+, `pytest` + `unittest.mock`.

**Note:** This plan touches a **different repo**: `/home/david/Work/ssdd/ssdd__qtile_widgets/`. The `sesio__ccx` repo is not modified by this plan.

**Prereq:** `ccx-terraform-main` plan applied (`~/.config/ccx/instance_id` exists). `ccx-control-plane` plan applied (`ccxctl` on PATH for click handler).

---

## File Structure

```
ssdd__qtile_widgets/
├── ssdd_qtile_widgets/
│   ├── __init__.py             # MODIFY: conditional import of CcxStatusWidget
│   └── ccx.py                  # CREATE
├── tests/
│   └── test_ccx.py             # CREATE
└── pyproject.toml              # MODIFY: add boto3 as optional extra
```

---

### Task 0: Read existing pattern

**Files (read-only):**
- `/home/david/Work/ssdd/ssdd__qtile_widgets/ssdd_qtile_widgets/__init__.py`
- `/home/david/Work/ssdd/ssdd__qtile_widgets/ssdd_qtile_widgets/claude_session.py` (or the file defining `ClaudeSessionWidget`)
- `/home/david/Work/ssdd/ssdd__qtile_widgets/pyproject.toml`
- `/home/david/Work/ssdd/ssdd__qtile_widgets/tests/`

- [ ] **Step 1: Locate files**

Run: `ls /home/david/Work/ssdd/ssdd__qtile_widgets/ssdd_qtile_widgets/ /home/david/Work/ssdd/ssdd__qtile_widgets/tests/`
Expected: list of widget modules + test files. If no file named `claude_session*` exists, search: `grep -rl 'ClaudeSessionWidget\|InLoopPollText' /home/david/Work/ssdd/ssdd__qtile_widgets/` — that's the reference.

- [ ] **Step 2: Read `ClaudeSessionWidget`**

Read the reference widget. Record:
- Class signature, base class, `defaults = [...]` structure
- Polling mechanism (`poll()`, `update_interval`)
- Click/mouse handling (`mouse_callbacks` vs. `button_press`)
- Error handling pattern (try/except around AWS/IO, fallback text)

The new widget must match these patterns exactly so the two widgets feel consistent in the bar.

- [ ] **Step 3: Read `__init__.py`**

Note the existing conditional-import pattern. Per the spec: "The import is guarded inside a try/except ImportError in ssdd_qtile_widgets/__init__.py, consistent with the existing pattern." — follow whatever shape that file uses.

- [ ] **Step 4: Read `pyproject.toml`**

Note whether `[project.optional-dependencies]` is present. Note the package's Python version constraint.

---

### Task 1: Add `boto3` as optional extra

**Files:**
- Modify: `/home/david/Work/ssdd/ssdd__qtile_widgets/pyproject.toml`

- [ ] **Step 1: Edit pyproject.toml**

If `[project.optional-dependencies]` exists, add:

```toml
[project.optional-dependencies]
aws = ["boto3>=1.34"]
```

If not, introduce the block. Preserve any existing extras.

- [ ] **Step 2: Sync the extra + dev deps**

Run: `cd /home/david/Work/ssdd/ssdd__qtile_widgets && /usr/bin/uv sync --extra aws --group dev`
Expected: boto3 installed, tests can be run.

---

### Task 2: Write failing tests

**Files:**
- Create: `/home/david/Work/ssdd/ssdd__qtile_widgets/tests/test_ccx.py`

- [ ] **Step 1: Write tests**

File `/home/david/Work/ssdd/ssdd__qtile_widgets/tests/test_ccx.py`:

```python
from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def instance_id_file(tmp_path: Path) -> str:
    p = tmp_path / "instance_id"
    p.write_text("i-deadbeef1234\n")
    return str(p)


def _widget(instance_id_file: str, **overrides):
    from ssdd_qtile_widgets.ccx import CcxStatusWidget
    defaults = dict(
        prefix="ccx ",
        update_interval=30,
        aws_profile="sesio__euwest1",
        region="eu-west-1",
        instance_id_file=instance_id_file,
    )
    defaults.update(overrides)
    return CcxStatusWidget(**defaults)


def _mock_describe(monkey_boto3, instance_payload):
    monkey_boto3.session.Session.return_value.client.return_value.describe_instances.return_value = {
        "Reservations": [{"Instances": [instance_payload]}]
    }


def test_render_running_with_uptime(instance_id_file):
    with patch("ssdd_qtile_widgets.ccx.boto3") as m:
        _mock_describe(m, {
            "State": {"Name": "running"},
            "LaunchTime": dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2, minutes=14),
            "InstanceType": "t4g.xlarge",
            "PublicIpAddress": "1.2.3.4",
        })
        w = _widget(instance_id_file)
        text = w.poll()
    assert text.startswith("ccx ● ")
    # 1-minute tolerance for test-wall-clock drift
    assert "2h14m" in text or "2h13m" in text


def test_render_stopped(instance_id_file):
    with patch("ssdd_qtile_widgets.ccx.boto3") as m:
        _mock_describe(m, {"State": {"Name": "stopped"}, "InstanceType": "t4g.xlarge"})
        w = _widget(instance_id_file)
        assert w.poll() == "ccx ○"


def test_render_pending(instance_id_file):
    with patch("ssdd_qtile_widgets.ccx.boto3") as m:
        _mock_describe(m, {"State": {"Name": "pending"}, "InstanceType": "t4g.xlarge"})
        w = _widget(instance_id_file)
        assert w.poll() == "ccx ◐"


def test_render_stopping(instance_id_file):
    with patch("ssdd_qtile_widgets.ccx.boto3") as m:
        _mock_describe(m, {"State": {"Name": "stopping"}, "InstanceType": "t4g.xlarge"})
        w = _widget(instance_id_file)
        assert w.poll() == "ccx ◑"


def test_render_error_on_aws_exception(instance_id_file):
    with patch("ssdd_qtile_widgets.ccx.boto3") as m:
        m.session.Session.return_value.client.return_value.describe_instances.side_effect = RuntimeError("boom")
        w = _widget(instance_id_file)
        assert w.poll() == "ccx !"


def test_render_error_on_missing_instance_id_file(tmp_path: Path):
    w = _widget(str(tmp_path / "does-not-exist"))
    assert w.poll() == "ccx !"


def test_force_update_calls_poll_then_update(instance_id_file):
    w = _widget(instance_id_file)
    w.poll = MagicMock(return_value="ccx ○")
    w.update = MagicMock()
    w.force_update()
    w.poll.assert_called_once()
    w.update.assert_called_once_with("ccx ○")
```

- [ ] **Step 2: Run — expect failure**

Run: `cd /home/david/Work/ssdd/ssdd__qtile_widgets && /usr/bin/uv run pytest tests/test_ccx.py -v`
Expected: `ModuleNotFoundError: No module named 'ssdd_qtile_widgets.ccx'` — all 7 tests error out. This confirms the tests exercise the missing implementation.

---

### Task 3: Implement `CcxStatusWidget`

**Files:**
- Create: `/home/david/Work/ssdd/ssdd__qtile_widgets/ssdd_qtile_widgets/ccx.py`

- [ ] **Step 1: Write the module**

File `/home/david/Work/ssdd/ssdd__qtile_widgets/ssdd_qtile_widgets/ccx.py`:

```python
"""CcxStatusWidget — qtile bar widget for the ccx coding station."""
from __future__ import annotations

import datetime as _dt
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from libqtile.widget.base import InLoopPollText

try:
    import boto3
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

_GLYPH_RUNNING  = "●"
_GLYPH_STOPPED  = "○"
_GLYPH_PENDING  = "◐"
_GLYPH_STOPPING = "◑"
_GLYPH_ERROR    = "!"


def _uptime_str(launch: _dt.datetime) -> str:
    secs = int((_dt.datetime.now(_dt.timezone.utc) - launch).total_seconds())
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"


class CcxStatusWidget(InLoopPollText):
    """Display ccx EC2 state + uptime; click fires `ccxctl menu`."""

    defaults: list[tuple[str, Any, str]] = [
        ("prefix",           "ccx ",                        "Text prefix."),
        ("update_interval",  30,                            "Poll interval (s)."),
        ("aws_profile",      "sesio__euwest1",              "AWS profile to use."),
        ("region",           "eu-west-1",                   "AWS region."),
        ("instance_id_file", "~/.config/ccx/instance_id",   "Path to instance_id."),
        ("ccxctl_path",      "ccxctl",                      "ccxctl binary on PATH."),
        ("log_file",         "~/.cache/ccx/widget.log",     "Log file for widget errors."),
    ]

    def __init__(self, **config: Any) -> None:
        super().__init__("", **config)
        self.add_defaults(CcxStatusWidget.defaults)
        self.mouse_callbacks = {"Button1": self._on_click}

    # --- qtile lifecycle ---------------------------------------------------
    def poll(self) -> str:
        try:
            return self._render()
        except Exception:
            self._log_exc()
            return f"{self.prefix}{_GLYPH_ERROR}"

    def force_update(self) -> None:
        """Called by ccxctl over `qtile cmd-obj` after state-changing actions."""
        self.update(self.poll())

    # --- internals ---------------------------------------------------------
    def _render(self) -> str:
        iid = self._read_instance_id()
        inst = self._describe_instance(iid)
        state = inst["State"]["Name"]

        if state == "running":
            launch = inst.get("LaunchTime")
            if launch is None:
                return f"{self.prefix}{_GLYPH_RUNNING}"
            return f"{self.prefix}{_GLYPH_RUNNING} {_uptime_str(launch)}"
        if state == "stopped":
            return f"{self.prefix}{_GLYPH_STOPPED}"
        if state in ("pending", "starting"):
            return f"{self.prefix}{_GLYPH_PENDING}"
        if state in ("stopping", "shutting-down"):
            return f"{self.prefix}{_GLYPH_STOPPING}"
        return f"{self.prefix}{_GLYPH_ERROR}"

    def _read_instance_id(self) -> str:
        path = Path(os.path.expanduser(self.instance_id_file))
        return path.read_text().strip()

    def _describe_instance(self, iid: str) -> dict[str, Any]:
        if boto3 is None:
            raise RuntimeError("boto3 not installed — install the 'aws' extra")
        session = boto3.session.Session(profile_name=self.aws_profile, region_name=self.region)
        ec2 = session.client("ec2")
        resp = ec2.describe_instances(InstanceIds=[iid])
        return resp["Reservations"][0]["Instances"][0]

    def _on_click(self) -> None:
        try:
            subprocess.Popen([self.ccxctl_path, "menu"])
        except Exception:
            self._log_exc()

    def _log_exc(self) -> None:
        log_path = Path(os.path.expanduser(self.log_file))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as f:
            import traceback
            f.write(f"[{_dt.datetime.now().isoformat()}]\n")
            traceback.print_exc(file=f)
            f.write("\n")
```

- [ ] **Step 2: Run tests — expect pass**

Run: `cd /home/david/Work/ssdd/ssdd__qtile_widgets && /usr/bin/uv run pytest tests/test_ccx.py -v`
Expected: `7 passed`.

---

### Task 4: Conditional export from package

**Files:**
- Modify: `/home/david/Work/ssdd/ssdd__qtile_widgets/ssdd_qtile_widgets/__init__.py`

- [ ] **Step 1: Edit `__init__.py` following existing pattern**

Use whatever shape the file already has for optional widgets (observed in Task 0 step 3). The append should look like:

```python
try:
    from .ccx import CcxStatusWidget  # noqa: F401
    __all__ = [*(__all__ if "__all__" in globals() else []), "CcxStatusWidget"]
except ImportError:
    pass
```

Do not unilaterally rewrite the file's import style — match the existing precedent.

- [ ] **Step 2: Smoke test import**

Run: `cd /home/david/Work/ssdd/ssdd__qtile_widgets && /usr/bin/uv run python -c "from ssdd_qtile_widgets import CcxStatusWidget; print(CcxStatusWidget)"`
Expected: prints `<class 'ssdd_qtile_widgets.ccx.CcxStatusWidget'>`.

---

### Task 5: Full test suite regression

- [ ] **Step 1: Run everything**

Run: `cd /home/david/Work/ssdd/ssdd__qtile_widgets && /usr/bin/uv run pytest -v`
Expected: all tests pass — the new 7 plus whatever pre-existing widget tests live in the repo.

---

### Task 6: Live install — add to qtile bar

- [ ] **Step 1: Install the updated package into qtile's Python**

Check the existing install mechanism (likely `pip install -e .` into qtile's venv, or a system-level install). Use the same one. If unsure, the package README or the user will clarify.

- [ ] **Step 2: Edit qtile `config.py` to add the widget**

In the user's qtile `config.py` (outside this repo), add inside the bar's widget list:

```python
from ssdd_qtile_widgets import CcxStatusWidget
# ...
CcxStatusWidget(name="ccx_status"),
```

The `name="ccx_status"` must match `CCX_WIDGET_NAME` default in `ccxctl` so `qtile cmd-obj -o widget ccx_status -f force_update` finds it.

- [ ] **Step 3: Reload qtile**

Run: `qtile cmd-obj -o cmd -f reload_config`
Expected: qtile reloads without error; the bar now includes a `ccx <glyph> …` segment.

- [ ] **Step 4: Visual verify**

- Bar shows expected glyph for current state (`ccx ○` stopped, `ccx ●` running).
- Click the widget → dmenu pops with state-aware choices.
- Run `ccxctl start`; visually confirm the widget flips through `◐ → ●` — either via `force_update` (called inside `ccxctl start`) or the 30 s poll.
- Run `ccxctl stop`; visually confirm the widget flips `◑ → ○`.

---

### Task 7: Commit (in `ssdd__qtile_widgets` repo)

- [ ] **Step 1: Review**

Run: `cd /home/david/Work/ssdd/ssdd__qtile_widgets && git status && git diff --cached --stat`
Expected: new `ssdd_qtile_widgets/ccx.py`, new `tests/test_ccx.py`, modified `__init__.py`, modified `pyproject.toml`.

- [ ] **Step 2: Commit**

Invoke `/commit`. Suggested message: `feat(ccx): add CcxStatusWidget for ccx coding station`.

---

## Done when

1. `pytest -v` in `ssdd__qtile_widgets` is green (7 new tests + existing).
2. Qtile bar shows `ccx <glyph> [uptime]` reflecting the live instance state.
3. Clicking the widget opens the `ccxctl menu` dmenu.
4. `ccxctl start` / `stop` visually flips the widget within a few seconds (via `force_update`, not just the 30 s poll).
