---
name: codeartifact-setup
description: Configure a Python project to use sesio's private CodeArtifact PyPI repository. Use when user asks to "configure codeartifact", "add sesio packages", "setup internal pypi", "add codeartifact source", or needs to install sesio-doc-publisher or other internal packages.
tools: Read, Edit, Bash
---

# CodeArtifact Setup Skill

Configure this project to use sesio's private CodeArtifact PyPI repository.

## What This Does

1. Fetches the repository URL from AWS SSM Parameter Store
2. Adds sesio as an index source in `pyproject.toml` for uv
3. Optionally configures specific packages to use the sesio index

## Instructions

### Step 1: Fetch the Repository URL

Run this command to get the CodeArtifact URL from SSM:

```bash
aws ssm get-parameter --name "/sesio/production/codeartifact/sesio/pip_index_url" --query "Parameter.Value" --output text
```

Expected result: `https://sesio-709389331805.d.codeartifact.eu-west-1.amazonaws.com/pypi/devtools/simple/`

### Step 2: Check for Existing Configuration

Read the project's `pyproject.toml` and check if:
- `[[tool.uv.index]]` section exists
- A sesio index is already configured

### Step 3: Add the Index Configuration

Add this to `pyproject.toml` (create `[tool.uv]` section if needed):

```toml
[[tool.uv.index]]
name = "sesio"
url = "https://sesio-709389331805.d.codeartifact.eu-west-1.amazonaws.com/pypi/devtools/simple/"
explicit = true
```

The `explicit = true` means packages only come from this index when explicitly specified.

### Step 4: Configure Package Sources (If Needed)

If the user wants to add a specific package from sesio (like `sesio-doc-publisher`), add:

```toml
[tool.uv.sources]
sesio-doc-publisher = { index = "sesio" }
```

### Step 5: Show Authentication Instructions

Tell the user how to authenticate:

```bash
# Set these environment variables before running uv commands:
export UV_INDEX_SESIO_USERNAME=aws
export UV_INDEX_SESIO_PASSWORD=$(aws codeartifact get-authorization-token --domain sesio --query authorizationToken --output text)

# Then run uv sync or uv add as normal
uv sync
```

The token is valid for 12 hours. Re-run the export command to refresh.

## Quick Reference

| SSM Parameter | Purpose |
|--------------|---------|
| `/sesio/production/codeartifact/sesio/pip_index_url` | PyPI simple index URL (for installing) |
| `/sesio/production/codeartifact/sesio/repository/devtools/twine_repository_url` | Twine upload URL (for publishing) |

## Example Complete Configuration

```toml
[project]
name = "my-project"
dependencies = [
    "sesio-doc-publisher>=0.1.0",
]

[[tool.uv.index]]
name = "sesio"
url = "https://sesio-709389331805.d.codeartifact.eu-west-1.amazonaws.com/pypi/devtools/simple/"
explicit = true

[tool.uv.sources]
sesio-doc-publisher = { index = "sesio" }
```
