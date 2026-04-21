---
name: greengrass-ops
description: >
  Unified Greengrass operations — provision (via gg-ops migrate), upgrade, restore.
  Routes to /greengrass-upgrade and /greengrass-restore for manual pipeline modes.
tools: Bash, Read, Write, Edit, Glob, Grep
args: >
  provision --node <thing-name> --template <name> [--dry-run]
  upgrade --target-node <thing> | --target-group <name> --component <name> --version <ver> [--dry-run]
  restore --node <thing-name> [--backup <path>]
  --help
---

# greengrass-ops — Unified Operations Skill

## Mode Routing

| Mode | How | Skill |
|------|-----|-------|
| provision/migrate | `gg-ops migrate` CLI | This skill (below) |
| upgrade | Manual AWS commands | `/greengrass-upgrade` |
| restore | Manual AWS commands | `/greengrass-restore` |

When user invokes `/greengrass-ops upgrade ...`, invoke `/greengrass-upgrade` with the same args.
When user invokes `/greengrass-ops restore ...`, invoke `/greengrass-restore` with the same args.

---

## Provision Mode (via gg-ops migrate)

**IMPORTANT:** Provision/migration operations are handled by `gg-ops migrate`.
Claude MUST use this tool instead of running individual AWS commands.

### Usage

```bash
export AWS_PROFILE=sesio__euwest1

# Always dry-run first
gg-ops migrate --node <thing> --template <template> --dry-run

# Then execute
gg-ops migrate --node <thing> --template <template>
```

### What Claude does:
1. Confirm template choice with user
2. Run `gg-ops migrate --node X --template Y --dry-run` first to preview
3. Show the plan and shadow diff output to user
4. If user approves, run `gg-ops migrate --node X --template Y`
5. Monitor output, flag any PARTIAL or FAILED outcomes
6. If wave fails: show backup path, offer `/greengrass-restore`

### What the tool handles automatically:
- Pre-flight (connectivity via fleet index, NOT core-device status)
- Backup (deployments, shadow, components)
- Shadow sync (diff + merge with human confirmation prompt built-in)
- Wave-by-wave deployment with DM MQTT verification
- Platform-setup → platform SWAP (not stack)
- Convergence polling per wave
- Final verification + report generation

### Claude MUST NOT:
- Run individual AWS IoT/Greengrass commands for standard migrations
- Skip the dry-run step
- Bypass the shadow merge confirmation
- Use `greengrassv2 get-core-device` HEALTHY/UNHEALTHY for connectivity (use fleet index)

### Options
```
--ref-node <node>    Reference node for shadow sync (default: 1000000090e04b5c)
--dry-run            Pre-flight + plan + shadow diff only
--yes                Skip confirmation prompts (except shadow)
--timeout <seconds>  Per-wave convergence timeout (default: 300)
--skip-upload        Don't upload report to S3
--log <path>         Custom JSONL output path
--formats <list>     Report formats (default: json,md,html)
--lang en|fr         Report language
```

---

## Constants

```
AWS_REGION        = eu-west-1
AWS_ACCOUNT_ID    = 709389331805
AWS_PROFILE       = sesio__euwest1
SHADOW_NAME       = node-config
DEFAULT_REF_NODE  = 1000000090e04b5c
DEPLOY_TIMEOUT    = 600  # seconds
MQTT_TIMEOUT      = 300  # seconds
```

## Templates

| Name | Profile | I/O | Logic | Addon |
|------|---------|-----|-------|-------|
| elevator-gpio-full | elevator | laser, inspection-mode, door-gpio, cabin-call-gpio | — | — |
| elevator-zwave | elevator | laser, inspection-mode, door-gpio, zwave, client-device-bridge | cabin-call-zwave | — |
| elevator-gpio-zwave | elevator | laser, inspection-mode, door-gpio, cabin-call-gpio, zwave, client-device-bridge | — | — |
| blink | blink | contact-gpio | — | — |
| blink-movement | blink | contact-gpio | movement | — |
| zero | zero | accelerometer | movement | — |
| lite | lite | bacnet | — | — |

## Group-to-Component Mapping

### Layer 0 — Platform
`sesio-platform-setup` → DependencyManager + AWS infra (bootstrap only)
`sesio-platform` → DependencyManager, VPN, Status, S3FileUploader + AWS infra (full)

### Layer 1 — Profiles (no deployment)
`sesio-profile-elevator`, `sesio-profile-blink`, `sesio-profile-zero`, `sesio-profile-lite`

### Layer 2 — I/O

> **Note:** `sesio-led` is now installed via apt + provisioning script (DependencyManager),
> not as a Greengrass component. The `sesio.greengrass.Led` component and `sesio-io-led`
> group no longer exist.

| Group | Component |
|-------|-----------|
| sesio-io-laser | sesio.greengrass.Laser |
| sesio-io-inspection-mode | sesio.greengrass.InspectionMode |
| sesio-io-door-gpio | sesio.greengrass.DoorGPIO |
| sesio-io-cabin-call-gpio | sesio.greengrass.CabinCallGPIO |
| sesio-io-contact-gpio | sesio.greengrass.ContactGPIO |
| sesio-io-accelerometer | sesio.greengrass.DeviceAccelerometer |
| sesio-io-bacnet | sesio.greengrass.BACnet |
| sesio-io-zwave | sesio.greengrass.Zwave |
| sesio-io-client-device-bridge | aws.greengrass.clientdevices.Auth, .mqtt.bridge, .mqtt.moquette |

### Layer 3 — Logic
| Group | Component |
|-------|-----------|
| sesio-logic-movement | sesio.greengrass.Movement |
| sesio-logic-cabin-call-zwave | sesio.greengrass.CabinCall |

### Layer 4 — Addon
| Group | Component |
|-------|-----------|
| sesio-addon-behavioral | sesio.greengrass.BehavioralAnalysis |
| sesio-addon-audio | sesio.greengrass.Audio |
| sesio-addon-energy | sesio.greengrass.Energy |
| sesio-addon-vibration | sesio.greengrass.DeviceVibration |

### Aggregate Groups
`sesio-aggregate-<sha256[:8]>` — created when node has >10 groups (AWS limit).

## Validation Rules

1. `sesio-io-zwave` **requires** `sesio-io-client-device-bridge`
2. `sesio-logic-cabin-call-zwave` **requires** `sesio-io-zwave`
3. Exactly **one** `sesio-profile-*` per node
4. `sesio-platform` **always required**
5. Flag if node has **both** `sesio-io-cabin-call-gpio` AND `sesio-logic-cabin-call-zwave`

## Execution Principles

1. **Double-check everything**: Re-verify AWS results
2. **Ask when uncertain**: Stop if ambiguous
3. **Never assume, always verify**: Confirm every mutating action

**Uncertainty triggers** (ask before proceeding):
- Node in non-sesio groups (legacy) — ask before removing
- Shadow has unexpected entries — ask before touching
- Node connectivity stale (>6h) — ask if safe
- Script arg looks node-specific — ask before copying
- Component version jumped unexpectedly — confirm intentional

## Known Issues

- **DependencyManager apt-get failure**: Check `pre-apt` scripts (`setup-sesio-apt-repo`)
- **VPN startup order**: VPN depends on DependencyManager; fix DM first
- **Convergence delay**: 1-5 min after group assignment + 2-3 min for DM apt
- **Log access requires VPN**: Use MQTT listener as primary verification
- **Nucleus telemetry**: Reports every 10 min; use `list-installed-components` as fallback
- **Shadow must exist before components start**: Shadow sync before deployment
- **Legacy group cleanup**: Ask before removing non-sesio groups
- **Platform bootstrap**: Always deploy `sesio-platform-setup` first, wait for DM, then swap to `sesio-platform`

## Help Text

```
Usage:
  /greengrass-ops provision --node <thing> --template <template> [--dry-run]
  /greengrass-ops upgrade ...    → delegates to /greengrass-upgrade
  /greengrass-ops restore ...    → delegates to /greengrass-restore

Examples:
  /greengrass-ops provision --node 10000000e3852b9c --template elevator-gpio-zwave
  /greengrass-ops provision --node 10000000cbee57b6 --template blink --dry-run

Report Commands:
  gg-report render <events.jsonl>
  gg-report upload <events.jsonl>
  gg-report list <thing-name>
```
