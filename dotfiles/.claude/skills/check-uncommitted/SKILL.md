---
name: check-uncommitted
description: Scan Work/sesio/* for uncommitted git changes. Shows repos with staged, unstaged, or untracked files. Use when user says "check uncommitted", "dirty repos", "what needs committing", or wants to see git status across all sesio projects.
tools: Bash
---

# Check Uncommitted Changes

Scan all repositories in `~/Work/sesio/` and report which ones have uncommitted work.

## Instructions

1. **Scan all directories** in `~/Work/sesio/` (top-level only)

2. **For each git repo**, check for:
   - Staged changes (ready to commit)
   - Unstaged changes (modified but not staged)
   - Untracked files

3. **Run this command** to gather the data:

```bash
bash /home/david/.claude/skills/check-uncommitted/check-dirty-repos.sh
```

4. **Count clean repos:**

```bash
bash /home/david/.claude/skills/check-uncommitted/count-repos.sh
```

5. **Present results** in this format:

## Git Status: ~/Work/sesio/

### Dirty Repos (N)

For each dirty repo, show:
- **repo-name** - X staged, Y modified, Z untracked

Only show non-zero counts. Example:
- **sesio__alert** - 2 modified, 1 untracked
- **sesio__watchdog** - 3 staged (ready to commit)

### Clean Repos (N)

State: "All other repos have no uncommitted changes."

### Quick Actions

- Use `/commit` in a specific repo to commit changes
- Use `git stash` to temporarily save changes
- Use `git checkout -- <file>` to discard unstaged changes
