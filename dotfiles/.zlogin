# Sourced by zsh on interactive login (after .zshrc). Prints the ccx motd —
# doing it here (instead of /etc/update-motd.d/) keeps $HOME correct and
# the output fresh on every login rather than cached in /run/motd.dynamic.
if [[ -o interactive ]] && command -v ccxctl >/dev/null 2>&1; then
  ccxctl motd 2>/dev/null || true
fi
