---
name: check-unpushed
description: Scan Work/sesio/* for unpushed commits. Shows repos with local commits not yet pushed to remote. Use when user says "check unpushed", "what needs pushing", "unpushed commits", or wants to see push status across all sesio projects.
tools: Bash
---

# Check Unpushed Commits

Scan all repositories in `~/Work/sesio/` and report which ones have commits not pushed to remote.

## Instructions

1. **Scan all directories** in `~/Work/sesio/` (top-level only)

2. **For each git repo**, check for:
   - Commits ahead of tracking branch (unpushed)
   - Branches with no upstream set

3. **Run this bash script** to gather the data:

```bash
for dir in ~/Work/sesio/*/; do
  if [ -d "$dir/.git" ]; then
    name=$(basename "$dir")
    cd "$dir"
    branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
    upstream=$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null)
    if [ -z "$upstream" ]; then
      echo "$name|$branch|no-upstream|0"
    else
      ahead=$(git rev-list --count @{u}..HEAD 2>/dev/null || echo 0)
      if [ "$ahead" -gt 0 ]; then
        echo "$name|$branch|$upstream|$ahead"
      fi
    fi
  fi
done
```

4. **Count repos:**

```bash
pushed=0; unpushed=0; no_upstream=0
for dir in ~/Work/sesio/*/; do
  [ -d "$dir/.git" ] || continue
  cd "$dir"
  upstream=$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null)
  if [ -z "$upstream" ]; then
    ((no_upstream++))
  else
    ahead=$(git rev-list --count @{u}..HEAD 2>/dev/null || echo 0)
    [ "$ahead" -gt 0 ] && ((unpushed++)) || ((pushed++))
  fi
done
echo "Pushed: $pushed, Unpushed: $unpushed, No upstream: $no_upstream"
```

5. **Present results** in this format:

## Push Status: ~/Work/sesio/

### Unpushed Repos (N)

For each repo with unpushed commits, show:
- **repo-name** (`branch`) - X commits ahead of `origin/branch`

Example:
- **sesio__alert** (`main`) - 3 commits ahead of `origin/main`
- **sesio__watchdog** (`feature/new-check`) - 1 commit ahead of `origin/feature/new-check`

### No Upstream Set (N)

For branches with no tracking branch:
- **repo-name** (`branch`) - no upstream configured

### Up to Date (N)

State: "All other repos are up to date with their remotes."

### Quick Actions

- Use `git push` in a specific repo to push commits
- Use `git push -u origin <branch>` to set upstream and push
- Use `git branch -vv` to see tracking info for all branches
