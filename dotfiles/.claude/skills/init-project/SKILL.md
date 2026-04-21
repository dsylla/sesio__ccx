---
name: init-project
description: Scaffold a new project with Claude Code conventions. Creates .claude/CLAUDE.md with project settings including optional TDD enforcement. Use when user says "init project", "new project", "scaffold project", or "create project".
tools: Bash, Read, Write, Edit, Glob, AskUserQuestion
---

# Init Project

Scaffold a new project with Claude Code conventions and workflow settings.

## Instructions

1. **Detect current context:**
   - Check if we're in a git repo (`git rev-parse --show-toplevel`)
   - Check if `.claude/CLAUDE.md` already exists
   - If it exists, ask: "Project CLAUDE.md already exists. Update it?"

2. **Gather project info** via AskUserQuestion:
   - Project name (default: git repo basename or directory name)
   - Short description (one line)
   - Primary language/stack (Python, TypeScript, Go, etc.)

3. **Ask about workflow settings:**
   - "Enable test-driven development enforcement?" (yes/no)
     - Description: "When enabled, Claude will remind you to write tests before implementation code."

4. **Create `.claude/CLAUDE.md`:**

   ```markdown
   # <Project Name>

   <Description>

   ## Project Settings
   tdd: <true|false>

   ## Tech Stack
   - <language/stack>

   ## Conventions
   <!-- Add project-specific conventions here -->
   ```

5. **If TDD enabled:**
   - Create `tests/` directory if it doesn't exist
   - For Python: create `tests/__init__.py` and `tests/conftest.py` (empty)
   - For TypeScript/JavaScript: note that jest/vitest config should be added
   - For Go: no action needed (tests are co-located)

6. **Create `.claude/` directory** if it doesn't exist:
   ```bash
   mkdir -p .claude
   ```

7. **Report what was created:**
   - List all files created
   - Suggest: "Run /analyze to generate full project documentation"
   - If TDD enabled: "TDD enforcement is active. The PreToolUse hook will remind you to write tests first."
