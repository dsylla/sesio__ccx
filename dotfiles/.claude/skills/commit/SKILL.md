---
name: commit
description: Create smart git commits with conventional commit format. Analyzes changes, suggests atomic commits, and can split changes into multiple logical commits. Use when user says "commit", "commit changes", "git commit", or wants to commit their work.
tools: Bash
---

# Smart Commit

Analyze the current changes and create meaningful, atomic commits following conventional commits format.

## Instructions

1. **Check the current state:**
   - Run `git status` to see staged and unstaged changes
   - Run `git diff --cached` for staged changes (or `git diff` if nothing staged)

2. **Analyze the changes:**
   - Identify logically separate changes (different features, fixes, refactors)
   - Determine if changes should be ONE commit or SPLIT into multiple atomic commits
   - Consider: Do these changes serve a single purpose or multiple purposes?

3. **If splitting is recommended:**
   - Explain to the user why splitting would be better
   - List the proposed commits with their scope
   - Ask for confirmation before proceeding
   - Use `git add -p` or selective `git add <file>` to stage each logical unit separately

4. **Generate commit messages:**
   - Follow conventional commits: `type(scope): description`
   - Types: feat, fix, refactor, docs, chore, test, style, perf
   - Keep the first line under 72 characters
   - Add a body if the change needs explanation (the "why", not the "what")

5. **Ask for context if helpful:**
   - If the intent behind changes is unclear from the diff alone, ask the user
   - Example: "These changes touch auth and logging - is this a single feature or separate concerns?"

6. **Execute the commit(s):**
   - Stage the appropriate files
   - Create the commit with the generated message
   - Do NOT include any Co-Authored-By lines
   - If multiple commits, repeat for each logical unit

## Example Output

For a single commit:
```
feat(vpn): preserve DNS records on client disconnect

DNS A records are now retained when devices disconnect, allowing
them to remain addressable for reconnection.
```

For split commits:
```
Proposed commits:
1. fix(dns): preserve records on disconnect
2. refactor(iot): extract status update logic

Proceed with this split? [Y/n]
```
