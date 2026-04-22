# ccx terraform

Main infrastructure for the ccx coding station. Uses the pre-existing shared
S3 state bucket `sesio-terraform-state` (same bucket as `sesio__network`),
key `ccx/terraform.tfstate`, with `use_lockfile = true` for native S3
locking (Terraform ≥ 1.11).

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
waiting for the real value. Seed it immediately after `apply` with:

```bash
ssh-keygen -t ed25519 -C "ccx claude-config deploy key" -f ~/.ssh/claude_config_deploy_key -N ''
gh repo deploy-key add ~/.ssh/claude_config_deploy_key.pub --repo dsylla/claude-config --title "ccx-instance (read-only)"
AWS_PROFILE=sesio__euwest1 aws ssm put-parameter \
  --name /ccx/claude_config_deploy_key --type SecureString --overwrite \
  --value "$(cat ~/.ssh/claude_config_deploy_key)"
shred -u ~/.ssh/claude_config_deploy_key ~/.ssh/claude_config_deploy_key.pub
```

## Smoke test

```bash
ssh david@ccx.dsylla.sesio.io 'cat /var/log/ccx-provision-ok'
```

If the marker file exists and prints versions (including rtk and the
claude-config commit SHA), cloud-init + ansible-pull succeeded.

## State

S3 backend at `s3://sesio-terraform-state/ccx/terraform.tfstate`. Locking
via S3 conditional writes (`use_lockfile = true`), no DynamoDB.
