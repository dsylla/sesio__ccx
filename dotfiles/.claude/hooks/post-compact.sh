#!/usr/bin/env bash
# Hook: SessionStart (compact)
# Re-injects workflow rules after context compaction, plus active plan state.

set -euo pipefail

cat << 'RULES'
=== Workflow Rules (re-injected after compaction) ===

Rule 1 — Cognee Knowledge Building:
- Query cognee for project context at session start
- Call save_interaction for significant decisions (~every 10 meaningful interactions)
- Save structured session summary at session end
- Cognify periodically (~30min) or use /cognify manually

Rule 2 — Org-Mode Progress Tracking:
- Keep ~/org/plans.org in sync with work
- Check for active plans and resume clocking
- Update checkboxes and clock entries when completing tasks
- Ensure open clocks are closed at session end

Rule 3 — Test-First Development:
- Only in projects with tdd: true in .claude/CLAUDE.md
- Write a failing test before implementation code
- Does NOT apply to scripts, infra, config, docs, skills

===
RULES

# Also output active plan state (reuse session-start logic)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -x "$SCRIPT_DIR/session-start.sh" ]]; then
  echo ""
  "$SCRIPT_DIR/session-start.sh"
fi
