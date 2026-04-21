# Global Instructions for Claude

<!-- CARL-MANAGED: Do not remove this section -->
## CARL Integration

Follow all rules in <carl-rules> blocks from system-reminders.
These are dynamically injected based on context and MUST be obeyed.
<!-- END CARL-MANAGED -->


## About Me
- GitHub: dsylla (personal), davidsylla (work/ssdd)

## Environment
- Uses **asdf** for Python, Node.js, and Ruby version management
- Uses **uv** (system package, not asdf) for Python package management:
  - Always call as `/usr/bin/uv` or ensure system uv is in PATH before asdf shims
  - Prefer `uv sync` with pyproject.toml (not `uv pip install`)
  - Use `uv add <pkg>` to add dependencies
  - Use `uv sync --group dev` for dev dependencies

## File Locations
- **Screenshots:** `/home/david/Screenshots`

## Coding Preferences
<!-- Add your preferences here, e.g.:
- Prefer functional style over OOP
- Use TypeScript strict mode
- Keep functions small and focused
-->

## Communication Style
<!-- Add your preferred communication style, e.g.:
- Be concise
- Skip explanations unless asked
- Always explain the "why" behind suggestions
-->

## Project Conventions

- **Always use `/commit` for git commits.** Never run `git commit` directly. The `/commit` skill analyzes changes, suggests conventional commit format, and can split into atomic commits. This applies everywhere: skills, subagents, plans, manual work.

## Workflow Rules

### Rule 1: Cognee Knowledge Building
- **Always** use cognee to learn from sessions
- At session start: query cognee for relevant context about the current project/task
  ```
  cognee.search("What do I know about <project>?", search_type="GRAPH_COMPLETION", top_k=5)
  ```
- During work: call `save_interaction` for significant decisions, architectural choices,
  debugging breakthroughs, and lessons learned (~every 10 meaningful interactions)
- Content format:
  ```
  [project: <name>] [type: architectural-decision|debugging|lesson-learned|preference]
  Decision/Finding: <what>
  Context: <why this came up>
  Reasoning: <why this choice>
  ```
- At session end: save a structured session summary to cognee
  ```
  [session-summary] [project: <name>] [date: YYYY-MM-DD]
  Accomplished: <what was done>
  Key decisions: <choices made and why>
  Blockers hit: <problems and solutions>
  Open threads: <unfinished work>
  ```
- Periodically cognify (~every 30 minutes of active work) to keep the graph fresh
- Use `/cognify` for manual mid-session graph updates when needed
- After committing, if the commit touches design docs or plans, call `save_interaction`
  with a summary of what was committed and why

### Rule 2: Org-Mode Progress Tracking
- **Always** keep `~/org/plans.org` in sync with actual work
- At session start: check for active IN-PROGRESS plans and resume clocking
- When completing tasks: update org checkboxes and clock entries
- At session end: ensure all open clocks are closed
- This supplements (not replaces) the existing superpowers + org-mode integration
- The `TaskCompleted` hook handles automatic updates; use `/plan-tracker` for complex operations

### Rule 3: Test-First Development
- **Only in projects that opt in** via `tdd: true` in their `.claude/CLAUDE.md`
- When `tdd: true`: write a failing test before writing implementation code
- When creating new project files: check for TDD opt-in before writing implementation
- Does NOT apply to: scripts, one-off commands, infrastructure code, config files,
  documentation, skills, or CLAUDE.md itself
- Use the `superpowers:test-driven-development` skill for the full TDD workflow
- The `PreToolUse[Edit|Write]` hook provides reminders; it never blocks

### Rule 4: Team Agents for Subagent-Driven Development
- **HARD RULE:** When using `superpowers:subagent-driven-development`, **always** use
  the Team Agent feature (TeamCreate + Task tools + SendMessage)
- Never dispatch isolated Task subagents without a team — always create a team first
- Workflow: TeamCreate -> TaskCreate for all plan tasks -> spawn teammates -> coordinate
- Parallelize independent tasks across teammates (wave-based execution)
- Team lead reviews between waves, runs verification after all tasks complete
- This applies to ALL subagent-driven work, no exceptions

## Superpowers + Org-Mode Integration

When using the superpowers planning workflow, bridge to org-mode tracking at these lifecycle points:

1. **When `brainstorming` starts** -> invoke `/plan-tracker from-brainstorming` to create a lightweight org entry and start the clock. This captures design/exploration time before the implementation plan exists.
2. **After `writing-plans` saves a plan file** -> invoke `/plan-tracker from-superpowers` with the plan file path. If an org entry already exists from brainstorming, it updates that entry with tasks. Otherwise creates a new one.
3. **During `executing-plans` or `subagent-driven-development`, after completing each task** -> invoke `/plan-tracker progress` to mark the corresponding org-mode checkboxes done and clock the next task.
4. **After `finishing-a-development-branch` completes** -> invoke `/plan-tracker done` to mark the org-mode plan as DONE.
5. **At session start, if resuming interrupted work** -> invoke `/plan-resume` to find where you left off via `~/org/plans.org`.

@RTK.md
