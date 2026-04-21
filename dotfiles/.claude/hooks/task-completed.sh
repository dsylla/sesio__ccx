#!/usr/bin/env bash
# Hook: TaskCompleted
# Auto-updates ~/org/plans.org when a TaskList task is completed.
# Matches task subject to org ** heading, marks checkboxes, updates clock.

set -uo pipefail

PLANS_FILE="$HOME/org/plans.org"

if [[ ! -f "$PLANS_FILE" ]]; then
  exit 0
fi

# Read JSON from stdin
input="$(cat)"

# Extract task subject
task_subject="$(echo "$input" | jq -r '.task_subject // .subject // empty' 2>/dev/null)"

if [[ -z "$task_subject" ]]; then
  exit 0
fi

# Export for Python to access via os.environ
export TASK_SUBJECT="$task_subject"

# Use Python for reliable org file manipulation
python3 << 'PYEOF'
import re
import sys
import os
from datetime import datetime

plans_file = os.path.expanduser("~/org/plans.org")
task_subject = os.environ.get("TASK_SUBJECT", "")

if not task_subject or not os.path.exists(plans_file):
    sys.exit(0)

with open(plans_file, "r") as f:
    lines = f.readlines()

# Current timestamp
now = datetime.now()
day_names = {"0": "dim.", "1": "lun.", "2": "mar.", "3": "mer.", "4": "jeu.", "5": "ven.", "6": "sam."}
day_name = day_names[now.strftime("%w")]
ts = now.strftime(f"[%Y-%m-%d {day_name} %H:%M]")
date_part = now.strftime(f"[%Y-%m-%d {day_name}]")

# Extract key phrase from task subject for matching
# e.g. "Task 3: Create report index page [0/3]" -> "Create report index page"
match = re.search(r'Task \d+[:\s]+(.+?)(?:\s*\[\d+/\d+\])?$', task_subject)
if match:
    search_phrase = match.group(1).strip()
else:
    search_phrase = task_subject.strip()

# Find ALL IN-PROGRESS plans with open clocks, then match task subject
candidates = []
i = 0
while i < len(lines):
    line = lines[i]
    if re.match(r'^\* IN-PROGRESS ', line):
        plan_start = i
        plan_end = len(lines)
        found_open_clock = False
        j = i + 1
        while j < len(lines):
            if re.match(r'^\* ', lines[j]):
                plan_end = j
                break
            if re.match(r'\s*CLOCK: \[\d{4}-\d{2}-\d{2} [^\]]+\]$', lines[j].rstrip()):
                found_open_clock = True
            j += 1
        if found_open_clock:
            candidates.append((plan_start, plan_end))
    i += 1

if not candidates:
    sys.exit(0)

# Search each candidate plan for a matching task heading
target_idx = None
next_todo_idx = None
active_plan_start = None
active_plan_end = None

for plan_start, plan_end in candidates:
    found_target = None
    found_next = None
    for i in range(plan_start + 1, plan_end):
        line = lines[i]
        if re.match(r'^\*\* (TODO|IN-PROGRESS) ', line) and search_phrase.lower() in line.lower():
            found_target = i
        elif found_target is not None and found_next is None:
            if re.match(r'^\*\* TODO ', line):
                found_next = i
    if found_target is not None:
        target_idx = found_target
        next_todo_idx = found_next
        active_plan_start = plan_start
        active_plan_end = plan_end
        break

if target_idx is None:
    sys.exit(0)

modified = False

# 1. Mark all checkboxes done in this task
checkbox_count = 0
checked_count = 0
task_end = active_plan_end
for i in range(target_idx + 1, active_plan_end):
    if re.match(r'^\*\* ', lines[i]):
        task_end = i
        break

for i in range(target_idx + 1, task_end):
    if re.match(r'\s*- \[ \] ', lines[i]):
        lines[i] = lines[i].replace('- [ ] ', '- [X] ', 1)
        modified = True
    if re.match(r'\s*- \[.\] ', lines[i]):
        checkbox_count += 1
        if '[X]' in lines[i]:
            checked_count += 1

# 2. Update cookie in heading
cookie_match = re.search(r'\[(\d+)/(\d+)\]', lines[target_idx])
if cookie_match and checkbox_count > 0:
    lines[target_idx] = re.sub(r'\[\d+/\d+\]', f'[{checkbox_count}/{checkbox_count}]', lines[target_idx])
    modified = True

# 3. Change state to DONE
lines[target_idx] = re.sub(r'^\*\* (TODO|IN-PROGRESS) ', '** DONE ', lines[target_idx])
modified = True

# 4. Close open CLOCK entry in this task
for i in range(target_idx + 1, task_end):
    clock_match = re.match(r'(\s*CLOCK: \[\d{4}-\d{2}-\d{2} [^\]]+\])$', lines[i].rstrip())
    if clock_match:
        start_str = clock_match.group(1).strip()
        # Parse start time to compute duration
        start_ts_match = re.search(r'\[(\d{4})-(\d{2})-(\d{2}) [^\]]+ (\d{2}):(\d{2})\]', start_str)
        if start_ts_match:
            start_dt = datetime(
                int(start_ts_match.group(1)), int(start_ts_match.group(2)),
                int(start_ts_match.group(3)), int(start_ts_match.group(4)),
                int(start_ts_match.group(5))
            )
            duration = now - start_dt
            hours = int(duration.total_seconds() // 3600)
            minutes = int((duration.total_seconds() % 3600) // 60)
            duration_str = f"{hours}:{minutes:02d}"
            lines[i] = f"   CLOCK: {start_str[7:]}--{ts} => {duration_str:>5}\n"
            modified = True
        break

# 5. Clock in next TODO task
if next_todo_idx is not None:
    # Change state to IN-PROGRESS
    lines[next_todo_idx] = re.sub(r'^\*\* TODO ', '** IN-PROGRESS ', lines[next_todo_idx])
    # Check if LOGBOOK already exists
    has_logbook = False
    insert_at = next_todo_idx + 1
    for j in range(next_todo_idx + 1, active_plan_end):
        if re.match(r'^\*\* ', lines[j]):
            break
        if ':LOGBOOK:' in lines[j]:
            has_logbook = True
            insert_at = j + 1
            break
        if re.match(r'\s+\S', lines[j]) and ':LOGBOOK:' not in lines[j] and ':END:' not in lines[j]:
            insert_at = j
            break

    if has_logbook:
        lines.insert(insert_at, f"   CLOCK: {ts}\n")
    else:
        lines.insert(insert_at, f"   :LOGBOOK:\n")
        lines.insert(insert_at + 1, f"   CLOCK: {ts}\n")
        lines.insert(insert_at + 2, f"   :END:\n")
    modified = True

if modified:
    with open(plans_file, "w") as f:
        f.writelines(lines)

PYEOF
