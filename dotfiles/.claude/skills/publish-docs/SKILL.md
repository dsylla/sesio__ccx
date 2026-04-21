---
name: publish-docs
description: Build and publish Sphinx documentation to the sésiO Documentation Portal. Handles CodeArtifact auth, sesio-doc-publisher install/update, building (with optional clean), and publishing. Use when user says "publish docs", "build and publish", "update doc-publisher", "push docs to portal", or needs to deploy documentation.
tools: Bash, Read, Edit, Glob
args: install | update | build | publish | clean | all
---

# Publish Documentation to sésiO Portal

Build and publish Sphinx documentation to the sésiO Documentation Portal using `sesio-doc-publisher`.

## Arguments

- `install` - Install sesio-doc-publisher (configure CodeArtifact + uv add)
- `update` - Update sesio-doc-publisher to latest version
- `build` - Build documentation only
- `clean` - Clean build directory then build
- `publish` - Build and publish to portal (default: dev environment)
- `publish prod` - Build and publish to production
- `all` - Full workflow: update + clean + publish

## Prerequisites

- Project has `pyproject.toml` with uv configuration
- Project has `docs/` directory with Sphinx documentation
- AWS credentials available (profile: `sesio__euwest1`)

## Instructions

### Step 1: Check Current State

```bash
# Check if doc-publisher is installed
grep -q "sesio-doc-publisher" pyproject.toml && echo "Installed" || echo "Not installed"

# Check for docs directory
ls -la docs/conf.py 2>/dev/null && echo "Sphinx docs found" || echo "No Sphinx docs"

# Check pyproject.toml for project info
grep -E "^name|^version|^description" pyproject.toml | head -5

# IMPORTANT: Check how docs dependencies are configured
grep -q "dependency-groups" pyproject.toml && echo "Uses dependency-groups" || echo "Uses optional-dependencies"
```

### Step 2: Configure CodeArtifact (if needed)

If `sesio-doc-publisher` is not in pyproject.toml:

1. Check for existing uv index configuration:
   ```bash
   grep -A2 "tool.uv.index" pyproject.toml
   ```

2. Add sesio index if missing:
   ```toml
   [[tool.uv.index]]
   name = "sesio"
   url = "https://sesio-709389331805.d.codeartifact.eu-west-1.amazonaws.com/pypi/devtools/simple/"
   explicit = true
   ```

3. Add to dependencies and sources:
   ```toml
   [project.optional-dependencies]
   docs = [
       "sesio-doc-publisher>=0.3.0",
       # ... other doc dependencies
   ]

   [tool.uv.sources]
   sesio-doc-publisher = { index = "sesio" }
   ```

### Step 3: Authenticate to CodeArtifact

**Always run this before uv operations with sesio packages:**

```bash
export AWS_PROFILE=sesio__euwest1
export UV_INDEX_SESIO_USERNAME=aws
export UV_INDEX_SESIO_PASSWORD=$(aws codeartifact get-authorization-token --domain sesio --domain-owner 709389331805 --region eu-west-1 --query authorizationToken --output text)
```

### Step 4: Install or Update sesio-doc-publisher

**Detect dependency style first:**
```bash
# Check if using dependency-groups or optional-dependencies
if grep -q "dependency-groups" pyproject.toml; then
    SYNC_FLAG="--group docs"
else
    SYNC_FLAG="--extra docs"
fi
```

**Install (first time):**
```bash
/usr/bin/uv add --optional docs sesio-doc-publisher
/usr/bin/uv sync $SYNC_FLAG
```

**Update to latest:**
```bash
/usr/bin/uv lock --upgrade-package sesio-doc-publisher
/usr/bin/uv sync $SYNC_FLAG
```

### Step 5: Build Documentation

**CRITICAL:** The `doc-publisher build` command output is MISLEADING. It may report `Output: docs/_build/html`
but the actual path is often `docs/docs/_build/html`. **ALWAYS use `find` to locate the real build output.**

**Standard build:**
```bash
/usr/bin/uv run doc-publisher build --source ./docs
```

**Clean build (removes _build first):**
```bash
/usr/bin/uv run doc-publisher build --source ./docs --clean
```

**ALWAYS find the actual build output (do NOT trust the command output):**
```bash
# The command output path is often WRONG - always use find
BUILD_OUTPUT=$(find docs -path "*/_build/html" -type d 2>/dev/null | head -1)
echo "Build output: $BUILD_OUTPUT"
# Expected: docs/docs/_build/html (NOT docs/_build/html)
```

### Step 6: Publish to Portal

**Get project info from pyproject.toml and conf.py:**
```bash
PROJECT=$(grep '^name' pyproject.toml | head -1 | cut -d'"' -f2)
VERSION=$(grep '^version' pyproject.toml | head -1 | cut -d'"' -f2)
DESCRIPTION=$(grep '^description' pyproject.toml | head -1 | cut -d'"' -f2)

# Get display name from Sphinx conf.py (may differ from project slug)
DISPLAY_NAME=$(grep '^project = ' docs/conf.py | cut -d'"' -f2)
```

**MANDATORY: Validate display name and description before publishing:**

1. **Display name MUST start with "sésiO "** (e.g., "sésiO Greengrass Tools", "sésiO VPN", "sésiO Connectivity Watchdog").
   - If `docs/conf.py` has a `project` value that does NOT start with "sésiO ", fix it before publishing.
   - The convention is: `project = "sésiO <Human-Readable Name>"` in `docs/conf.py`.
   - Examples: `"sésiO Greengrass Tools"`, `"sésiO Automation"`, `"sésiO Connectivity Watchdog"`
2. **Description MUST NOT be empty.** Extract from `pyproject.toml` `description` field. If missing, ask the user.
3. Both `--display-name` and `--description` are **required** on every publish command.

**Find build output and publish to dev:**
```bash
BUILD_OUTPUT=$(find docs -path "*/_build/html" -type d 2>/dev/null | head -1)

/usr/bin/uv run doc-publisher publish \
    --project "$PROJECT" \
    --source "$BUILD_OUTPUT" \
    --env dev \
    --version "$VERSION" \
    --description "$DESCRIPTION" \
    --display-name "$DISPLAY_NAME"
```

**Publish to production:**
```bash
/usr/bin/uv run doc-publisher publish \
    --project "$PROJECT" \
    --source "$BUILD_OUTPUT" \
    --env prod \
    --version "$VERSION" \
    --description "$DESCRIPTION" \
    --display-name "$DISPLAY_NAME"
```

**Note:** The `--display-name` parameter sets the human-readable name shown in the portal (e.g., "sésiO Automation")
while `--project` is the URL-safe slug (e.g., "sesio-automation").

### Step 7: Verify Publication

```bash
/usr/bin/uv run doc-publisher status --project "$PROJECT" --env dev
```

## Quick Reference

| Command | Description |
|---------|-------------|
| `doc-publisher build -s ./docs` | Build docs |
| `doc-publisher build -s ./docs --clean` | Clean build |
| `doc-publisher publish ... --display-name "Name"` | Publish with custom display name |
| `doc-publisher status -p PROJECT -e dev` | Check dev status |
| `doc-publisher validate -p PROJECT -s BUILD_OUTPUT` | Validate before publish |
| `find docs -path "*/_build/html" -type d` | Find actual build output (ALWAYS use this!) |

## Portal URLs

- **Dev**: https://dev-docs.sesio.io/projects/{project}/
- **Prod**: https://docs.sesio.io/projects/{project}/

## Full Workflow Example

```bash
# 1. Authenticate
export AWS_PROFILE=sesio__euwest1
export UV_INDEX_SESIO_USERNAME=aws
export UV_INDEX_SESIO_PASSWORD=$(aws codeartifact get-authorization-token --domain sesio --domain-owner 709389331805 --region eu-west-1 --query authorizationToken --output text)

# 2. Detect dependency style and sync
if grep -q "dependency-groups" pyproject.toml; then
    SYNC_FLAG="--group docs"
else
    SYNC_FLAG="--extra docs"
fi

# 3. Update doc-publisher
/usr/bin/uv lock --upgrade-package sesio-doc-publisher
/usr/bin/uv sync $SYNC_FLAG

# 4. Clean build
/usr/bin/uv run doc-publisher build --source ./docs --clean

# 5. Find build output (DO NOT trust the command output - it's often wrong!)
BUILD_OUTPUT=$(find docs -path "*/_build/html" -type d 2>/dev/null | head -1)
echo "Actual build output: $BUILD_OUTPUT"

# 6. Get project metadata (ALL fields required)
PROJECT=$(grep '^name' pyproject.toml | head -1 | cut -d'"' -f2)
VERSION=$(grep '^version' pyproject.toml | head -1 | cut -d'"' -f2)
DESCRIPTION=$(grep '^description' pyproject.toml | head -1 | cut -d'"' -f2)
DISPLAY_NAME=$(grep '^project = ' docs/conf.py | cut -d'"' -f2)

# 6b. VALIDATE: display name MUST start with "sésiO " (fix docs/conf.py if not)
# VALIDATE: description MUST NOT be empty

# 7. Publish with ALL metadata (display-name and description are REQUIRED!)
/usr/bin/uv run doc-publisher publish \
    --project "$PROJECT" \
    --source "$BUILD_OUTPUT" \
    --env dev \
    --version "$VERSION" \
    --description "$DESCRIPTION" \
    --display-name "$DISPLAY_NAME"

# 8. Verify
/usr/bin/uv run doc-publisher status --project "$PROJECT" --env dev
```

## Troubleshooting

**"Path does not exist" error for publish (COMMON!):**
- The `doc-publisher build` command output is MISLEADING - it reports a wrong path
- Example: Command says `Output: docs/_build/html` but actual path is `docs/docs/_build/html`
- **ALWAYS** use `find docs -path "*/_build/html" -type d` to locate the real output
- Never trust the path shown in the build command output

**"Package not found" error:**
- CodeArtifact token expired (valid for 12 hours)
- Re-authenticate with the export commands above

**"AWS credentials" error:**
- Ensure AWS_PROFILE is set: `export AWS_PROFILE=sesio__euwest1`
- Or run: `aws sso login --profile sesio__euwest1`

**"Group `docs` is not defined" error:**
- Project uses `[project.optional-dependencies]` not `[dependency-groups]`
- Use `--extra docs` instead of `--group docs`

**Display name not showing correctly in portal:**
- Use `--display-name` parameter with the human-readable name from `docs/conf.py`
- Extract with: `grep '^project = ' docs/conf.py | cut -d'"' -f2`

**Description shows "Contents:" or is empty in portal:**
- The `--description` parameter is REQUIRED for proper portal display
- Extract with: `grep '^description' pyproject.toml | head -1 | cut -d'"' -f2`
- Always pass `--description "$DESCRIPTION"` to the publish command

**Build fails:**
- Check Sphinx errors in output
- Ensure all doc dependencies are installed with correct sync flag
