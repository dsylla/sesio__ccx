#!/usr/bin/env bash
# Hook: PreToolUse (Edit|Write)
# Checks if file is in a TDD-opted project and reminds if no test exists.
# NEVER blocks — always exits 0.

set -uo pipefail

# Read JSON from stdin
input="$(cat)"

# Extract file_path from tool_input
file_path="$(echo "$input" | jq -r '.tool_input.file_path // .tool_input.filePath // empty' 2>/dev/null)"

if [[ -z "$file_path" ]]; then
  exit 0
fi

# Resolve to absolute path
if [[ "$file_path" != /* ]]; then
  cwd="$(echo "$input" | jq -r '.cwd // empty' 2>/dev/null)"
  if [[ -n "$cwd" ]]; then
    file_path="$cwd/$file_path"
  fi
fi

# Check if inside a git repo
repo_root="$(git -C "$(dirname "$file_path")" rev-parse --show-toplevel 2>/dev/null)" || exit 0

# Check for TDD opt-in
claude_md="$repo_root/.claude/CLAUDE.md"
if [[ ! -f "$claude_md" ]]; then
  exit 0
fi

if ! grep -q 'tdd: *true' "$claude_md" 2>/dev/null; then
  exit 0
fi

# Get basename and extension
basename="$(basename "$file_path")"
extension="${basename##*.}"

# Excluded extensions
case "$extension" in
  md|rst|txt|toml|yaml|yml|json|cfg|ini|tf|sh|dockerfile) exit 0 ;;
esac

# Excluded filenames
case "$basename" in
  CLAUDE.md|SKILL.md|Makefile|Dockerfile|conftest.py) exit 0 ;;
esac

# Is this a test file?
if [[ "$file_path" == */tests/* ]] || [[ "$basename" == test_* ]] || [[ "$basename" == *_test.* ]]; then
  exit 0
fi

# For Python files: check if test_<name>.py exists anywhere in tests/
if [[ "$extension" == "py" ]]; then
  impl_name="${basename%.py}"
  # Look for test file in the repo
  test_file="$(find "$repo_root/tests" -name "test_${impl_name}.py" -print -quit 2>/dev/null)"
  if [[ -n "$test_file" ]]; then
    # Test exists — no reminder needed
    exit 0
  fi
  # No test file found
  rel_path="${file_path#$repo_root/}"
  echo "TDD: No test found for $rel_path. Consider writing a failing test first."
  exit 0
fi

# For other languages: generic check — look for any test file with matching name
impl_name="${basename%.*}"
test_file="$(find "$repo_root" -path "*/test*" -name "*${impl_name}*" -print -quit 2>/dev/null)"
if [[ -z "$test_file" ]]; then
  rel_path="${file_path#$repo_root/}"
  echo "TDD: No test found for $rel_path. Consider writing a failing test first."
fi

exit 0
