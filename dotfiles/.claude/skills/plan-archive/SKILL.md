---
name: plan-archive
description: Archive completed or cancelled plans from ~/org/plans.org to ~/org/plans-archive.org. Preserves full history including clock data. Use when user says "archive plans", "clean up plans", "archive done", or after completing plans.
tools: Read, Write, Edit
args: all | (plan name)
---

# Plan Archive

Move completed (DONE) and cancelled (CANCELLED) plans from `~/org/plans.org` to `~/org/plans-archive.org`, preserving full sub-tree with clock data.

## Arguments
- `all` - Archive all DONE/CANCELLED plans
- `<plan name>` - Archive a specific plan by name match
- (none) - List archivable plans, ask which to archive

## Org Files

- Source: `~/org/plans.org`
- Destination: `~/org/plans-archive.org`

## Instructions

### Step 1: Identify archivable plans

1. **Read `~/org/plans.org`.**

2. **Find top-level headings** with state DONE or CANCELLED:
   ```
   * DONE [#B] Plan title  :tags:
   ```
   or
   ```
   * CANCELLED [#C] Plan title  :tags:
   ```

3. **If no args:** List them and ask which to archive. Show title, completion date (from LOGBOOK state change or CLOSED timestamp), and total clocked time.

4. **If `all`:** Select all DONE/CANCELLED headings.

5. **If `<plan name>`:** Find the heading whose title contains the given text (case-insensitive).

### Step 2: Extract the full sub-tree

For each plan to archive, extract everything from the top-level heading to (but not including) the next top-level heading:

```
* DONE [#B] Plan title  :tags:
  :PROPERTIES:
  ...
  :END:
  SCHEDULED: ...
  DEADLINE: ...
** DONE Sub-task 1
   :LOGBOOK:
   CLOCK: [...]--[...] => H:MM
   :END:
** DONE Sub-task 2
   :LOGBOOK:
   CLOCK: [...]--[...] => H:MM
   :END:
```

Include everything: properties, scheduling, sub-headings, LOGBOOK entries, body text.

### Step 3: Add to archive under date heading

1. **Read `~/org/plans-archive.org`.**

2. **Determine the month heading** from the plan's CREATED property:
   ```
   * 2026-02 February
   ```

3. **If the month heading exists**, append the plan sub-tree under it (demoted one level: `*` becomes `**`, `**` becomes `***`, etc.).

4. **If the month heading doesn't exist**, create it at the appropriate chronological position, then append the plan sub-tree.

   Archive structure:
   ```org
   #+TITLE: Plan Archive
   #+CATEGORY: archive

   * 2026-02 February
   ** DONE [#B] First archived plan  :tags:
      :PROPERTIES:
      ...
      :END:
   *** DONE Sub-task 1
       :LOGBOOK:
       CLOCK: [...]--[...] => H:MM
       :END:

   * 2026-01 January
   ** DONE [#A] Older archived plan  :tags:
   ```

### Step 4: Remove from plans.org

1. **Remove the archived plan's full sub-tree** from `~/org/plans.org`.

2. **Clean up** any extra blank lines left behind (keep at most one blank line between headings).

### Step 5: Confirm

Show the user:
- How many plans were archived
- Their titles
- Where they are now (`~/org/plans-archive.org` under which month heading)
- Remaining active plans count in `plans.org`

## Demoting Headings

When moving a plan into the archive under a month heading, every heading level must increase by 1:
- `* DONE Plan` becomes `** DONE Plan`
- `** DONE Sub-task` becomes `*** DONE Sub-task`

This keeps the archive org-mode structure valid with month headings at level 1.
