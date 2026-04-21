# ccx — Terraform Main (Infrastructure) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provision the ccx EC2 instance, its EBS volumes, EIP, Route 53 record, IAM role, security group, and the SSM parameter that holds the claude-config deploy key — using the S3 backend created by `terraform/bootstrap/`. Cloud-init kicks off `ansible-pull` so the box self-configures on first boot. The user manually seeds the SSM parameter value once after apply (see Task 5 Step 3 recipe).

**Architecture:** Flat Terraform root at `terraform/` (no modules in v1 — start simple, extract if reuse emerges). Split into files by concern: `data.tf` for lookups, `iam.tf` for the instance role, `security.tf` for the SG, `compute.tf` for the instance + EBS + EIP, `dns.tf` for Route 53, `user_data.tftpl` for the cloud-init script. Outputs write `~/.config/ccx/instance_id` locally so `ccxctl` can find the box.

**Tech Stack:** Terraform ≥ 1.7, AWS provider ≥ 5.0, `hashicorp/local` ≥ 2.0, profile `sesio__euwest1`, region `eu-west-1`.

**Prereqs:**
- `ccx-terraform-bootstrap` plan applied (S3 + DynamoDB exist).
- `ccx-ansible` plan applied (playbook present in the repo so `ansible-pull` can find it).
- `ccx-dotfiles` plan applied (dotfiles present for the Ansible `dotfiles` role to consume).

---

## File Structure

```
sesio__ccx/
└── terraform/
    ├── versions.tf               # TF + provider pins, S3 backend config
    ├── providers.tf              # aws provider, default_tags
    ├── variables.tf
    ├── data.tf                   # AMI, default VPC/subnet, hosted zone
    ├── iam.tf                    # role, instance profile
    ├── security.tf               # SG + rules
    ├── ssm.tf                    # SecureString parameter for claude-config deploy key
    ├── compute.tf                # EC2 + root + EBS home + attachment + EIP
    ├── dns.tf                    # Route 53 A record
    ├── user_data.tftpl           # cloud-init script
    ├── outputs.tf                # outputs + local_file for instance_id
    ├── terraform.tfvars.example  # gitignored; .example is checked in
    └── README.md
```

---

### Task 1: versions, providers, variables

**Files:**
- Create: `terraform/versions.tf`
- Create: `terraform/providers.tf`
- Create: `terraform/variables.tf`
- Create: `terraform/terraform.tfvars.example`

- [ ] **Step 1: versions.tf (with S3 backend)**

File `/home/david/Work/sesio/sesio__ccx/terraform/versions.tf`:

```hcl
terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
    local = {
      source  = "hashicorp/local"
      version = ">= 2.0"
    }
  }
  backend "s3" {
    bucket         = "sesio-terraform-state"
    key            = "ccx/terraform.tfstate"
    region         = "eu-west-1"
    dynamodb_table = "sesio-terraform-state-lock"
    encrypt        = true
    profile        = "sesio__euwest1"
  }
}
```

- [ ] **Step 2: providers.tf**

File `/home/david/Work/sesio/sesio__ccx/terraform/providers.tf`:

```hcl
provider "aws" {
  profile = var.aws_profile
  region  = var.aws_region
  default_tags {
    tags = {
      Project   = "ccx"
      ManagedBy = "terraform"
      Owner     = "dsylla"
    }
  }
}
```

- [ ] **Step 3: variables.tf**

File `/home/david/Work/sesio/sesio__ccx/terraform/variables.tf`:

```hcl
variable "aws_profile" {
  type    = string
  default = "sesio__euwest1"
}

variable "aws_region" {
  type    = string
  default = "eu-west-1"
}

variable "name" {
  type    = string
  default = "ccx"
}

variable "hostname" {
  type    = string
  default = "ccx.dsylla.sesio.io"
}

variable "hosted_zone_name" {
  type    = string
  default = "sesio.io."
}

variable "instance_type" {
  type    = string
  default = "t4g.xlarge"
}

variable "root_volume_size_gb" {
  type    = number
  default = 30
}

variable "home_volume_size_gb" {
  type    = number
  default = 100
}

variable "admin_cidr" {
  type        = string
  description = "/32 CIDR allowed to SSH. Updated out-of-band by `ccxctl refresh-sg`."
}

variable "repo_url" {
  type    = string
  default = "https://github.com/dsylla/sesio__ccx.git"
}

variable "instance_id_local_path" {
  type        = string
  description = "Local file where the instance_id output is written."
  default     = "~/.config/ccx/instance_id"
}
```

- [ ] **Step 4: terraform.tfvars.example**

File `/home/david/Work/sesio/sesio__ccx/terraform/terraform.tfvars.example`:

```hcl
# Copy to terraform.tfvars and edit.
# terraform.tfvars is gitignored.

admin_cidr = "1.2.3.4/32"   # your current public /32
```

- [ ] **Step 5: Ensure tfvars are gitignored**

Edit `/home/david/Work/sesio/sesio__ccx/.gitignore` to contain:

```
terraform.tfvars
*.auto.tfvars
```

- [ ] **Step 6: terraform init**

Run: `cd /home/david/Work/sesio/sesio__ccx/terraform && AWS_PROFILE=sesio__euwest1 terraform init`
Expected: `Terraform has been successfully initialized!` Backend: `s3`.

---

### Task 2: Data sources

**Files:**
- Create: `terraform/data.tf`

- [ ] **Step 1: Write data sources**

File `/home/david/Work/sesio/sesio__ccx/terraform/data.tf`:

```hcl
# Latest official Debian 12 arm64 AMI
data "aws_ami" "debian12_arm64" {
  most_recent = true
  owners      = ["136693071363"] # Debian
  filter {
    name   = "name"
    values = ["debian-12-arm64-*"]
  }
  filter {
    name   = "architecture"
    values = ["arm64"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# Default VPC + default-public subnets
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default_public" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

# Pick the first default-public subnet (v1 — volume + instance share its AZ)
data "aws_subnet" "picked" {
  id = sort(tolist(data.aws_subnets.default_public.ids))[0]
}

# Hosted zone for DNS
data "aws_route53_zone" "root" {
  name         = var.hosted_zone_name
  private_zone = false
}

data "aws_caller_identity" "current" {}
```

- [ ] **Step 2: Validate**

Run: `cd /home/david/Work/sesio/sesio__ccx/terraform && terraform validate`
Expected: `Success! The configuration is valid.`

---

### Task 3: IAM role + instance profile

**Files:**
- Create: `terraform/iam.tf`

- [ ] **Step 1: Write IAM resources**

File `/home/david/Work/sesio/sesio__ccx/terraform/iam.tf`:

```hcl
data "aws_iam_policy_document" "ec2_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "${var.name}-instance-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_trust.json
}

resource "aws_iam_role_policy_attachment" "admin" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "instance" {
  name = "${var.name}-instance-profile"
  role = aws_iam_role.instance.name
}
```

**Deferred:** the spec mentions an `aws:SourceArn` condition on the trust policy pinning to the instance ARN. That creates a chicken-and-egg with instance creation (the role has to exist before the instance, but the condition needs the instance ARN). Deferred to a v1.1 hardening pass. Only this instance has the profile attached, so the risk delta is small.

- [ ] **Step 2: Validate**

Run: `cd /home/david/Work/sesio/sesio__ccx/terraform && terraform validate`
Expected: success.

---

### Task 4: Security group

**Files:**
- Create: `terraform/security.tf`

- [ ] **Step 1: Write SG**

File `/home/david/Work/sesio/sesio__ccx/terraform/security.tf`:

```hcl
resource "aws_security_group" "instance" {
  name        = "${var.name}-sg"
  description = "ccx: SSH from admin_cidr only, egress all."
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  security_group_id = aws_security_group.instance.id
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  cidr_ipv4         = var.admin_cidr
  description       = "SSH from admin /32"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.instance.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "All egress"
}
```

**Note:** `ccxctl refresh-sg` will revoke/authorize rules on this SG out-of-band as the laptop's public IP changes. Terraform does not `ignore_changes` these rules — if you re-apply after a laptop IP change, Terraform will revert the SG to match `admin_cidr` in tfvars. Update tfvars before re-applying, or accept the SG flap and run `ccxctl refresh-sg` again afterward.

- [ ] **Step 2: Validate**

Run: `cd /home/david/Work/sesio/sesio__ccx/terraform && terraform validate`
Expected: success.

---

### Task 5: SSM parameter — claude-config deploy key

The Ansible `dotfiles` role reads a read-only GitHub deploy key from SSM Parameter Store and uses it to clone `dsylla/claude-config` (private). Terraform creates the parameter; the user seeds the actual value once via `aws ssm put-parameter`. `ignore_changes = [value]` keeps Terraform from reverting the seeded value on re-apply.

**Files:**
- Create: `terraform/ssm.tf`

- [ ] **Step 1: Write the resource**

File `/home/david/Work/sesio/sesio__ccx/terraform/ssm.tf`:

```hcl
resource "aws_ssm_parameter" "claude_config_deploy_key" {
  name        = "/${var.name}/claude_config_deploy_key"
  description = "Read-only deploy key for dsylla/claude-config. Seeded manually."
  type        = "SecureString"
  value       = "PLACEHOLDER__seed_via_aws_ssm_put-parameter__"

  lifecycle {
    ignore_changes = [value]
  }
}
```

- [ ] **Step 2: Validate**

Run: `cd /home/david/Work/sesio/sesio__ccx/terraform && terraform validate`
Expected: success.

- [ ] **Step 3: Post-apply recipe (documented; executed in Task 12 Step 6)**

After `terraform apply`, seed the real key value:

```bash
# 1. Generate a fresh ed25519 keypair on the laptop (no passphrase, no overwrite)
ssh-keygen -t ed25519 -C "ccx claude-config deploy key" -f ~/.ssh/claude_config_deploy_key -N ''

# 2. Register the public half as a read-only deploy key on the claude-config repo
gh repo deploy-key add ~/.ssh/claude_config_deploy_key.pub \
  --repo dsylla/claude-config \
  --title "ccx-instance (read-only)"

# 3. Put the private half into SSM (overwrite the placeholder)
AWS_PROFILE=sesio__euwest1 aws ssm put-parameter \
  --name /ccx/claude_config_deploy_key \
  --type SecureString \
  --overwrite \
  --value "$(cat ~/.ssh/claude_config_deploy_key)"

# 4. (Optional) Wipe the laptop copy; the only copy now lives in SSM + the deploy key slot
shred -u ~/.ssh/claude_config_deploy_key ~/.ssh/claude_config_deploy_key.pub
```

---

### Task 6: User-data template

**Files:**
- Create: `terraform/user_data.tftpl`

- [ ] **Step 1: Write template**

File `/home/david/Work/sesio/sesio__ccx/terraform/user_data.tftpl`:

```
#!/bin/bash
set -euxo pipefail
exec > >(tee -a /var/log/ccx-user-data.log) 2>&1

HOME_DEV_ID="nvme-Amazon_Elastic_Block_Store_vol-${home_volume_id_stripped}"
HOME_DEV_PATH="/dev/disk/by-id/$${HOME_DEV_ID}"

# Wait up to 120s for the home volume to appear as a by-id node.
for i in $(seq 1 60); do
  if [ -e "$${HOME_DEV_PATH}" ]; then break; fi
  sleep 2
done
test -e "$${HOME_DEV_PATH}" || { echo "home volume never appeared"; exit 1; }

# Format if blank (reattached volumes keep their filesystem).
if ! blkid "$${HOME_DEV_PATH}" >/dev/null 2>&1; then
  mkfs.ext4 -L ccx-home "$${HOME_DEV_PATH}"
fi

mkdir -p /home
grep -q "$${HOME_DEV_ID}" /etc/fstab || \
  echo "$${HOME_DEV_PATH} /home ext4 defaults,nofail,x-systemd.device-timeout=30 0 2" >> /etc/fstab
mountpoint -q /home || mount /home

# Base packages for clone + ansible-pull.
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y git ansible python3 python3-pip

# Clone the (public) repo into a root-owned cache and run the playbook.
REPO_CACHE=/opt/sesio__ccx
if [ ! -d "$${REPO_CACHE}/.git" ]; then
  git clone "${repo_url}" "$${REPO_CACHE}"
else
  git -C "$${REPO_CACHE}" pull --ff-only
fi

cd "$${REPO_CACHE}/ansible"
ansible-playbook -i inventory site.yml 2>&1 | tee /var/log/ansible-pull.log
```

**Template syntax notes:**
- `$${...}` → literal `${...}` in the rendered bash script (so shell vars survive).
- `${home_volume_id_stripped}` / `${repo_url}` → Terraform-side interpolation.

- [ ] **Step 2: Validate (template is not parsed until `templatefile()` runs)**

Run: `cd /home/david/Work/sesio/sesio__ccx/terraform && terraform validate`
Expected: success.

---

### Task 7: EBS home + EC2 + EIP

**Files:**
- Create: `terraform/compute.tf`

- [ ] **Step 1: Write compute resources**

File `/home/david/Work/sesio/sesio__ccx/terraform/compute.tf`:

```hcl
resource "aws_ebs_volume" "home" {
  availability_zone = data.aws_subnet.picked.availability_zone
  size              = var.home_volume_size_gb
  type              = "gp3"
  encrypted         = true

  tags = {
    Name = "${var.name}-home"
  }

  lifecycle {
    prevent_destroy = true
  }
}

locals {
  home_volume_id_stripped = replace(aws_ebs_volume.home.id, "vol-", "")

  user_data = templatefile("${path.module}/user_data.tftpl", {
    home_volume_id_stripped = local.home_volume_id_stripped
    repo_url                = var.repo_url
  })
}

resource "aws_instance" "ccx" {
  ami                    = data.aws_ami.debian12_arm64.id
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnet.picked.id
  vpc_security_group_ids = [aws_security_group.instance.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  user_data              = local.user_data

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "enabled"
  }

  root_block_device {
    volume_size           = var.root_volume_size_gb
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  tags = {
    Name = var.name
  }

  lifecycle {
    # Don't recreate the instance just because a newer AMI shipped or
    # user-data shifted a byte.
    ignore_changes = [ami, user_data]
  }
}

resource "aws_volume_attachment" "home" {
  device_name                    = "/dev/sdh"
  volume_id                      = aws_ebs_volume.home.id
  instance_id                    = aws_instance.ccx.id
  force_detach                   = false
  stop_instance_before_detaching = true
}

resource "aws_eip" "ccx" {
  instance = aws_instance.ccx.id
  domain   = "vpc"

  tags = {
    Name = "${var.name}-eip"
  }
}
```

- [ ] **Step 2: Validate**

Run: `cd /home/david/Work/sesio/sesio__ccx/terraform && terraform validate`
Expected: success.

---

### Task 8: Route 53

**Files:**
- Create: `terraform/dns.tf`

- [ ] **Step 1: Write DNS**

File `/home/david/Work/sesio/sesio__ccx/terraform/dns.tf`:

```hcl
resource "aws_route53_record" "ccx" {
  zone_id = data.aws_route53_zone.root.zone_id
  name    = var.hostname
  type    = "A"
  ttl     = 60
  records = [aws_eip.ccx.public_ip]
}
```

- [ ] **Step 2: Validate**

Run: `cd /home/david/Work/sesio/sesio__ccx/terraform && terraform validate`
Expected: success.

---

### Task 9: Outputs + instance_id file write

**Files:**
- Create: `terraform/outputs.tf`

- [ ] **Step 1: outputs.tf**

File `/home/david/Work/sesio/sesio__ccx/terraform/outputs.tf`:

```hcl
output "instance_id" {
  value = aws_instance.ccx.id
}

output "public_ip" {
  value = aws_eip.ccx.public_ip
}

output "hostname" {
  value = var.hostname
}

output "home_volume_id" {
  value = aws_ebs_volume.home.id
}

output "root_device_name" {
  value = aws_instance.ccx.root_block_device[0].device_name
}

resource "local_file" "ccx_instance_id" {
  content         = aws_instance.ccx.id
  filename        = pathexpand(var.instance_id_local_path)
  file_permission = "0644"
}
```

- [ ] **Step 2: terraform init -upgrade to fetch local provider**

Run: `cd /home/david/Work/sesio/sesio__ccx/terraform && AWS_PROFILE=sesio__euwest1 terraform init -upgrade`
Expected: `hashicorp/local` installed.

- [ ] **Step 3: Validate**

Run: `cd /home/david/Work/sesio/sesio__ccx/terraform && terraform validate`
Expected: success.

---

### Task 10: README

**Files:**
- Create: `terraform/README.md`

- [ ] **Step 1: Write README**

File `/home/david/Work/sesio/sesio__ccx/terraform/README.md`:

````markdown
# ccx terraform

Main infrastructure for the ccx coding station. Depends on
`terraform/bootstrap/` being applied first.

## Usage

```bash
cp terraform.tfvars.example terraform.tfvars
# edit: set admin_cidr to your current public /32

cd terraform
AWS_PROFILE=sesio__euwest1 terraform init
AWS_PROFILE=sesio__euwest1 terraform plan -out=ccx.tfplan
AWS_PROFILE=sesio__euwest1 terraform apply ccx.tfplan
```

On success, writes the instance ID to `~/.config/ccx/instance_id`.

## Seed the claude-config deploy key (one-time, post-apply)

The `aws_ssm_parameter.claude_config_deploy_key` resource is created with a
placeholder value. The ansible `dotfiles` role polls SSM for up to 20 min
waiting for the real value. Seed it immediately after `apply` with the
4-step recipe in `docs/superpowers/plans/2026-04-21-ccx-terraform-main.md`
(Task 5 Step 3): generate ed25519 keypair, add the public half as a
read-only deploy key on `dsylla/claude-config`, put the private half into
SSM with `--overwrite`, wipe the laptop copy.

## Smoke test

```bash
ssh david@ccx.dsylla.sesio.io 'cat /var/log/ccx-provision-ok'
```

If the marker file exists and prints versions (including rtk and the
claude-config commit SHA), cloud-init + ansible-pull succeeded.

## State

S3 backend at `s3://sesio-terraform-state/ccx/terraform.tfstate`, locked
via DynamoDB table `sesio-terraform-state-lock`.
````

---

### Task 11: fmt + plan

- [ ] **Step 1: fmt + validate**

Run: `cd /home/david/Work/sesio/sesio__ccx/terraform && terraform fmt -recursive && terraform validate`
Expected: no changes reported by `fmt`, `validate` passes.

- [ ] **Step 2: Create tfvars**

```bash
cd /home/david/Work/sesio/sesio__ccx/terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars: set admin_cidr to your current public /32.
```

Get current public /32:
Run: `curl -fsSL https://checkip.amazonaws.com`
Expected: your public IPv4 — append `/32` and set `admin_cidr`.

- [ ] **Step 3: Plan**

Run: `cd /home/david/Work/sesio/sesio__ccx/terraform && AWS_PROFILE=sesio__euwest1 terraform plan -out=ccx.tfplan`
Expected: `Plan: 13 to add, 0 to change, 0 to destroy.` (rough count — IAM role, 2× role attachments, instance profile, SG, 2× SG rules, SSM SecureString, EBS home volume, EC2 instance, volume attachment, EIP, Route 53 A record, local file).

---

### Task 12: Apply (user approval gate)

- [ ] **Step 1: Stop and surface the plan**

Before applying, show the user the resource count and cost estimate:

> "Ready to `terraform apply`? This is the first real spend on ccx.  ~€19/month if stopped most of the time per the spec §10. 12 resources added, nothing destroyed."

Do not proceed without explicit "yes".

- [ ] **Step 2: Apply**

Run: `cd /home/david/Work/sesio/sesio__ccx/terraform && AWS_PROFILE=sesio__euwest1 terraform apply ccx.tfplan`
Expected: `Apply complete! Resources: 12 added.` Outputs printed. `~/.config/ccx/instance_id` exists and contains the new instance ID.

- [ ] **Step 3: Verify instance_id file**

Run: `cat ~/.config/ccx/instance_id`
Expected: a string starting with `i-…`.

- [ ] **Step 4: Smoke — DNS**

Run: `dig +short ccx.dsylla.sesio.io`
Expected: the EIP (may take 30–60s for a fresh record to propagate).

- [ ] **Step 5: Seed the claude-config deploy key into SSM (time-sensitive)**

Immediately after `apply` returns, run the 4-step recipe from Task 5 Step 3 (generate keypair, register deploy key on GitHub, `aws ssm put-parameter --overwrite`, wipe the laptop copy). The ansible `dotfiles` role polls SSM for up to 20 minutes waiting for the real value; if the timer runs out, ansible will fail and provision-ok won't be written.

- [ ] **Step 6: Smoke — SSH + provision marker (poll up to 15 min)**

Run: `for i in $(seq 1 45); do ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 david@ccx.dsylla.sesio.io 'test -f /var/log/ccx-provision-ok && echo OK' 2>/dev/null && break; sleep 20; done`
Expected: `OK` within 15 minutes (cloud-init + `ansible-pull` end-to-end is ~6–10 minutes after SSM is seeded).

- [ ] **Step 7: Smoke — versions**

Run: `ssh david@ccx.dsylla.sesio.io 'zsh --version; docker --version; asdf --version; python --version; node --version; ruby --version; claude --version; aws --version; rtk --version'`
Expected: version output for each line, non-zero exit for none.

- [ ] **Step 8: Smoke — dotfiles + claude-config symlinked correctly**

Run:
```bash
ssh david@ccx.dsylla.sesio.io '
  echo "--- top-level dotfiles ---"
  ls -la ~/.zshrc ~/.p10k.zsh ~/.tmux.conf ~/.gitconfig
  echo "--- ~/.claude/ ---"
  ls -la ~/.claude/
  echo "--- claude-config clone ---"
  git -C ~/claude-config rev-parse --short HEAD
'
```
Expected:
- `.zshrc`, `.p10k.zsh`, `.tmux.conf`, `.gitconfig` → symlinks into `~/sesio__ccx/dotfiles/`
- `~/.claude/settings.json`, `RTK.md`, `commands`, `hooks` → symlinks into `~/sesio__ccx/dotfiles/.claude/`
- `~/.claude/CLAUDE.md` → symlink into `~/claude-config/CLAUDE.md`
- `~/.claude/skills`   → symlink into `~/claude-config/skills`
- `git -C ~/claude-config rev-parse` prints a short commit SHA

---

### Task 13: Commit

- [ ] **Step 1: Secret leak check**

Run: `grep -RE 'BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY' /home/david/Work/sesio/sesio__ccx/terraform/`
Expected: no output.

Run: `git status --porcelain /home/david/Work/sesio/sesio__ccx/terraform/terraform.tfvars`
Expected: empty (file is gitignored — it should NOT appear as staged or modified in a way that can reach git).

- [ ] **Step 2: Review**

Run: `cd /home/david/Work/sesio/sesio__ccx && git status && git diff --cached --stat`
Expected: files under `terraform/` (except `terraform.tfvars` and `.terraform/`) + `.gitignore` update.

- [ ] **Step 3: Commit**

Invoke `/commit`. Suggested message: `feat(terraform): main infrastructure (EC2, EBS, EIP, DNS, IAM, SG)`.

---

## Done when

1. `terraform apply` succeeds from a clean state.
2. `dig +short ccx.dsylla.sesio.io` returns the EIP.
3. `ssh david@ccx.dsylla.sesio.io 'cat /var/log/ccx-provision-ok'` prints the marker.
4. `~/.config/ccx/instance_id` contains the new instance ID.
5. `terraform.tfvars` is NOT in git.
6. Stopping and starting the instance preserves `/home` contents (manual: `ssh … touch ~/smoke`, `terraform taint aws_instance.ccx && apply`, confirm `~/smoke` survives — defer this to a later session if not confident).
