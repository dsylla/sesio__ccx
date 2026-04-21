#!/usr/bin/env bash
# Hook: SessionStart (startup|resume)
# Reads ~/org/plans.org, outputs active plan context, reminds to query cognee.

set -euo pipefail

PLANS_FILE="$HOME/org/plans.org"

if [[ ! -f "$PLANS_FILE" ]]; then
  echo "No plans.org found. Skipping plan context."
  exit 0
fi

# Find IN-PROGRESS top-level headings with open CLOCK entries
# An open CLOCK has no "--" (end time)
active_plan=""
current_task=""
plan_progress=""
clocked_since=""
next_tasks=""

in_active_plan=false
in_active_task=false
plan_title=""
task_title=""
total_tasks=0
done_tasks=0
has_open_clock=false

while IFS= read -r line; do
  # Top-level heading
  if [[ "$line" =~ ^\*\ IN-PROGRESS\ (.+) ]]; then
    in_active_plan=true
    plan_title="${BASH_REMATCH[1]}"
    # Strip tags (anything after double space + colon-delimited)
    plan_title="${plan_title%%  :*}"
    total_tasks=0
    done_tasks=0
    has_open_clock=false
    current_task=""
    next_tasks=""
    continue
  fi

  # New top-level heading that isn't IN-PROGRESS ends the active plan scan
  if [[ "$line" =~ ^\*\ [A-Z] ]] && [[ ! "$line" =~ ^\*\ IN-PROGRESS ]]; then
    if $in_active_plan && $has_open_clock; then
      break
    fi
    in_active_plan=false
    continue
  fi

  if ! $in_active_plan; then
    continue
  fi

  # Sub-task heading
  if [[ "$line" =~ ^\*\*\ (TODO|IN-PROGRESS|DONE)\ (.+) ]]; then
    task_state="${BASH_REMATCH[1]}"
    task_title="${BASH_REMATCH[2]}"
    total_tasks=$((total_tasks + 1))
    if [[ "$task_state" == "DONE" ]]; then
      done_tasks=$((done_tasks + 1))
    elif [[ "$task_state" == "IN-PROGRESS" ]]; then
      current_task="$task_title"
    elif [[ "$task_state" == "TODO" ]] && [[ -n "$current_task" ]]; then
      # Collect up to 2 next tasks
      short="${task_title%% \[*}"
      if [[ -z "$next_tasks" ]]; then
        next_tasks="$short"
      elif [[ "$next_tasks" != *","* ]]; then
        next_tasks="$next_tasks, $short"
      fi
    fi
    continue
  fi

  # Open CLOCK entry (no --)
  if [[ "$line" =~ CLOCK:\ \[([0-9]{4}-[0-9]{2}-[0-9]{2}\ [A-Za-zé.]+\ [0-9]{2}:[0-9]{2})\]$ ]]; then
    has_open_clock=true
    clocked_since="${BASH_REMATCH[1]}"
    continue
  fi

done < "$PLANS_FILE"

if $in_active_plan && [[ -n "$plan_title" ]]; then
  echo "=== Active Plan ==="
  echo "Plan: $plan_title"
  echo "Status: IN-PROGRESS | $done_tasks/$total_tasks tasks done"
  if [[ -n "$current_task" ]]; then
    echo "Current task: $current_task"
  fi
  if [[ -n "$clocked_since" ]]; then
    echo "Clocked in since: [$clocked_since]"
  fi
  if [[ -n "$next_tasks" ]]; then
    echo "Next tasks: $next_tasks"
  fi
  echo ""
  echo "Reminder: Query cognee for project context before starting work."
  echo "==="
else
  echo "No active IN-PROGRESS plan with open clock found."
fi
