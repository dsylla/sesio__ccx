#!/bin/bash
for dir in ~/Work/sesio/*/; do
  if [ -d "$dir/.git" ]; then
    name=$(basename "$dir")
    git_status=$(git -C "$dir" status --porcelain 2>/dev/null)
    if [ -n "$git_status" ]; then
      staged=$(echo "$git_status" | grep -c '^[MADRC]')
      unstaged=$(echo "$git_status" | grep -c '^.[MADRC]')
      untracked=$(echo "$git_status" | grep -c '^??')
      echo "$name|$staged|$unstaged|$untracked"
    fi
  fi
done
