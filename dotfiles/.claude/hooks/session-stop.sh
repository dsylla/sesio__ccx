#!/usr/bin/env bash
# Lightweight stop hook: warn about open clocks in plans.org

# Ring terminal bell to trigger urgency hint (highlights workspace in qtile bar)
printf '\a' > /dev/tty
PLANS="$HOME/org/plans.org"
if [ -f "$PLANS" ]; then
    OPEN_CLOCKS=$(grep -n 'CLOCK: \[.*\]$' "$PLANS" | head -3)
    if [ -n "$OPEN_CLOCKS" ]; then
        echo "Open clock entries found in plans.org — run /plan-tracker to close them"
    fi
fi
