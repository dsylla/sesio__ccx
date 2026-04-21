---
name: publish-codeartifact
description: Build and publish a Python package to sesio's private CodeArtifact PyPI repository. Handles authentication, building, and publishing via uv. Use when user says "publish to codeartifact", "push to codeartifact", "publish package", or needs to upload a Python package to the internal registry.
user_invocable: true
---

# Publish to CodeArtifact

Build and publish a Python package to sesio's private CodeArtifact PyPI repository.

## Arguments

- (none) - Build and publish the current project
- `--dry-run` - Build only, show what would be published without uploading
- `--build-only` - Build wheel/sdist without publishing

## Configuration

- **Domain**: `sesio`
- **Repository**: `devtools`
- **Region**: `eu-west-1`
- **Account ID**: `709389331805`
- **AWS Profile**: `sesio__euwest1`
- **Publish URL**: `https://sesio-709389331805.d.codeartifact.eu-west-1.amazonaws.com/pypi/devtools/`

## Instructions

### Step 1: Verify Project

1. Check that `pyproject.toml` exists and has a valid `[project]` section with `name` and `version`
2. Read the version from pyproject.toml
3. Show the user what will be published: package name, version

### Step 2: Run Tests (if available)

Check if tests exist and offer to run them:

```bash
uv run pytest --tb=short -q
```

If tests fail, warn the user and ask if they want to proceed anyway.

### Step 3: Build the Package

```bash
# Clean previous builds
rm -rf dist/

# Build wheel and sdist
uv build
```

Verify the build output exists in `dist/`.

### Step 4: Authenticate to CodeArtifact

```bash
export AWS_PROFILE=sesio__euwest1

# Get authorization token (valid for 12 hours)
CODEARTIFACT_AUTH_TOKEN=$(aws codeartifact get-authorization-token \
    --domain sesio \
    --domain-owner 709389331805 \
    --region eu-west-1 \
    --query authorizationToken \
    --output text)
```

If authentication fails:
- Check that `AWS_PROFILE=sesio__euwest1` is set
- Suggest running `aws sso login --profile sesio__euwest1` if using SSO
- Check that the user has `codeartifact:GetAuthorizationToken` permission

### Step 5: Publish

```bash
PUBLISH_URL="https://sesio-709389331805.d.codeartifact.eu-west-1.amazonaws.com/pypi/devtools/"

uv publish \
    --publish-url "$PUBLISH_URL" \
    --username aws \
    --password "$CODEARTIFACT_AUTH_TOKEN"
```

### Step 6: Verify

```bash
# Check the package is available
aws codeartifact list-package-versions \
    --domain sesio \
    --domain-owner 709389331805 \
    --repository devtools \
    --format pypi \
    --package <package-name> \
    --region eu-west-1 \
    --query "versions[0].{version:version,status:status}" \
    --output table
```

### Step 7: Report

Show the user:
- Package name and version published
- CodeArtifact URL
- How to install: `uv add <package-name>` (with CodeArtifact configured)

## Troubleshooting

| Error | Solution |
|-------|----------|
| 401 Unauthorized | Re-authenticate: token may have expired (12h lifetime) |
| 409 Conflict | Version already exists - bump the version first |
| Build fails | Check pyproject.toml build-system configuration |
| Token command fails | Ensure AWS credentials are valid: `aws sts get-caller-identity` |

## Notes

- Always use `uv build` and `uv publish`, never pip/twine
- Tokens are valid for 12 hours
- The `devtools` repository has `pypi-proxy` as upstream (public PyPI cache)
- Publishing requires the CodeBuild service role or developer IAM permissions
