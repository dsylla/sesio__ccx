---
name: greengrass-upgrade
description: >
  Upgrade component versions in Greengrass group deployments. Manual pipeline
  with pre-flight, version discovery, deployment creation, convergence polling,
  verification, and reporting.
tools: Bash, Read, Write, Edit, Glob, Grep
args: >
  --target-node <thing> [--ref-node <node>] [--dry-run] [--log <path>]
  --target-group <group> --component <name> --version <ver> [--dry-run] [--log <path>]
---

# greengrass-upgrade — Component Version Upgrade

## Constants

```
AWS_REGION        = eu-west-1
AWS_ACCOUNT_ID    = 709389331805
AWS_PROFILE       = sesio__euwest1
DEFAULT_REF_NODE  = 1000000090e04b5c
```

## Pipeline

```
Phase 1: Pre-flight → Phase 2: Plan → Phase 3: Apply → Phase 4: Shadow sync → Phase 5: Verify → Phase 6: Report
```

---

## Phase 1: Pre-flight

### 1.1 Verify AWS credentials
```bash
export AWS_PROFILE=sesio__euwest1
aws sts get-caller-identity
```

### 1.2 Read node attributes
```bash
aws iot describe-thing --thing-name $NODE
```

### 1.3 Read current groups
```bash
aws iot list-thing-groups-for-thing --thing-name $NODE
```

### 1.4 Check node connectivity
```bash
aws iot search-index --query-string "thingName:$NODE"
```
Check `connectivity.connected` field. Do NOT use `get-core-device` HEALTHY/UNHEALTHY.

### 1.5 Mode-specific pre-flight

**--target-node**: Discover all group deployments, query latest published versions,
show version comparison table (per-group, by layer). Flag UPDATE / SAME / AHEAD.

**--target-group**: Fetch group's deployment, verify component exists,
verify version exists as published. Find affected aggregates.

### 1.6 Event logging setup
If `--log` is set:
```python
from ggtools.events import EventType, EventWriter
writer = EventWriter(Path(args.log))
writer.emit(EventType.OPERATION_START, "pre-flight", {
    "type": "upgrade",
    "node": NODE,
    "component": COMPONENT, "version": VERSION,
})
```

**GATE**: User confirms plan before proceeding.

---

## Phase 2: Plan

### --target-node
For each group with version updates:
- Show current → new version
- User selects which updates to apply (default: all sesio components, ask for AWS components)
- Build per-group revision payloads

### --target-group
Build single revision payload: update `$COMPONENT_NAME` to `$COMPONENT_VERSION`.
Identify affected aggregates to rebuild.

**Dry-run**: Display full plan, then stop.

---

## Phase 3: Apply

For each group to update:
```bash
aws greengrassv2 create-deployment --target-arn $GROUP_ARN --components $COMPONENTS_JSON
```
Order: non-aggregate first, then aggregates.

Emit `DEPLOYMENT_CREATE` events with deployment IDs.

Monitor deployments (poll every 30s, timeout 10 min):
```bash
aws greengrassv2 get-deployment --deployment-id $ID
```
Emit `DEPLOYMENT_CONVERGE` event when complete.

**Dry-run**: Display revision payloads that *would* be applied.

---

## Phase 4: Shadow Sync (optional)

Runs after apply. Ask user if they want to sync shadow.

### 4.1 Fetch both shadows
```bash
aws iot-data get-thing-shadow --thing-name $NODE --shadow-name node-config /tmp/target-shadow.json
aws iot-data get-thing-shadow --thing-name $REF_NODE --shadow-name node-config /tmp/ref-shadow.json
```

### 4.2 Compare dependency sections

Compare these paths (match by `name` field):
- `state.desired.config.dependencies.apt[]`
- `state.desired.config.scripts[]`
- `state.desired.dependencies.apt[]`

**NEVER touch** (per-node specific):
- `config.components`, `config.gpio`, `config.led`, `config.vpn`, `config.usb_devices`

### 4.3 Script arg auto-adaptation

| Script | Arg | Adaptation |
|--------|-----|-----------|
| `setup-sesio-bandwidth` | args[0] = thing name | **Auto-replace with `$NODE`** |
| `setup-sesio-watchdog` | args = [gateway, flag, iface] | **Flag for review** |
| `setup-sesio-apt-repo` | args = [key, secret, region, ...] | Copy as-is |

### 4.4 Apply missing items
1. Show exact merge payload
2. Flag un-adapted script args
3. **GATE**: User confirms
4. Apply via `update-thing-shadow`

---

## Phase 5: Verify

### 5.1 Deployment convergence
```bash
aws greengrassv2 list-effective-deployments --core-device-thing-name $NODE
```
Poll until no IN_PROGRESS/QUEUED. Timeout: 10 min.

### 5.2 Installed components
```bash
aws greengrassv2 list-installed-components --core-device-thing-name $NODE
```
Verify each is RUNNING with correct version.

### 5.3 MQTT listener
Subscribe to `devices/$NODE/#`, monitor for component telemetry. Timeout: 5 min.

### 5.4 SSH log tailing (after VPN confirmed up)
Use `gg-logs --component <kind> --node <thing> --compact` per component.

---

## Phase 6: Report

```bash
gg-report render $LOG_PATH --output-dir /tmp/gg-reports/ --formats json,md,html
gg-report upload $LOG_PATH
gg-report list $NODE
```

---

## Rollback

If any deployment fails:

1. Find most recent COMPLETED deployment for each failed group
2. Show rollback plan. **GATE**: User confirms
3. Restore: `aws greengrassv2 create-deployment --target-arn $GROUP_ARN --components $PREVIOUS_COMPONENTS`
4. Monitor rollback (same polling)
5. Shadow rollback: set added entries to `null`

---

## Help Text

```
Usage:
  /greengrass-upgrade --target-node <thing> [--ref-node <node>] [--dry-run] [--log <path>]
  /greengrass-upgrade --target-group <group> --component <name> --version <ver> [--dry-run] [--log <path>]

Examples:
  /greengrass-upgrade --target-node 10000000e3852b9c
  /greengrass-upgrade --target-group sesio-io-led --component sesio.greengrass.Led --version 2.1.15
```
