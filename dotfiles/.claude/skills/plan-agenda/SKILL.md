---
name: plan-agenda
description: Review, prioritize, and schedule Claude Code plans using org-mode. Shows dashboard, re-prioritizes plans, sets SCHEDULED/DEADLINE dates, manages backlog. Use when user says "plan agenda", "plan priority", "review plans", "what should I work on", "save plan for later", "backlog".
tools: Read, Write, Edit
args: review | schedule | status | backlog
---

# Plan Agenda

Review, prioritize, and schedule plans across `~/org/plans.org` and `~/org/plans-backlog.org`.

## Arguments
- `status` (default) - Quick dashboard of active plans, clocked time, overdue items
- `review` - Review all plans and backlog, re-prioritize
- `schedule` - Set SCHEDULED/DEADLINE dates on a plan
- `backlog` - Add a new idea to the backlog or promote one to active

## Org Files

- Active plans: `~/org/plans.org`
- Backlog: `~/org/plans-backlog.org`

## Instructions

### `status` - Dashboard (default)

1. **Read both org files.**

2. **Parse and display:**

   **Active Plans:**
   For each non-DONE/CANCELLED top-level heading in `plans.org`:
   - Title, priority, tags
   - Task progress: count DONE tasks / total tasks (e.g. `2/5 tasks`)
   - Step progress: count checked `[X]` / total checkboxes across all tasks (e.g. `8/15 steps`)
   - State: TODO, IN-PROGRESS, or DEFERRED
   - SCHEDULED/DEADLINE if set
   - Currently clocked task with its checkbox progress (e.g. `Task 3 [1/4]`)
   - Total clocked time (sum all CLOCK durations under this heading)

   **Overdue:**
   Any plan with a DEADLINE in the past.

   **Upcoming (next 7 days):**
   Plans with SCHEDULED or DEADLINE within the next 7 days.

   **Backlog count:**
   Number of items in `plans-backlog.org`.

3. **Format as a concise table or list.** Example:

   ```
   ## Plan Dashboard

   ### Active (3)
   | Priority | Plan                        | Tasks | Steps  | Clocked | Status      |
   |----------|-----------------------------|-------|--------|---------|-------------|
   | [#A]     | Fix auth bug                | 2/4   | 8/15   | 1:15    | IN-PROGRESS |
   | [#B]     | Org planning workflow       | 0/6   | 0/20   | 0:00    | TODO        |
   | [#C]     | Update docs                 | 3/3   | 9/9    | 0:45    | DEFERRED    |

   ### Overdue: None
   ### Next 7 days: "Fix auth bug" due Wed
   ### Backlog: 5 ideas
   ```

4. **Suggest actions:** "Use `/plan-agenda review` to re-prioritize or `/plan-agenda schedule` to set dates."

### `review` - Re-prioritize Plans

1. **Read both org files.**

2. **List all plans** (active + backlog) with current priority, state, and effort:

   ```
   Active:
   1. [#A] Fix auth bug (IN-PROGRESS, 1h effort)
   2. [#B] Org planning workflow (TODO, 2h effort)
   3. [#C] Update docs (DEFERRED, 30min effort)

   Backlog:
   4. [#C] IDEA: MCP for home automation (4h effort)
   5. [#C] IDEA: Refactor alert service (2h effort)
   ```

3. **Ask the user** what to change:
   - Re-prioritize items? (set [#A], [#B], or [#C])
   - Promote backlog items to active?
   - Drop backlog items?

4. **Apply changes:**
   - Priority: Replace `[#X]` in the heading
   - Promote: Move heading from `plans-backlog.org` to `plans.org`, change state from IDEA/SOMEDAY to TODO
   - Drop: Change state to DROPPED in backlog file

### `schedule` - Set Dates

1. **Read `~/org/plans.org`.**

2. **Show active plans.** Ask which plan to schedule.

3. **Ask for dates:**
   - SCHEDULED: When to start working on it (shows in agenda on that day)
   - DEADLINE: When it must be done by (agenda shows warnings as it approaches)
   - Both are optional.

4. **Add or update the date lines** right after the properties drawer:

   ```org
   * TODO [#B] Plan title  :tags:
     :PROPERTIES:
     ...
     :END:
     SCHEDULED: <2026-02-20 Fri>
     DEADLINE: <2026-02-28 Sat>
   ```

   Date format: `<YYYY-MM-DD Day>`

   Use bash to help format: `date -d "next friday" '+<%Y-%m-%d %a>'`

5. **Confirm** the scheduling to the user.

### `backlog` - Manage Backlog

1. **Ask the user** what to do:
   - Add a new idea
   - Review existing backlog (delegates to `review`)

2. **For new ideas, gather:**
   - Title (short description)
   - Priority (default C)
   - Effort estimate (optional)
   - Tags (optional)
   - Brief description (1-2 lines)

3. **Read `~/org/plans-backlog.org`.**

4. **Append a new heading:**

   ```org

   * IDEA [#C] New idea title  :tag:
     :PROPERTIES:
     :CREATED:  [YYYY-MM-DD Day]
     :EFFORT:   4h
     :END:
     Brief description of the idea.
   ```

5. **Confirm** to the user.

## Parsing Clock Durations

To sum clocked time for a plan, find all lines matching:
```
CLOCK: [...]--[...] =>  H:MM
```

Extract `H:MM` values and sum them. Report as `H:MM` total.

## Tip for Users

After using `/plan-agenda`, open Emacs and press `SPC o A` to see the native org-agenda views:
- `a` - Weekly/daily agenda
- `t` - All TODOs
- `m` - Match by tags
- `s` - Search
