# ccx — Remote Coding Station on AWS — Design

**Date:** 2026-04-21
**Codename:** ccx (Claude Code eXtension)
**Host:** `ccx.dsylla.sesio.io`
**Author:** David Sylla

## 1. Goal

Provision a personal remote coding station on AWS, reachable at `ccx.dsylla.sesio.io`, managed entirely by Terraform and Ansible, with a local qtile/dmenu control plane on the laptop to start, stop, SSH into, resize, and snapshot it.

The station is an additive extension of the laptop environment: same shell, same tooling, same Claude Code setup, but runs on AWS compute that can scale up when the laptop can't.

## 2. Deliverables

Two repos touched:

1. **New repo: `~/Work/sesio/sesio__ccx/`**
   - `terraform/` — AWS infrastructure (EC2, EBS, EIP, DNS, IAM, SG, state backend)
   - `terraform/bootstrap/` — one-time state bucket + lock table creation (local state)
   - `ansible/` — idempotent playbook that converts a blank Debian 12 arm64 instance into the coding station
   - `dotfiles/` — seeded from the laptop's current `~/.zshrc`, `~/.p10k.zsh`, `~/.tmux.conf`, `~/.gitconfig`
   - `control-plane/bin/` — `ccxctl` shell script + `dmenu-ccx` wrapper
   - `docs/` — this spec, subsequent plans

2. **Extend existing repo: `~/Work/ssdd/ssdd__qtile_widgets/`**
   - Add `ssdd_qtile_widgets/ccx.py` with `CcxStatusWidget`
   - Add `boto3` dependency (optional, conditional import in `__init__.py`)

## 3. AWS architecture

- **Account:** `709389331805` (sesio), **profile** `sesio__euwest1`, **region** `eu-west-1`
- **VPC:** account's default VPC, default public subnet (no custom VPC in v1)
- **Instance:** `t4g.xlarge` (arm64 Graviton, 4 vCPU, 16 GB RAM)
- **AMI:** official Debian 12 arm64, resolved via `aws_ami` data source filtering on owner `136693071363` and name pattern `debian-12-arm64-*`
- **Root volume:** 30 GB gp3, encrypted, `delete_on_termination = true`
- **Home volume:** 100 GB gp3, encrypted, `delete_on_termination = false`, attached at `/dev/sdh`, mounted as `/home`. Separate lifecycle from the instance: rebuilding the OS does not touch home.
- **Elastic IP:** always attached to the instance (even when stopped, so DNS stays stable)
- **Route 53:** A record `ccx.dsylla.sesio.io` → EIP, TTL 60, in the existing `sesio.io` hosted zone (looked up via data source)
- **Security group:** ingress TCP 22 from `var.admin_cidr` only; egress all. No other ports exposed.
- **IAM role:** EC2 instance profile trusting `ec2.amazonaws.com`, attached policy `AdministratorAccess` (v1 — mirrors current human dev perms, can be narrowed later). `aws:SourceArn` condition pins the trust to this instance's ARN.
- **IMDSv2:** required (no v1 fallback), mitigates SSRF to creds.

### Terraform state backend

- `sesio-terraform-state` S3 bucket (new): versioned, SSE-S3 encrypted, public access blocked. Shared with future sesio terraform projects under separate keys.
- `sesio-terraform-state-lock` DynamoDB table (new): `LockID` hash key, pay-per-request billing.
- Bootstrap chicken-and-egg: a `terraform/bootstrap/` sub-project with **local state** creates the bucket + table on first apply, then the main project uses the remote backend with key `ccx/terraform.tfstate`.

## 4. Provisioning flow

### First boot — cloud-init user-data (~60 lines)

1. Wait for the home EBS device to appear. Resolve via `/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_vol-<id>` (stable across reboots; `/dev/nvme1n1` is not guaranteed across attach orderings). The volume ID is templated into user-data by Terraform.
2. If unformatted, `mkfs.ext4` directly on the block device (no partition table — simpler growth semantics, no `growpart` needed). If formatted (reattaching an existing volume), skip.
3. Add to `/etc/fstab` using the `by-id` path with `nofail` and mount at `/home`.
4. `apt-get update && apt-get install -y python3 python3-pip git ansible`.
5. Clone the `sesio__ccx` repo using a deploy key stored in SSM Parameter Store (fetched via the instance IAM role).
6. `ansible-pull -U <repo> ansible/site.yml` runs the full playbook locally.

### Ansible playbook — `ansible/site.yml`

Idempotent and re-runnable. Roles:

| Role | Purpose |
|---|---|
| `base` | apt upgrade, install build-essential, curl, jq, tmux, unzip, fail2ban, unattended-upgrades (security only) |
| `user` | Create user `david` (uid 1000), add to `sudo` and `docker` groups, authorize SSH public key |
| `zsh` | Install zsh, set as default shell; install oh-my-zsh unattended; install p10k theme and plugins (`zsh-autosuggestions`, `zsh-syntax-highlighting`, any additional beyond the ones in the current `~/.zshrc` — `git asdf sudo ruby aws shell-aws-autoprofile`) |
| `dotfiles` | Clone the `dotfiles` repo, symlink `.zshrc`, `.p10k.zsh`, `.tmux.conf`, `.gitconfig` into `/home/david/` |
| `asdf` | Install asdf to `/home/david/.asdf`; install plugins for `python`, `nodejs`, `ruby`; install latest stable of each; set `global` versions |
| `docker` | Install Docker CE from Docker's apt repo (arm64 channel), enable service, add `david` to docker group |
| `claude_code` | `npm install -g @anthropic-ai/claude-code` (Node already present via asdf) |
| `aws_cli` | Install AWS CLI v2 for arm64 |
| `verify` | Final task: check `zsh/docker/asdf/claude/aws` versions, write `/var/log/ccx-provision-ok` marker |

### Updating the box later

SSH in → `cd ~/sesio__ccx && git pull && ansible-playbook ansible/site.yml -i localhost, -c local`. Tagging roles (`--tags zsh`) lets a single concern be re-applied.

## 5. Control plane

### Widget — `ssdd_qtile_widgets/ccx.py`

Follows existing `InLoopPollText` pattern (same as `ClaudeSessionWidget`).

- **Config defaults:** `prefix="ccx "`, `update_interval=30`, `aws_profile="sesio__euwest1"`, `region="eu-west-1"`, `instance_id_file="~/.config/ccx/instance_id"`
- **Click handler:** `Button1` fires `subprocess.Popen(["ccxctl", "menu"])`
- **Display states:**
  - `ccx ● 2h14m` — running (green)
  - `ccx ○` — stopped (dim)
  - `ccx ◐` — pending/starting (yellow)
  - `ccx ◑` — stopping (yellow)
  - `ccx !` — error (red); details logged to `~/.cache/ccx/widget.log`
- **Force refresh:** widget exposes a `force_update()` method, callable from `ccxctl` via `qtile cmd-obj -o widget ccx_status -f force_update` after any state change — avoids 30 s UI lag after clicking "Start".
- **Robustness:** all AWS calls wrapped; expired creds, network errors, missing instance ID file surface as `ccx !` rather than bar crashes.
- **Dependencies:** `boto3` added to `ssdd__qtile_widgets/pyproject.toml`. The import is guarded inside a `try/except ImportError` in `ssdd_qtile_widgets/__init__.py`, consistent with the existing pattern.

### CLI — `ccxctl`

Bash script in `sesio__ccx/control-plane/bin/ccxctl`, symlinked into `~/.local/bin/`. Reads instance ID from `~/.config/ccx/instance_id` (written by `terraform output` on apply).

| Subcommand | Behavior |
|---|---|
| `status` | Print state, uptime, instance type, public IP |
| `start` | `aws ec2 start-instances`, wait for running, refresh widget |
| `stop` | `aws ec2 stop-instances`, wait for stopped |
| `ssh` | Open SSH to `ccx.dsylla.sesio.io` in a new terminal |
| `refresh-sg` | Fetch current public IP; revoke stale rule, authorize current `/32` |
| `resize <type>` | Requires stopped state; `modify-instance-attribute`. No arg → dmenu with common types |
| `grow-home <GB>` | `aws ec2 modify-volume`, wait for `optimizing` state, SSH `resize2fs /dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_vol-<id>`. No arg → dmenu for GB |
| `grow-root <GB>` | `aws ec2 modify-volume` + SSH `growpart /dev/nvme0n1 1 && resize2fs /dev/nvme0n1p1` (root has a partition table because the Debian AMI ships one). No arg → dmenu |
| `snapshot` | `create-snapshot` on home volume, tag with date + optional note |
| `menu` | Entry point: state-aware dmenu of available actions (only offers "Stop/SSH" when running, "Start/Resize/Grow" when stopped, etc.) |

All commands print a single-line status to stdout + `notify-send` on completion. Errors → `notify-send -u critical` + non-zero exit.

### Menu wrapper — `dmenu-ccx`

Thin shell script wrapping `dmenu` with consistent font/style, calls `ccxctl <subcmd>` based on selection. Also bound to a qtile keybinding (e.g. `mod+c c`) for keyboard-only invocation.

## 6. Dotfiles repo

New repo (in-tree as `sesio__ccx/dotfiles/` to start — promotable to a separate repo later without changing consumers).

**Included:** `.zshrc`, `.p10k.zsh`, `.tmux.conf`, `.gitconfig`, `~/.claude/settings.json`, `~/.claude/CLAUDE.md`, `~/.claude/skills/`, `~/.claude/commands/`, `~/.claude/hooks/`.

**Explicitly excluded:** `~/.claude/credentials*`, `~/.claude/projects/` (per-project memory is laptop-specific), `~/.aws/credentials`, anything with tokens.

Claude Code and AWS both authenticate on first use — OAuth / IAM role, no secrets at rest.

## 7. Security

- SSH port 22 only, restricted to a single `/32` managed by `ccxctl refresh-sg`.
- Key-only SSH (`PasswordAuthentication no`, `PermitRootLogin no`), single authorized public key (the existing `sesio-nodes` key from the laptop).
- `fail2ban` for passive defense.
- `unattended-upgrades` for Debian security patches only (no kernel upgrades — those require deliberate reboots).
- IMDSv2 required.
- IAM role trust pinned to instance ARN via `aws:SourceArn`.
- EBS volumes encrypted with AWS-managed KMS (no extra cost).
- No static AWS credentials on the box — role only.
- CloudTrail already on at the account level.

**Accepted risks:** admin-equivalent IAM role on a single-user machine. Compromise of ccx = compromise of the sesio AWS account. Mitigated by stopped-by-default, tight SG, and single authorized key.

## 8. Testing

**Terraform:** `terraform validate` + `tflint` + `tfsec` in a `make check` target. Plan before every apply. No integration tests in v1.

**Ansible:** `ansible-playbook --check` dry-runs; `ansible-lint` in `make check`. The `verify` role writes `/var/log/ccx-provision-ok` confirming all tool installs succeeded; `ccxctl status` surfaces this.

**Widget:** pytest for pure functions (state classification, uptime formatting, widget text rendering), mocking boto3 with `moto` or `unittest.mock`. Follows the existing `ssdd__qtile_widgets/tests/` pattern.

**ccxctl:** Bats for help/subcommand parsing. Manual smoke test checklist in the README for AWS-interacting commands.

## 9. Error handling

- Every AWS call in `ccxctl` checks exit code, surfaces via `notify-send -u critical`.
- Widget catches all exceptions, displays `ccx !`, logs to `~/.cache/ccx/widget.log`.
- `resize2fs` (and `growpart` for root) via SSH: check exit codes. Roll forward only — EBS size increases are one-way (no shrinking).
- Cloud-init → Ansible failures visible in `/var/log/cloud-init-output.log` and `/var/log/ansible-pull.log`; the `verify` role's marker file is the single source of truth for provisioning success.

## 10. Cost (running 50 h/month, stopped otherwise)

| Item | Monthly |
|---|---|
| `t4g.xlarge` compute, 50 h | ~€5.50 |
| EBS root 30 GB gp3 | ~€2.50 |
| EBS home 100 GB gp3 | ~€8.00 |
| EIP (stopped ~650 h) | ~€3.20 |
| Route 53 zone (existing) | €0 marginal |
| S3 state | <€0.10 |
| **Total** | **~€19** |

Running 24/7 would be ~€105/mo. "Stopped by default" is the single biggest lever.

## 11. Explicit non-goals (v1)

- Multi-user access
- Tailscale or VPN layer
- Automatic EBS snapshot schedule (DLM) — manual via `ccxctl snapshot`
- Auto-stop-on-idle
- Packer-built custom AMI
- Custom VPC (use default VPC)
- Narrower IAM role than `AdministratorAccess`
- Arch Linux on the server (Debian 12 instead; can be revisited once provisioning is proven)

All are doable later without design changes.

## 12. File layout

```
~/Work/sesio/sesio__ccx/
├── .awsprofile                    # contains: sesio__euwest1
├── README.md
├── docs/
│   └── superpowers/
│       ├── specs/
│       │   └── 2026-04-21-ccx-coding-station-design.md   # this file
│       └── plans/                 # implementation plan lands here
├── terraform/
│   ├── bootstrap/                 # one-time: state bucket + lock table, local state
│   │   ├── main.tf
│   │   └── outputs.tf
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   ├── providers.tf               # S3 backend config points at sesio-terraform-state
│   └── modules/                   # only if a clear boundary emerges; start flat
├── ansible/
│   ├── site.yml
│   ├── ansible.cfg
│   ├── inventory                  # 'localhost ansible_connection=local' for ansible-pull
│   └── roles/
│       ├── base/
│       ├── user/
│       ├── zsh/
│       ├── dotfiles/
│       ├── asdf/
│       ├── docker/
│       ├── claude_code/
│       ├── aws_cli/
│       └── verify/
├── dotfiles/                      # copies of current laptop dotfiles, edited for portability
│   ├── .zshrc
│   ├── .p10k.zsh
│   ├── .tmux.conf
│   └── .gitconfig
└── control-plane/
    ├── bin/
    │   ├── ccxctl
    │   └── dmenu-ccx
    └── README.md

~/Work/ssdd/ssdd__qtile_widgets/   # existing; extend
└── ssdd_qtile_widgets/
    └── ccx.py                     # new CcxStatusWidget
```

## 13. Success criteria

The v1 is done when:

1. `cd terraform/bootstrap && terraform apply` creates the state backend.
2. `cd terraform && terraform apply` creates the box and DNS record; output writes `~/.config/ccx/instance_id`.
3. From a fresh SSH session after first boot, `/var/log/ccx-provision-ok` exists.
4. `ssh ccx.dsylla.sesio.io` drops into a zsh session with powerlevel10k rendering, the current laptop plugins active, asdf providing Python/Node/Ruby, `docker ps` working for user `david`, `claude --version` responding.
5. The qtile `CcxStatusWidget` shows the correct state and uptime.
6. `ccxctl menu` (keyboard shortcut or widget click) drives the full lifecycle (start, SSH, resize, grow home, snapshot, refresh SG, stop) with `notify-send` feedback.
7. Stopping and starting the instance preserves `/home` contents and keeps DNS resolving to the same IP.
