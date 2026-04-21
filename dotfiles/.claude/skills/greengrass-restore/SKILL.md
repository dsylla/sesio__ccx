---
name: greengrass-restore
description: >
  Restore a Greengrass node from a backup directory. Reapplies saved deployments
  and shadow from a Phase 0 backup, with staggered deployment and verification.
tools: Bash, Read, Write, Edit, Glob, Grep
args: >
  --node <thing-name> [--backup <path>]
---

# greengrass-restore — Restore from Backup

## Constants

```
AWS_REGION        = eu-west-1
AWS_ACCOUNT_ID    = 709389331805
AWS_PROFILE       = sesio__euwest1
```

## Usage

```
/greengrass-restore --node <thing> [--backup <path>]
```

If `--backup` not specified, list available backups in `__BACKUPS/$NODE/` and let user choose.

---

## RS.1 Read backup directory

```bash
ls __BACKUPS/$NODE/
```

List contents: deployment JSONs, shadow, installed components, zwave store (if present).
Show the `operations.md` summary for context on what happened during the original migration.

## RS.2 Show restore plan

For each saved deployment JSON, compare with current deployment:
- Show version diff (backup version vs current version)
- Show shadow diff (backup shadow vs current shadow)

## RS.3 Confirm restore

**GATE**: User confirms what will be restored.

## RS.4 Reapply deployments

For each group deployment in backup:
```bash
COMPONENTS=$(jq '.components' "$BACKUP_DIR/deployments/$GROUP.json")

aws greengrassv2 create-deployment --region $AWS_REGION \
  --target-arn "arn:aws:iot:$AWS_REGION:$AWS_ACCOUNT_ID:thinggroup/$GROUP" \
  --deployment-name "$GROUP" \
  --components "$COMPONENTS"
```

Deploy staggered (same wave order as provision) to avoid overwhelming the node.

## RS.5 Reapply shadow

```bash
PAYLOAD=$(jq '{state: {desired: .state.desired}}' "$BACKUP_DIR/shadow-node-config.json")

aws iot-data update-thing-shadow --thing-name $NODE --shadow-name node-config \
  --region $AWS_REGION --payload "$PAYLOAD"
```

## RS.6 Verify

Run full verification:

### Deployment convergence
```bash
aws greengrassv2 list-effective-deployments --core-device-thing-name $NODE
```
Poll until no IN_PROGRESS/QUEUED. Timeout: 10 min.

### Installed components
```bash
aws greengrassv2 list-installed-components --core-device-thing-name $NODE
```
Verify each is RUNNING with correct version.

### MQTT listener
Subscribe to `devices/$NODE/#`, monitor for component telemetry. Timeout: 5 min.

### SSH log tailing (after VPN confirmed up)
Use `gg-logs --component <kind> --node <thing> --compact` per component.

---

## Backup Directory Structure

```
__BACKUPS/<thing-name>/<date-time>/
├── deployments/
│   ├── sesio-platform.json
│   ├── sesio-io-led.json
│   └── ...
├── shadow-node-config.json
├── installed-components.json
├── operations.md
└── zwave-store-*.tar.gz (optional)
```

## Help Text

```
Usage:
  /greengrass-restore --node <thing> [--backup <path>]

Examples:
  /greengrass-restore --node 10000000e3852b9c
  /greengrass-restore --node 10000000e3852b9c --backup __BACKUPS/10000000e3852b9c/2026-03-07-1430
```
