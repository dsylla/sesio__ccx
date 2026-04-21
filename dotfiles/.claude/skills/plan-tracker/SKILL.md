---
name: plan-tracker
description: Track Claude Code plans in org-mode. Creates, updates, and completes plan entries in ~/org/plans.org with task-level clocking. Use when user says "track plan", "update plan", "plan done", "clock in", "clock out", or after completing brainstorming/writing-plans workflows. Use from-brainstorming when brainstorming starts. Use from-superpowers after writing-plans saves a plan file.
tools: Read, Write, Edit
args: create | from-brainstorming | from-superpowers | progress | done | defer
---

# Plan Tracker

Track Claude Code plans in `~/org/plans.org` with TODO states, properties, and org-mode clock entries.

## Arguments
- `create` - Register a new plan from a design doc or implementation plan
- `from-brainstorming` - Create lightweight entry when brainstorming starts (captures design time)
- `from-superpowers` - Auto-create/update from a superpowers plan file (no interactive questions)
- `progress` - Update task states and clock entries for an active plan
- `progress tasks 1-3 done` - Batch-complete tasks 1 through 3 (for superpowers execution sync)
- `done` - Mark a plan as completed, fill in SUMMARY
- `defer` - Set a plan to DEFERRED with a note
- (none) - Auto-detect: show active plans and ask what to update

## Org File

**Location:** `~/org/plans.org`

**Header (must exist at top of file):**
```org
#+TITLE: Claude Code Plans
#+CATEGORY: plans
#+TODO: TODO IN-PROGRESS DEFERRED | DONE CANCELLED
```

## Clock Invariants — MANDATORY

**Every task MUST have a LOGBOOK with clock entries. No exceptions.**

These invariants apply to ALL operations (`create`, `from-superpowers`, `progress`, `done`, `defer`, batch completion):

1. **IN-PROGRESS task → must have an open CLOCK** (no `--` end time)
2. **DONE task → must have a closed CLOCK** (with `--` end time and `=> H:MM` duration)
3. **Transitioning TODO → IN-PROGRESS** → add LOGBOOK with open CLOCK if missing
4. **Transitioning IN-PROGRESS → DONE** → close the open CLOCK with current timestamp
5. **Batch completion** → each task in the range gets a closed CLOCK entry. If a task has no LOGBOOK, add one with a reasonable estimated range (split the elapsed time evenly across tasks).
6. **Defer/Done on plan** → close ALL open clocks (project-level and task-level)

**Validation on read:** When reading plans.org for any operation, scan for DONE tasks missing LOGBOOK entries. If found, warn the user: "Task N has no clock entry — add estimated times?"

**Clock structure for tasks:**
```org
** IN-PROGRESS Task N: Title [0/3]
   :LOGBOOK:
   CLOCK: [YYYY-MM-DD Day HH:MM]
   :END:
   Description.
   - [ ] Step 1
```

```org
** DONE Task N: Title [3/3]
   :LOGBOOK:
   CLOCK: [YYYY-MM-DD Day HH:MM]--[YYYY-MM-DD Day HH:MM] =>  H:MM
   :END:
   Description.
   - [X] Step 1
```

**Why this matters:** Clock data enables time analysis across projects. Missing clocks make the org file unreliable for tracking where time goes. This is non-negotiable.

## Instructions

### `create` - Register a New Plan

1. **Gather plan info.** Ask the user for (or infer from context):
   - Plan title (short, descriptive)
   - Project name (for :PROJECT: property)
   - Source file path (design doc or plan file, for :SOURCE: property)
   - Effort estimate (e.g. "2h", "1d", for :EFFORT: property)
   - Tags (e.g. `:sesio:`, `:personal:`, `:claude:`)
   - Priority (A/B/C, default B)
   - Sub-tasks (from implementation plan steps, or ask user)

2. **Read `~/org/plans.org`** to get current contents.

3. **Read the SOURCE plan file** if one exists. Extract all tasks and their steps to build
   a detailed org sub-tree. Each task in the plan becomes a `**` sub-heading, and each step
   within a task becomes an org checkbox (`- [ ]`).

4. **Append a new top-level heading** at the end of the file using this structure:

```org

* TODO [#B] Plan title here  :tag1:tag2:
  :PROPERTIES:
  :CREATED:  [YYYY-MM-DD Day]
  :SOURCE:   /path/to/design-doc.md
  :PROJECT:  project-name
  :EFFORT:   2h
  :SUMMARY:
  :END:
** TODO Task 1: Create the widget [0/3]
   Short description of what this task accomplishes.
   - [ ] Write failing test for widget rendering
   - [ ] Implement widget component
   - [ ] Run tests and verify
** TODO Task 2: Add API endpoint [0/4]
   Wire up the REST endpoint and validation.
   - [ ] Define route in router.py
   - [ ] Implement handler with input validation
   - [ ] Write integration test
   - [ ] Commit
** TODO Task 3: Update docs [0/2]
   Update README and API docs.
   - [ ] Add widget section to README
   - [ ] Update API reference
```

   **Structure rules:**
   - `**` headings = tasks (high-level units of work, with TODO state)
   - `[0/N]` = checkbox cookie after task title (org auto-updates this count)
   - Description body = 1-2 line summary under the heading (before checkboxes)
   - `- [ ]` = individual steps/actions within the task (checkboxes)
   - `- [X]` = completed step

   Use the current date/day for CREATED. Format: `[2026-02-17 Tue]`.

   **Extracting from plan files:** If the SOURCE is an implementation plan, read it and map:
   - Each `## Task N` or `### Task N` section → `** TODO Task N: title [0/M]`
   - Each `**Step N**` within a task → `- [ ] Step description`
   - The task's goal/first paragraph → description body

5. **Clock in the first sub-task.** Add a LOGBOOK with an open clock entry:

```org
** TODO Task 1: Create the widget [0/3]
   :LOGBOOK:
   CLOCK: [YYYY-MM-DD Day HH:MM]
   :END:
   Short description of what this task accomplishes.
   - [ ] Write failing test for widget rendering
   - [ ] Implement widget component
   - [ ] Run tests and verify
```

   Use the current timestamp. An open CLOCK entry (no `--` end time) means "currently clocked in".

5. **Confirm** to the user what was created.

### `from-brainstorming` - Create Lightweight Entry at Brainstorming Start

Use this when the `brainstorming` skill begins. Creates a minimal org entry to
capture design/exploration time before an implementation plan exists.

1. **Infer context from the conversation:**
   - **Title:** Use the topic being brainstormed (e.g. "S3 file uploader design")
   - **Project name:** from the current git repo or working directory
   - **Tags:** derive from project name

2. **Read `~/org/plans.org`** to get current contents.

3. **Append a minimal top-level heading:**

   ```org

   * TODO [#B] Plan title here  :tag1:tag2:
     :PROPERTIES:
     :CREATED:  [YYYY-MM-DD Day]
     :PROJECT:  project-name
     :EFFORT:
     :SUMMARY:
     :END:
     :LOGBOOK:
     CLOCK: [YYYY-MM-DD Day HH:MM]
     :END:
   ```

   No sub-tasks yet — those come later when `from-superpowers` runs.
   The open CLOCK captures brainstorming time.

4. **Confirm** to the user: "Tracking brainstorming for [title]. Clock started."

### `from-superpowers` - Auto-Create from Superpowers Plan File

Use this after the `writing-plans` skill saves a plan file. Extracts all metadata
from the plan file itself — no interactive questions needed.

1. **Identify the plan file.** The argument should be the path to the plan file
   (e.g. `docs/plans/2026-02-17-feature-name.md`). If not provided, check the
   current conversation context for the most recently saved plan file path.

2. **Read the plan file.** Extract:
   - **Title:** From the `# ...` H1 heading (strip " Implementation Plan" suffix if present)
   - **Goal:** From the `**Goal:**` line
   - **Tech Stack:** From the `**Tech Stack:**` line (use for tags)
   - **Tasks:** Each `## Task N:` or `### Task N:` section becomes an org sub-heading.
     Within each task, each `**Step N:**` becomes a checkbox.

3. **Infer metadata from context:**
   - **Project name:** basename of the git repo (run `git rev-parse --show-toplevel` and take the last path component), or infer from the plan file path
   - **Tags:** derive from project name (e.g. `sesio__greengrass` → `:sesio:greengrass:`)
   - **Priority:** default B
   - **Effort:** count tasks × 30min as rough estimate, or use the plan's estimate if present
   - **Source:** absolute path to the plan file

4. **Read `~/org/plans.org`** to get current contents.

5. **Check for existing brainstorming entry.** Search for a top-level heading that
   matches the project and topic (from a prior `from-brainstorming` call). Match by:
   - Same `:PROJECT:` property, AND
   - Title contains similar keywords (fuzzy match on key words)

   **If found:** Update the existing entry in-place:
   - Close any open CLOCK entry (this ends the brainstorming clock)
   - Add `:SOURCE:` property with the plan file path
   - Add `:EFFORT:` if not already set
   - Add sub-task headings below the existing heading
   - Clock in the first task

   **If not found:** Create a new entry (proceed to step 6).

6. **Append or update the org entry.** Follow the same structure as `create`, using the extracted data.
   Map plan tasks and steps like this:

   Plan file:
   ```markdown
   ## Task 1: Create the widget

   **Step 1: Write the failing test**
   ...
   **Step 2: Run test to verify it fails**
   ...
   **Step 3: Implement**
   ...
   ```

   Becomes:
   ```org
   ** TODO Task 1: Create the widget [0/3]
      Write the failing test, run test, implement.
      - [ ] Write the failing test
      - [ ] Run test to verify it fails
      - [ ] Implement
   ```

   For the description body under each task heading, write a 1-line summary
   (do NOT copy the full step content — just the step names).

   Collapse adjacent implementation steps (write test + run test + implement + run test + commit)
   into fewer checkboxes when they form a natural TDD cycle. Group as:
   - `Write and verify failing test` (combines write test + run to verify fail)
   - `Implement and verify` (combines implement + run to verify pass)
   - `Commit`

   This keeps the org file readable rather than having 5+ checkboxes per task
   that each take 2 minutes.

6. **Clock in the first task** with an open CLOCK entry (same as `create`).

7. **Set the top-level heading to IN-PROGRESS** (since execution is about to begin).

8. **Confirm** to the user: show the plan title, task count, and that it's been registered.

### `progress` - Update Task States and Clocking

1. **Read `~/org/plans.org`.**

1b. **Validate clocks.** Scan all DONE tasks in active plans for missing LOGBOOK entries.
   If any DONE task lacks a CLOCK, warn: "Task N: [title] has no clock entry — add estimated times?"
   Offer to fix before proceeding.

2. **Show active plans** (top-level headings not DONE/CANCELLED). For each, show:
   - Title and priority
   - Task summary: N/M tasks done
   - Checkbox progress within current task: `[2/5]` steps done
   - Currently clocked-in task (open CLOCK entry)

3. **Ask the user** what to update. Options:
   - Mark individual checkboxes as done (`- [ ]` → `- [X]`)
   - Complete a whole task (all checkboxes done → task DONE)
   - Start the next task

4. **When marking checkboxes done**, change `- [ ]` to `- [X]` and update the
   cookie counter in the task heading:
   ```org
   ** IN-PROGRESS Task 1: Create the widget [2/3]
      - [X] Write failing test
      - [X] Implement widget component
      - [ ] Run tests and verify
   ```

5. **Clock out the completed task.** Find the open CLOCK entry and close it:

   Before:
   ```org
   CLOCK: [2026-02-17 Tue 14:00]
   ```
   After:
   ```org
   CLOCK: [2026-02-17 Tue 14:00]--[2026-02-17 Tue 14:32] =>  0:32
   ```

   Calculate the duration as `H:MM`. Right-pad with spaces so `=>` aligns.

6. **When all checkboxes in a task are done**, change the task state to DONE
   and update the cookie to show completion:
   ```org
   ** DONE Task 1: Create the widget [3/3]
      - [X] Write failing test
      - [X] Implement widget component
      - [X] Run tests and verify
   ```

7. **If a next task is specified, mark it IN-PROGRESS and clock in:**
   ```org
   ** IN-PROGRESS Task 2: Add API endpoint [0/4]
      :LOGBOOK:
      CLOCK: [2026-02-17 Tue 14:33]
      :END:
      Wire up the REST endpoint and validation.
      - [ ] Define route in router.py
      - [ ] Implement handler with input validation
      - [ ] Write integration test
      - [ ] Commit
   ```

8. **Update the top-level heading state** to `IN-PROGRESS` if not already.

#### Batch Completion (for superpowers sync)

When invoked as `progress tasks N-M done` (or `progress task N done` for a single task):

1. **Read `~/org/plans.org`.**

2. **Identify the active plan** (the one with an IN-PROGRESS top-level heading and an
   open CLOCK entry). If multiple plans are IN-PROGRESS, use the one with the open clock.

3. **For each task in the range N through M:**
   - Mark all checkboxes as done (`- [ ]` → `- [X]`)
   - Update the cookie counter to show completion (`[0/3]` → `[3/3]`)
   - Change the task state to DONE
   - **CLOCK ENFORCEMENT:** If the task has an open CLOCK entry, close it. If the task
     has NO LOGBOOK at all, add one with estimated clock times. Split the elapsed time
     (from previous task's clock end or plan start to current timestamp) evenly across
     the batch. Every DONE task MUST have a closed CLOCK — no exceptions.

4. **Clock in task M+1** (the next task after the batch) if it exists:
   - Add a LOGBOOK with an open CLOCK entry
   - Change its state to IN-PROGRESS

5. **Report** what was updated: "Marked tasks N-M as DONE, clocked in task M+1."

### `done` - Complete a Plan

1. **Read `~/org/plans.org`.**

2. **Show active plans.** Ask which one is done (or infer from context).

3. **Clock out any open clock entries** (same as progress step 4).

4. **Mark all remaining sub-tasks as DONE** (or CANCELLED if skipped).

5. **Set top-level heading to DONE:**
   ```org
   * DONE [#B] Plan title here  :tag1:tag2:
   ```

6. **Ask for a summary** (1-2 sentences of what was accomplished).

7. **Fill in the SUMMARY property:**
   ```org
   :SUMMARY:  Built 3 skills and 3 org files for plan tracking with clocking
   ```

8. **Suggest running `/plan-archive`** to move the completed plan to the archive.

### `defer` - Defer a Plan

1. **Read `~/org/plans.org`.**

2. **Show active plans.** Ask which one to defer.

3. **Clock out any open clock entries.**

4. **Set top-level heading to DEFERRED:**
   ```org
   * DEFERRED [#B] Plan title here  :tag1:tag2:
   ```

5. **Add a state change note** in the LOGBOOK of the top-level heading:
   ```org
   :LOGBOOK:
   - State "DEFERRED"   from "IN-PROGRESS" [2026-02-17 Tue 15:00] \\
     Reason: waiting on upstream dependency
   :END:
   ```

6. **Optionally ask** if the plan should be moved to the backlog instead.

## Timestamp Formats

- Inactive timestamp (properties): `[YYYY-MM-DD Day]` e.g. `[2026-02-17 Tue]`
- Active timestamp (schedule/deadline): `<YYYY-MM-DD Day>` e.g. `<2026-02-18 Wed>`
- Clock timestamp: `[YYYY-MM-DD Day HH:MM]` e.g. `[2026-02-17 Tue 14:00]`
- Clock range: `[YYYY-MM-DD Day HH:MM]--[YYYY-MM-DD Day HH:MM] =>  H:MM`

Day names: Mon, Tue, Wed, Thu, Fri, Sat, Sun

## Getting Current Timestamp

Use bash to get properly formatted timestamps:
```bash
date '+[%Y-%m-%d %a %H:%M]'
```
