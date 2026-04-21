#!/bin/bash
clean=0; dirty=0
for dir in ~/Work/sesio/*/; do
  [ -d "$dir/.git" ] || continue
  if [ -z "$(git -C "$dir" status --porcelain 2>/dev/null)" ]; then
    ((clean++))
  else
    ((dirty++))
  fi
done
echo "Clean: $clean, Dirty: $dirty"
