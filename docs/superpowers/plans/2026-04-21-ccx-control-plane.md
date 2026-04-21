# ccx — Control Plane (ccxctl + dmenu-ccx) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bash CLI `ccxctl` that drives the instance lifecycle (start/stop/ssh/resize/grow/snapshot/refresh-sg) and a `dmenu-ccx` wrapper for keyboard-only operation. Bound to a qtile keybinding and invoked by the widget on click.

**Architecture:** One script, subcommand dispatch. Reads instance ID from `~/.config/ccx/instance_id` (written by Terraform). All AWS calls go through `aws` CLI with the profile pinned to `sesio__euwest1`. Every command prints a one-liner to stdout and fires `notify-send` on completion (success or failure). Widget-refresh after state changes via `qtile cmd-obj`. Bats tests cover arg parsing, help output, and missing-file guard; AWS-hitting commands have a manual smoke checklist.

**Tech Stack:** Bash 5, aws-cli v2, `dmenu`, libnotify (`notify-send`), qtile CLI (`qtile cmd-obj`), `bats-core` for tests.

**Prereq:** `ccx-terraform-main` plan applied (`~/.config/ccx/instance_id` exists, instance is reachable).

---

## File Structure

```
sesio__ccx/
└── control-plane/
    ├── bin/
    │   ├── ccxctl              # executable
    │   └── dmenu-ccx           # executable
    ├── tests/
    │   └── ccxctl.bats
    └── README.md
```

Install: symlink `control-plane/bin/{ccxctl,dmenu-ccx}` into `~/.local/bin/`.

---

### Task 1: Scaffold ccxctl (config, helpers, dispatch, help)

**Files:**
- Create: `control-plane/bin/ccxctl`

- [ ] **Step 1: Create dirs**

```bash
mkdir -p /home/david/Work/sesio/sesio__ccx/control-plane/{bin,tests}
```

- [ ] **Step 2: Write skeleton**

File `/home/david/Work/sesio/sesio__ccx/control-plane/bin/ccxctl`:

```bash
#!/usr/bin/env bash
# ccxctl - control plane for the ccx coding station
set -euo pipefail

# --- config ---------------------------------------------------------------
: "${AWS_PROFILE:=sesio__euwest1}"
export AWS_PROFILE
REGION="${AWS_REGION:-eu-west-1}"
HOSTNAME_FQDN="${CCX_HOSTNAME:-ccx.dsylla.sesio.io}"
INSTANCE_ID_FILE="${CCX_INSTANCE_ID_FILE:-$HOME/.config/ccx/instance_id}"
WIDGET_NAME="${CCX_WIDGET_NAME:-ccx_status}"
LOG_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/ccx"
mkdir -p "$LOG_DIR"

# --- helpers --------------------------------------------------------------
log()    { printf '%s\n' "$*"; }
notify() { command -v notify-send >/dev/null && notify-send "$@" || true; }
die()    { log "error: $*" >&2; notify -u critical "ccx error" "$*"; exit 1; }

instance_id() {
  [[ -r "$INSTANCE_ID_FILE" ]] || die "instance id file missing: $INSTANCE_ID_FILE"
  cat "$INSTANCE_ID_FILE"
}

aws_ec2() { aws ec2 --region "$REGION" --output json "$@"; }

refresh_widget() {
  command -v qtile >/dev/null || return 0
  qtile cmd-obj -o widget "$WIDGET_NAME" -f force_update 2>/dev/null || true
}

usage() {
  cat <<'EOF'
ccxctl — control the ccx coding station

Usage: ccxctl <subcommand> [args]

  status              Print state, type, ip, uptime, instance id
  start               Start the instance, wait for running
  stop                Stop the instance, wait for stopped
  ssh [args...]       SSH to the hostname (passes args through to ssh)
  refresh-sg          Update SG ingress to current public /32
  resize [TYPE]       Change instance type (stopped state only).
                      No TYPE → dmenu of common types.
  grow-home [GB]      Grow home volume; resize2fs over SSH. No GB → dmenu.
  grow-root [GB]      Grow root volume; growpart + resize2fs over SSH. No GB → dmenu.
  snapshot [NOTE]     Snapshot the home volume, tagged with date + NOTE.
  menu                State-aware dmenu; re-execs self with chosen subcommand.

Env overrides:
  AWS_PROFILE=sesio__euwest1
  AWS_REGION=eu-west-1
  CCX_HOSTNAME=ccx.dsylla.sesio.io
  CCX_INSTANCE_ID_FILE=~/.config/ccx/instance_id
  CCX_WIDGET_NAME=ccx_status
EOF
}

# (subcommand functions are appended by subsequent tasks)

# --- subcommand dispatch --------------------------------------------------
cmd="${1:-}"; shift || true
case "$cmd" in
  ""|-h|--help|help) usage ;;
  status)       cmd_status "$@" ;;
  start)        cmd_start "$@" ;;
  stop)         cmd_stop "$@" ;;
  ssh)          cmd_ssh "$@" ;;
  refresh-sg)   cmd_refresh_sg "$@" ;;
  resize)       cmd_resize "$@" ;;
  grow-home)    cmd_grow_home "$@" ;;
  grow-root)    cmd_grow_root "$@" ;;
  snapshot)     cmd_snapshot "$@" ;;
  menu)         cmd_menu "$@" ;;
  *)            usage; exit 2 ;;
esac
```

- [ ] **Step 3: Make executable**

Run: `chmod +x /home/david/Work/sesio/sesio__ccx/control-plane/bin/ccxctl`

- [ ] **Step 4: Help works (subcommands are undefined yet — that's fine)**

Run: `/home/david/Work/sesio/sesio__ccx/control-plane/bin/ccxctl --help`
Expected: prints usage; exits 0.

---

### Task 2: `status`

**Files:**
- Modify: `control-plane/bin/ccxctl`

- [ ] **Step 1: Insert `cmd_status`**

Insert into `/home/david/Work/sesio/sesio__ccx/control-plane/bin/ccxctl` **before** the `# --- subcommand dispatch ---` comment:

```bash
cmd_status() {
  local id; id=$(instance_id)
  local json; json=$(aws_ec2 describe-instances --instance-ids "$id" --query 'Reservations[0].Instances[0]')
  local state launch type ip
  state=$(jq -r '.State.Name'        <<<"$json")
  launch=$(jq -r '.LaunchTime // ""' <<<"$json")
  type=$(jq -r '.InstanceType'       <<<"$json")
  ip=$(jq -r '.PublicIpAddress // "-"' <<<"$json")

  local uptime=""
  if [[ "$state" == "running" && -n "$launch" ]]; then
    local secs
    secs=$(( $(date +%s) - $(date -d "$launch" +%s) ))
    printf -v uptime '%dh%02dm' $((secs/3600)) $((secs%3600/60))
  fi

  printf '%s %s %s %s %s\n' "$state" "$type" "$ip" "$uptime" "$id"
}
```

- [ ] **Step 2: Smoke**

Run: `/home/david/Work/sesio/sesio__ccx/control-plane/bin/ccxctl status`
Expected: a line like `running t4g.xlarge 1.2.3.4 0h14m i-0abc...` (or `stopped t4g.xlarge - - i-0abc...`).

---

### Task 3: `start`

**Files:**
- Modify: `control-plane/bin/ccxctl`

- [ ] **Step 1: Append**

Append to the function region (still before the dispatch case):

```bash
cmd_start() {
  local id; id=$(instance_id)
  log "starting $id ..."
  aws_ec2 start-instances --instance-ids "$id" >/dev/null
  aws ec2 --region "$REGION" wait instance-running --instance-ids "$id"
  refresh_widget
  notify "ccx" "instance started"
  cmd_status
}
```

- [ ] **Step 2: Smoke (compute-cost-incurring — only when ready)**

Run: `ccxctl start`
Expected: prints `starting i-…`, blocks ~30–60s, prints `running …`.

---

### Task 4: `stop`

**Files:**
- Modify: `control-plane/bin/ccxctl`

- [ ] **Step 1: Append**

```bash
cmd_stop() {
  local id; id=$(instance_id)
  log "stopping $id ..."
  aws_ec2 stop-instances --instance-ids "$id" >/dev/null
  aws ec2 --region "$REGION" wait instance-stopped --instance-ids "$id"
  refresh_widget
  notify "ccx" "instance stopped"
  cmd_status
}
```

- [ ] **Step 2: Smoke**

Run: `ccxctl stop`
Expected: prints `stopping i-…`, blocks ~60–120s, prints `stopped …`.

---

### Task 5: `ssh`

**Files:**
- Modify: `control-plane/bin/ccxctl`

- [ ] **Step 1: Append**

```bash
cmd_ssh() {
  exec ssh "david@${HOSTNAME_FQDN}" "$@"
}
```

- [ ] **Step 2: Smoke (instance must be running + SG must admit your IP)**

Run: `ccxctl ssh -o ConnectTimeout=5 'echo ok'`
Expected: prints `ok`.

---

### Task 6: `refresh-sg`

**Files:**
- Modify: `control-plane/bin/ccxctl`

- [ ] **Step 1: Append**

```bash
cmd_refresh_sg() {
  local id; id=$(instance_id)
  local sg_id
  sg_id=$(aws_ec2 describe-instances --instance-ids "$id" \
    --query 'Reservations[0].Instances[0].SecurityGroups[0].GroupId' --output text)

  local new_cidr
  new_cidr="$(curl -fsSL https://checkip.amazonaws.com | tr -d '[:space:]')/32"
  [[ "$new_cidr" =~ ^[0-9.]+/32$ ]] || die "could not discover public ip"
  log "current public ip: $new_cidr"

  local existing
  existing=$(aws_ec2 describe-security-groups --group-ids "$sg_id" \
    --query 'SecurityGroups[0].IpPermissions[?FromPort==`22`].IpRanges[].CidrIp' --output text)

  for c in $existing; do
    if [[ "$c" != "$new_cidr" ]]; then
      log "revoking stale cidr $c"
      aws_ec2 revoke-security-group-ingress --group-id "$sg_id" \
        --protocol tcp --port 22 --cidr "$c" >/dev/null
    fi
  done

  if ! grep -qxF "$new_cidr" <<<"$existing"; then
    log "authorizing $new_cidr"
    aws_ec2 authorize-security-group-ingress --group-id "$sg_id" \
      --protocol tcp --port 22 --cidr "$new_cidr" >/dev/null
  fi

  notify "ccx" "SG refreshed to $new_cidr"
}
```

- [ ] **Step 2: Smoke**

Run: `ccxctl refresh-sg`
Expected: prints `current public ip: X.X.X.X/32`; at least one of `revoking stale cidr` or `authorizing`; `notify-send` popup. Then `ccxctl ssh -o ConnectTimeout=5 true` exits 0.

---

### Task 7: `resize`

**Files:**
- Modify: `control-plane/bin/ccxctl`

- [ ] **Step 1: Append**

```bash
COMMON_TYPES=(t4g.small t4g.medium t4g.large t4g.xlarge t4g.2xlarge c7g.xlarge c7g.2xlarge c7g.4xlarge m7g.xlarge m7g.2xlarge r7g.xlarge)

cmd_resize() {
  local id; id=$(instance_id)
  local new_type="${1:-}"
  if [[ -z "$new_type" ]]; then
    new_type=$(printf '%s\n' "${COMMON_TYPES[@]}" | dmenu -p "ccx resize:") || return 0
  fi
  [[ -n "$new_type" ]] || die "no instance type chosen"

  local state
  state=$(aws_ec2 describe-instances --instance-ids "$id" --query 'Reservations[0].Instances[0].State.Name' --output text)
  [[ "$state" == "stopped" ]] || die "resize requires stopped state (currently: $state)"

  log "resizing $id -> $new_type"
  aws_ec2 modify-instance-attribute --instance-id "$id" --instance-type "{\"Value\":\"$new_type\"}" >/dev/null
  notify "ccx" "resized to $new_type"
}
```

- [ ] **Step 2: Smoke (requires stopped state)**

```bash
ccxctl stop
ccxctl resize t4g.large
ccxctl status    # expect: stopped t4g.large …
ccxctl resize t4g.xlarge  # reset
```

---

### Task 8: `grow-home`

**Files:**
- Modify: `control-plane/bin/ccxctl`

- [ ] **Step 1: Append**

```bash
cmd_grow_home() {
  local id; id=$(instance_id)
  local new_gb="${1:-}"
  if [[ -z "$new_gb" ]]; then
    new_gb=$(printf '%s\n' 150 200 300 500 750 1000 | dmenu -p "ccx grow home GB:") || return 0
  fi
  [[ "$new_gb" =~ ^[0-9]+$ ]] || die "invalid size: $new_gb"

  local vol_id
  vol_id=$(aws_ec2 describe-instances --instance-ids "$id" \
    --query "Reservations[0].Instances[0].BlockDeviceMappings[?DeviceName=='/dev/sdh'].Ebs.VolumeId" \
    --output text)
  [[ -n "$vol_id" && "$vol_id" != None ]] || die "home volume not found at /dev/sdh"

  local current
  current=$(aws_ec2 describe-volumes --volume-ids "$vol_id" --query 'Volumes[0].Size' --output text)
  (( new_gb > current )) || die "requested size $new_gb GB <= current $current GB"

  log "growing $vol_id: $current -> $new_gb GB"
  aws_ec2 modify-volume --volume-id "$vol_id" --size "$new_gb" >/dev/null

  while :; do
    local mstate
    mstate=$(aws_ec2 describe-volumes-modifications --volume-ids "$vol_id" \
      --query 'VolumesModifications[0].ModificationState' --output text 2>/dev/null || echo "")
    [[ "$mstate" != "modifying" ]] && break
    sleep 5
  done

  local stripped="${vol_id#vol-}"
  ssh "david@${HOSTNAME_FQDN}" "sudo resize2fs /dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_vol-${stripped}"
  notify "ccx" "home grown to $new_gb GB"
}
```

- [ ] **Step 2: Smoke (only if comfortable — EBS size is one-way)**

Skip for now unless actually needed.

---

### Task 9: `grow-root`

**Files:**
- Modify: `control-plane/bin/ccxctl`

- [ ] **Step 1: Append**

```bash
cmd_grow_root() {
  local id; id=$(instance_id)
  local new_gb="${1:-}"
  if [[ -z "$new_gb" ]]; then
    new_gb=$(printf '%s\n' 40 50 60 80 100 | dmenu -p "ccx grow root GB:") || return 0
  fi
  [[ "$new_gb" =~ ^[0-9]+$ ]] || die "invalid size: $new_gb"

  local root_dev vol_id
  root_dev=$(aws_ec2 describe-instances --instance-ids "$id" \
    --query 'Reservations[0].Instances[0].RootDeviceName' --output text)
  vol_id=$(aws_ec2 describe-instances --instance-ids "$id" \
    --query "Reservations[0].Instances[0].BlockDeviceMappings[?DeviceName=='${root_dev}'].Ebs.VolumeId" \
    --output text)
  [[ -n "$vol_id" ]] || die "root volume not found"

  local current
  current=$(aws_ec2 describe-volumes --volume-ids "$vol_id" --query 'Volumes[0].Size' --output text)
  (( new_gb > current )) || die "requested size $new_gb GB <= current $current GB"

  log "growing root $vol_id: $current -> $new_gb GB"
  aws_ec2 modify-volume --volume-id "$vol_id" --size "$new_gb" >/dev/null

  while :; do
    local mstate
    mstate=$(aws_ec2 describe-volumes-modifications --volume-ids "$vol_id" \
      --query 'VolumesModifications[0].ModificationState' --output text 2>/dev/null || echo "")
    [[ "$mstate" != "modifying" ]] && break
    sleep 5
  done

  ssh "david@${HOSTNAME_FQDN}" "sudo growpart /dev/nvme0n1 1 && sudo resize2fs /dev/nvme0n1p1"
  notify "ccx" "root grown to $new_gb GB"
}
```

- [ ] **Step 2: Smoke (skip for v1 unless root fills up)**

30 GB root is plenty for v1.

---

### Task 10: `snapshot`

**Files:**
- Modify: `control-plane/bin/ccxctl`

- [ ] **Step 1: Append**

```bash
cmd_snapshot() {
  local id; id=$(instance_id)
  local note="${1:-manual}"

  local vol_id
  vol_id=$(aws_ec2 describe-instances --instance-ids "$id" \
    --query "Reservations[0].Instances[0].BlockDeviceMappings[?DeviceName=='/dev/sdh'].Ebs.VolumeId" \
    --output text)
  [[ -n "$vol_id" && "$vol_id" != None ]] || die "home volume not found"

  local date_tag; date_tag=$(date -u +%Y-%m-%d-%H%M%S)
  log "snapshotting $vol_id ..."
  local snap_id
  snap_id=$(aws_ec2 create-snapshot --volume-id "$vol_id" \
    --description "ccx home snapshot $date_tag: $note" \
    --tag-specifications "ResourceType=snapshot,Tags=[{Key=Project,Value=ccx},{Key=Date,Value=$date_tag},{Key=Note,Value=$note}]" \
    --query 'SnapshotId' --output text)
  log "$snap_id"
  notify "ccx" "snapshot started: $snap_id"
}
```

- [ ] **Step 2: Smoke**

Run: `ccxctl snapshot "smoke test"`
Expected: prints `snap-…`.

Verify: `AWS_PROFILE=sesio__euwest1 aws ec2 describe-snapshots --snapshot-ids <snap-id> --query 'Snapshots[0].State' --output text`
Expected: `pending` at first, then `completed`.

---

### Task 11: `menu`

**Files:**
- Modify: `control-plane/bin/ccxctl`

- [ ] **Step 1: Append**

```bash
cmd_menu() {
  local id; id=$(instance_id)
  local state
  state=$(aws_ec2 describe-instances --instance-ids "$id" --query 'Reservations[0].Instances[0].State.Name' --output text)

  local -a actions
  case "$state" in
    running)
      actions=(stop ssh refresh-sg snapshot status)
      ;;
    stopped)
      actions=(start resize grow-home grow-root snapshot status)
      ;;
    pending|stopping|shutting-down)
      actions=(status)
      ;;
    *)
      actions=(status)
      ;;
  esac

  local choice
  choice=$(printf '%s\n' "${actions[@]}" | dmenu -p "ccx ($state):") || return 0
  [[ -n "$choice" ]] || return 0
  exec "$0" "$choice"
}
```

- [ ] **Step 2: Smoke**

Run: `ccxctl menu`
Expected: dmenu pops with a state-appropriate action set.

---

### Task 12: `dmenu-ccx` wrapper

**Files:**
- Create: `control-plane/bin/dmenu-ccx`

- [ ] **Step 1: Write wrapper**

File `/home/david/Work/sesio/sesio__ccx/control-plane/bin/dmenu-ccx`:

```bash
#!/usr/bin/env bash
# Bind to e.g. mod+c c in qtile; invokes ccxctl menu.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
exec ccxctl menu
```

- [ ] **Step 2: Make executable**

Run: `chmod +x /home/david/Work/sesio/sesio__ccx/control-plane/bin/dmenu-ccx`

---

### Task 13: Bats tests

**Files:**
- Create: `control-plane/tests/ccxctl.bats`

- [ ] **Step 1: Write tests**

File `/home/david/Work/sesio/sesio__ccx/control-plane/tests/ccxctl.bats`:

```bats
#!/usr/bin/env bats

setup() {
  export PATH="$BATS_TEST_DIRNAME/../bin:$PATH"
  export CCX_INSTANCE_ID_FILE="$BATS_TMPDIR/instance_id"
  echo "i-deadbeef" > "$CCX_INSTANCE_ID_FILE"
}

@test "help prints usage" {
  run ccxctl --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"ccxctl — control the ccx coding station"* ]]
}

@test "no args prints usage" {
  run ccxctl
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: ccxctl"* ]]
}

@test "unknown subcommand exits 2" {
  run ccxctl bogus
  [ "$status" -eq 2 ]
  [[ "$output" == *"Usage: ccxctl"* ]]
}

@test "missing instance id file exits non-zero" {
  export CCX_INSTANCE_ID_FILE="$BATS_TMPDIR/nope"
  run ccxctl status
  [ "$status" -ne 0 ]
  [[ "$output" == *"instance id file missing"* ]]
}
```

- [ ] **Step 2: Install bats if needed**

Run: `command -v bats || sudo apt-get install -y bats`
Expected: bats on PATH.

- [ ] **Step 3: Run tests**

Run: `cd /home/david/Work/sesio/sesio__ccx && bats control-plane/tests/ccxctl.bats`
Expected: `4 tests, 0 failures`.

---

### Task 14: Install + README

**Files:**
- Create: `control-plane/README.md`

- [ ] **Step 1: Install symlinks**

```bash
mkdir -p ~/.local/bin
ln -sf /home/david/Work/sesio/sesio__ccx/control-plane/bin/ccxctl     ~/.local/bin/ccxctl
ln -sf /home/david/Work/sesio/sesio__ccx/control-plane/bin/dmenu-ccx  ~/.local/bin/dmenu-ccx
```

- [ ] **Step 2: Verify on PATH**

Run: `command -v ccxctl && command -v dmenu-ccx`
Expected: both resolve to `~/.local/bin/…`.

- [ ] **Step 3: Add qtile keybinding (manual, user's qtile config)**

Add to the user's qtile `config.py` keybindings list:

```python
Key([mod, "control"], "c", lazy.spawn("dmenu-ccx")),
```

Reload qtile: `qtile cmd-obj -o cmd -f reload_config`.

- [ ] **Step 4: Write README**

File `/home/david/Work/sesio/sesio__ccx/control-plane/README.md`:

````markdown
# ccxctl + dmenu-ccx

Bash control plane for the ccx coding station. Reads instance ID from
`~/.config/ccx/instance_id` (written by `terraform apply`).

## Install

```bash
ln -sf $(pwd)/bin/ccxctl    ~/.local/bin/ccxctl
ln -sf $(pwd)/bin/dmenu-ccx ~/.local/bin/dmenu-ccx
```

## Subcommands

| Command | Purpose |
|---|---|
| `ccxctl status` | state, type, ip, uptime |
| `ccxctl start` | start instance, wait |
| `ccxctl stop` | stop instance, wait |
| `ccxctl ssh` | ssh to the hostname (passes args through) |
| `ccxctl refresh-sg` | update SG ingress to current public /32 |
| `ccxctl resize [TYPE]` | change instance type (stopped only) |
| `ccxctl grow-home [GB]` | grow /home volume + resize2fs via ssh |
| `ccxctl grow-root [GB]` | grow root volume + growpart + resize2fs via ssh |
| `ccxctl snapshot [NOTE]` | snapshot home volume, tagged |
| `ccxctl menu` | state-aware dmenu, re-execs the chosen subcommand |

## Smoke checklist

- [ ] `ccxctl status` prints a sane line
- [ ] `ccxctl start` → `status` shows `running`
- [ ] `ccxctl ssh true` exits 0
- [ ] `ccxctl refresh-sg` updates SG when public IP changes
- [ ] `ccxctl snapshot "smoke"` creates a snapshot
- [ ] `ccxctl stop` → `status` shows `stopped`
- [ ] `ccxctl resize t4g.large` works when stopped; back to xlarge after

## Tests

```bash
bats tests/ccxctl.bats
```
````

---

### Task 15: Commit

- [ ] **Step 1: Review**

Run: `cd /home/david/Work/sesio/sesio__ccx && git status && git diff --cached --stat`
Expected: files under `control-plane/`.

- [ ] **Step 2: Commit**

Invoke `/commit`. Suggested message: `feat(control-plane): ccxctl + dmenu-ccx with bats tests`.

---

## Done when

1. `bats control-plane/tests/ccxctl.bats` passes (4/4).
2. `ccxctl --help`, `ccxctl status`, `ccxctl start`, `ccxctl stop`, `ccxctl ssh`, `ccxctl refresh-sg`, `ccxctl snapshot` all work against the real instance (manual smoke).
3. `ccxctl menu` pops dmenu and re-execs the chosen subcommand.
4. `dmenu-ccx` bound to `mod+ctrl+c` (or equivalent) triggers the same.
