---
name: plan-resume
description: Resume interrupted plan work by reading ~/org/plans.org state. Shows where you left off and offers to restart superpowers execution. Use when user says "resume plan", "where was I", "continue plan", "pick up where I left off", or at session start when resuming work.
tools: Read, Bash
args: (none) | <plan-name>
---

# Plan Resume

Resume interrupted work by reading the persistent state in `~/org/plans.org`.
Bridges from org-mode tracking back into the superpowers execution workflow.

## Arguments
- (none) - Show all IN-PROGRESS plans and ask which to resume
- `<plan-name>` - Resume a specific plan by name match

## Instructions

### Step 1: Read org state

1. **Read `~/org/plans.org`.**

2. **Find IN-PROGRESS plans** (top-level headings with `IN-PROGRESS` state).

3. **For each, extract:**
   - Title, priority, tags
   - `:SOURCE:` property (path to the plan file)
   - `:PROJECT:` property
   - Total tasks and how many are DONE
   - Currently clocked-in task (open CLOCK entry, if any)
   - Checkbox progress within that task

### Step 2: Present status

If multiple IN-PROGRESS plans, show them all and ask which to resume:

```
## Plans in progress

1. [#B] IoT Core to S3 production (sesio__iaas) - 2/6 tasks done
   → Currently on: Task 3 - Extend iot-topic-rule module [0/6]

2. [#C] Add Python/uv to sesio__apt (sesio__apt) - 0/4 tasks done
   → Currently on: Task 1 - Create pyproject.toml [0/2]

Which plan to resume? (or "all" for dashboard only)
```

If only one IN-PROGRESS plan, auto-select it.

If `<plan-name>` argument provided, match it (case-insensitive substring).

### Step 3: Load the plan file

1. **Read the `:SOURCE:` plan file.** If the file doesn't exist, warn the user
   and ask for the correct path.

2. **Find the next incomplete task** — the first `** TODO` or `** IN-PROGRESS`
   sub-heading in the org entry.

3. **Find the corresponding task section** in the plan file
   (match by task number: `## Task N` or `### Task N`).

4. **Within that task, find the next incomplete step** — match org checkboxes
   to plan steps. The first unchecked `- [ ]` corresponds to the next step to execute.

### Step 4: Present resume point

```
## Resuming: IoT Core to S3 production

**Plan file:** ~/Work/sesio/sesio__iaas/docs/plans/2026-02-16-iot-to-s3-production-implementation.md
**Progress:** 2/6 tasks complete
**Next:** Task 3, Step 1 - Add firehose_stream_name and firehose_stream_arn variables

Two options:
1. **Continue with executing-plans** — batch execution with checkpoints
2. **Continue with subagent-driven-development** — fresh subagent per task, same session
```

### Step 5: Hand off to execution skill

Based on user choice:

**If executing-plans:**
- Announce: "Resuming with executing-plans skill."
- The plan file and starting task are already identified.
- Invoke `superpowers:executing-plans` — it will read the plan and create TodoWrite
  starting from the next incomplete task.

**If subagent-driven-development:**
- Announce: "Resuming with subagent-driven-development skill."
- Invoke `superpowers:subagent-driven-development` — provide the plan file path
  and note which tasks are already complete.

### Step 6: Verify clock state

Before handing off, check if the org plan has a stale open CLOCK entry
(clocked in but no activity). If so:

1. Close the stale clock with the current timestamp.
2. Open a fresh clock on the task being resumed.

This keeps the clocked time accurate.

## Edge Cases

- **Plan file moved or deleted:** Ask the user for the new path. Update the `:SOURCE:` property.
- **All tasks done but plan not marked DONE:** Suggest running `/plan-tracker done`.
- **No IN-PROGRESS plans:** Check for TODO plans and offer to start one, or suggest `/plan-agenda`.
- **Plan was DEFERRED:** Show it separately and ask if the user wants to un-defer it first.
