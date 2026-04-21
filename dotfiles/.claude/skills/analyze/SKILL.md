---
name: analyze
description: Analyze a codebase and generate comprehensive documentation including README, Sphinx docs, llms.txt, and CLAUDE.md. Discovers infrastructure via MCP. Use when user says "analyze project", "generate docs", "document this codebase", "refresh docs", "update documentation", or needs project documentation.
tools: Bash, Read, Write, Edit, Glob, Grep, Task
args: aws | db | docs-only | refresh | (none for full analysis)
---

# Project Analysis & Documentation Generator

Analyze a codebase, discover infrastructure via MCP, and generate comprehensive documentation.

## Arguments
- `aws` - Focus on AWS infrastructure discovery (skip Phase 1, go straight to Phase 2 AWS)
- `db` - Focus on database schema analysis (skip Phase 2 AWS, focus Phase 2 DB)
- `docs-only` - Skip Phase 2 entirely, generate docs from code analysis only
- `refresh` - Update existing documentation (see Refresh Mode section below)
- (none) - Full analysis: all phases

## Instructions

### Phase 0: Context Gathering

1. **Query cognee for prior analysis:**
   ```
   cognee.search("What do I know about <project-name>?", search_type="GRAPH_COMPLETION", top_k=5)
   ```
   Use any prior findings to avoid redundant analysis and to detect what changed.

2. **Detect existing documentation format:**
   Use Glob to check what already exists:
   ```
   Glob: README.* in project root
   Glob: CLAUDE.md, .claude/CLAUDE.md
   Glob: llms.txt
   Glob: docs/conf.py
   ```
   Record existing formats — match them in Phase 3 (don't generate README.org if README.md exists).

3. **Detect project shape (single vs monorepo):**
   ```
   Glob: */package.json, */pyproject.toml, */go.mod, */Cargo.toml
   ```
   If multiple project manifests exist in subdirectories, this is a monorepo.
   Ask the user: analyze the whole repo or a specific service?

### Phase 1: Codebase Analysis

> **Skip if argument is `aws`.** Proceed for all other arguments.

1. **Detect project type:**
   Use Glob to discover config/manifest files:
   ```
   Glob: *.json, *.yaml, *.yml, *.toml in project root and one level deep
   Glob: Makefile, Justfile, Taskfile.yml
   ```

2. **Identify stack:**
   - Languages: use Glob for extensions (`**/*.py`, `**/*.ts`, `**/*.go`, etc.)
   - Package managers & dependencies:
     - `pyproject.toml` (uv/poetry/flit) — **check this first for Python projects**
     - `requirements.txt`, `setup.py`, `setup.cfg` (legacy Python)
     - `package.json` (Node.js)
     - `go.mod` (Go)
     - `Cargo.toml` (Rust)
     - `Gemfile` (Ruby)
   - Infrastructure: `docker-compose.yml`, `Dockerfile`, `k8s/`, `terraform/`, `serverless.yml`, `cdk.json`
   - CI/CD: `.github/workflows/`, `.gitlab-ci.yml`, `Jenkinsfile`, `buildspec.yml`

3. **Map structure:**
   - Entry points (main, index, app, cmd/)
   - Services and their responsibilities
   - API routes and endpoints (use Grep to find route decorators/handlers)
   - Background workers/jobs
   - Database models/schemas

4. **Extract key patterns:**
   - Authentication/authorization approach
   - Error handling conventions
   - Logging and observability
   - Configuration management
   - Testing strategy (use Glob: `**/test_*`, `**/*_test.*`, `**/*.test.*`, `**/*.spec.*`)

### Phase 2: Infrastructure Discovery (MCP)

> **Skip entirely if argument is `docs-only`.**
> **Skip AWS section if argument is `db`.** Skip DB section if argument is `aws`.**

#### AWS (if MCP available and not `db`-only)

**MANDATORY: Follow the AWS MCP tool order:**
1. `check_environment_variables()` — always first
2. `get_aws_session_info(env_check_result)` — always second
3. Then use `list_resources` / `get_resource` to discover:
   - Compute: EC2, ECS, EKS, Lambda functions
   - Storage: S3 buckets, EBS, EFS
   - Database: RDS, DynamoDB, ElastiCache
   - Networking: VPCs, subnets, security groups, load balancers
   - IAM: roles, policies relevant to the project

If AWS credentials are unavailable, note it and move on — don't block the analysis.

#### MongoDB (if MCP available and not `aws`-only)
- List databases and collections
- Analyze schemas (sample documents)
- Identify indexes
- Map relationships between collections

#### Other databases
- Use Grep to find connection strings in config files
- **IMPORTANT:** Never include actual connection strings, passwords, or secrets in generated docs. Mask them and warn the user if found.
- Document schema if accessible

### Phase 3: Generate Documentation

Use the formats detected in Phase 0. If no existing docs, use the defaults below.

#### 3.1 README (match existing format, default to .org)

**Format detection:** If `README.md` exists, generate markdown. If `README.org` exists or no README exists, generate org-mode.

**Org-mode template:**
```org
#+TITLE: [Project Name]
#+AUTHOR: [from git config or package.json/pyproject.toml]
#+OPTIONS: toc:2 num:nil

* Overview
[Brief description of what the project does]

* Architecture
** Components
[List of services/components and their roles]

** Infrastructure
[AWS resources, databases, external services]

** Diagram
[Generate a Graphviz dot diagram — widely supported in org-mode via ob-dot]

#+BEGIN_SRC dot :file architecture.png :cmdline -Kdot -Tpng
digraph architecture {
  rankdir=LR;
  // nodes and edges here
}
#+END_SRC

* Getting Started
** Prerequisites
[Required tools, versions, accounts]

** Installation
#+BEGIN_SRC bash
[Setup commands]
#+END_SRC

** Configuration
[Environment variables, config files]

** Running Locally
#+BEGIN_SRC bash
[Commands to run the project]
#+END_SRC

* Development
** Project Structure
[Directory tree with explanations]

** Key Commands
| Command | Description |
|---------+-------------|
| ...     | ...         |

** Testing
[How to run tests]

* Deployment
[Deployment process and environments]

* API Reference
[Key endpoints or link to full docs]
```

**Markdown template:** Same structure but using `#` headings, fenced code blocks, and standard markdown tables. For architecture diagrams, output a separate `architecture.mmd` mermaid file and reference it.

#### 3.2 Sphinx Documentation (docs/)

Create if not exists:
```
docs/
├── conf.py          # Sphinx config with ReadTheDocs theme
├── index.rst        # Main entry point
├── getting-started.rst
├── architecture.rst
├── api/             # Auto-generated API docs
├── guides/          # How-to guides
└── deployment.rst
```

**conf.py essentials:**
```python
project = '[Project Name]'
extensions = ['sphinx.ext.autodoc', 'sphinx.ext.viewcode', 'myst_parser']
html_theme = 'sphinx_rtd_theme'
```

#### 3.3 llms.txt (Root directory)

Follow llmstxt.org specification:
```
# [Project Name]

> [One-line description]

[2-3 paragraph summary of what this project does, its main purpose,
and the key technologies used.]

## Architecture

[Brief description of how components fit together]

## Key Files

- `src/main.py`: Application entry point
- `src/api/`: REST API endpoints
- `src/models/`: Database models
- `config/`: Configuration files

## Development

[How to set up and run locally]

## Optional

- [Links to detailed docs]
- [API reference]
```

#### 3.4 CLAUDE.md

**Placement:** If `.claude/CLAUDE.md` exists, update it there. If `CLAUDE.md` exists at project root, update at root. If neither exists, create at `.claude/CLAUDE.md` (preferred convention).

```markdown
# Project Context for Claude

## Overview
[What this project does in 2-3 sentences]

## Tech Stack
- Language: [X]
- Framework: [X]
- Package manager: [X — e.g., uv, npm, cargo]
- Database: [X]
- Infrastructure: [X]

## Project Structure
[Key directories and their purpose]

## Conventions
- [Coding style, naming conventions]
- [Commit message format]
- [PR process]

## Common Tasks
- **Run locally:** `[command]`
- **Run tests:** `[command]`
- **Lint/format:** `[command]`
- **Deploy:** `[command]`

## Architecture Decisions
- [Key decisions and why they were made]

## Gotchas
- [Non-obvious things that could trip you up]

## External Services
- [List of external APIs, services with their purpose]
```

### Phase 4: Output

1. **Show summary** of discovered information
2. **Ask for confirmation** before writing files
3. **Create files** in appropriate locations
4. **Generate architecture diagram** (Graphviz dot for org-mode, mermaid for markdown — save as separate file)
5. **Validate Sphinx build** (if Sphinx docs were generated):
   ```bash
   cd docs && sphinx-build -b html . _build/ -q 2>&1 | tail -20
   ```
   Fix any errors before declaring success.
6. **Save findings to cognee:**
   ```
   cognee.save_interaction:
   [project: <name>] [type: architectural-decision]
   Decision/Finding: Project analysis completed
   Context: Full codebase analysis via /analyze skill
   Key findings: <stack summary, services, infra, patterns>
   ```
7. **List next steps:**
   - Review and customize generated docs
   - Mark manual sections with `<!-- MANUAL: START/END -->` markers
   - Offer to run `/publish-docs` if Sphinx docs were generated
   - Offer to run `/commit` for the new documentation files

## Refresh Mode (`refresh` argument)

When `refresh` is specified, update existing documentation rather than generating from scratch.

### Refresh Phase 1: Inventory Existing Docs

1. **Find existing documentation files:**
   Use Glob to locate all doc files:
   ```
   Glob: CLAUDE.md, .claude/CLAUDE.md, llms.txt, README.org, README.md
   Glob: docs/conf.py, docs/**/*.rst
   ```

2. **Read and parse existing docs:**
   - Extract sections and their content
   - Identify auto-generated vs manual sections
   - Note any `<!-- MANUAL -->` or `# MANUAL` markers (preserve these)

3. **Get last analysis timestamp:**
   - Check git log for last doc changes
   - Compare against code changes since then

### Refresh Phase 2: Detect Changes

1. **Find code changes since last doc update:**
   ```bash
   git log --oneline --since="<last_doc_commit_date>" --name-only -- ':!docs' ':!*.md' ':!*.org' ':!*.rst' ':!llms.txt'
   ```

2. **Cross-check with current state:**
   Also use Glob/Grep to verify the current codebase matches what docs describe.
   Git history alone can miss rebases or squashes — always validate against actual files.

3. **Categorize changes:**
   - New files/directories added
   - Files removed
   - Significant modifications (new functions, classes, endpoints)
   - Infrastructure changes (terraform, docker, CI/CD)
   - Dependency changes (pyproject.toml, package.json, requirements.txt, etc.)

4. **Map changes to doc sections:**
   | Change Type | Affected Sections |
   |-------------|-------------------|
   | New API endpoint | API Reference, llms.txt Key Files |
   | New dependency | Tech Stack, Prerequisites |
   | New service/component | Architecture, Project Structure |
   | Config change | Configuration, Environment Variables |
   | Infrastructure | Infrastructure, Deployment |

### Refresh Phase 3: Update Documentation

1. **For each doc file with changes needed:**
   - Read current content
   - Identify sections to update
   - Preserve sections marked as manual
   - Generate updated content for changed sections only

2. **Update strategies by file:**

   **CLAUDE.md:**
   - Update Tech Stack if dependencies changed
   - Update Project Structure if new directories
   - Update Common Tasks if new scripts/commands found
   - Preserve Conventions, Architecture Decisions, Gotchas (likely manual)

   **llms.txt:**
   - Update Key Files section with new important files
   - Update Architecture if structure changed significantly
   - Preserve overview unless major project changes

   **README.org/README.md:**
   - Update Prerequisites if new dependencies
   - Update Installation if setup changed
   - Update Project Structure with new directories
   - Update API Reference with new endpoints
   - Preserve Getting Started narrative (likely customized)

   **docs/ (Sphinx):**
   - Regenerate API docs if code changed
   - Update architecture.rst if infrastructure changed
   - Preserve custom guides

3. **Show diff before applying:**
   - Display what will change in each file
   - Highlight sections being updated vs preserved
   - Ask for confirmation

### Refresh Phase 4: Output

1. **Summary of changes detected:**
   - Files changed since last analysis
   - Sections that need updating
   - Sections preserved (manual)

2. **Preview updates** (use Edit tool to show diffs)

3. **Apply updates** after confirmation

4. **Validate Sphinx build** if docs/ was updated

5. **Save refresh findings to cognee**

6. **Suggest next steps:**
   - Review changes
   - Mark any new manual sections with `<!-- MANUAL -->`
   - Offer to run `/publish-docs` if Sphinx docs were updated
   - Offer to run `/commit` for documentation updates

### Preservation Markers

Use these markers to protect manual content from refresh:

```markdown
<!-- MANUAL: START -->
This content will not be overwritten during refresh.
<!-- MANUAL: END -->
```

```org
# MANUAL: START
This content will not be overwritten during refresh.
# MANUAL: END
```

## Notes
- Respect .gitignore — don't document ignored files
- **Security:** Mask secrets found in config (warn user). Never include credentials, connection strings, API keys, or tokens in generated docs
- If Sphinx exists, update rather than overwrite
- Offer to add docs dependencies to pyproject.toml/requirements/package.json
- In refresh mode, always preserve content between MANUAL markers
- In refresh mode, show diffs before applying changes
- For monorepos, offer per-service or whole-repo analysis
- Always use Glob/Grep tools instead of `find`/`ls`/`grep` bash commands for file discovery
