# ccx — Terraform State Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the S3 state bucket (`sesio-terraform-state`) and DynamoDB lock table (`sesio-terraform-state-lock`) used by the main ccx Terraform project and all future sesio Terraform projects.

**Architecture:** Small standalone Terraform project with **local state** (chicken-and-egg — you can't use an S3 backend before its bucket exists). Creates the two resources once, then its own state file is committed so the project is reproducible.

**Tech Stack:** Terraform ≥ 1.7, AWS provider ≥ 5.0, profile `sesio__euwest1`, region `eu-west-1`.

---

## File Structure

```
sesio__ccx/
└── terraform/
    └── bootstrap/
        ├── versions.tf
        ├── variables.tf
        ├── main.tf
        ├── outputs.tf
        └── README.md
```

---

### Task 1: Scaffold versions.tf + variables.tf

**Files:**
- Create: `terraform/bootstrap/versions.tf`
- Create: `terraform/bootstrap/variables.tf`

- [ ] **Step 1: Create directory**

Run: `mkdir -p /home/david/Work/sesio/sesio__ccx/terraform/bootstrap`

- [ ] **Step 2: Write versions.tf**

File `/home/david/Work/sesio/sesio__ccx/terraform/bootstrap/versions.tf`:

```hcl
terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  profile = var.aws_profile
  region  = var.aws_region
  default_tags {
    tags = {
      Project   = "ccx"
      ManagedBy = "terraform"
      Bootstrap = "true"
    }
  }
}
```

- [ ] **Step 3: Write variables.tf**

File `/home/david/Work/sesio/sesio__ccx/terraform/bootstrap/variables.tf`:

```hcl
variable "aws_profile" {
  type    = string
  default = "sesio__euwest1"
}

variable "aws_region" {
  type    = string
  default = "eu-west-1"
}

variable "state_bucket_name" {
  type    = string
  default = "sesio-terraform-state"
}

variable "lock_table_name" {
  type    = string
  default = "sesio-terraform-state-lock"
}
```

---

### Task 2: S3 state bucket

**Files:**
- Create: `terraform/bootstrap/main.tf`

- [ ] **Step 1: Write bucket resources**

File `/home/david/Work/sesio/sesio__ccx/terraform/bootstrap/main.tf`:

```hcl
resource "aws_s3_bucket" "state" {
  bucket = var.state_bucket_name
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
```

- [ ] **Step 2: init + validate**

Run: `cd /home/david/Work/sesio/sesio__ccx/terraform/bootstrap && AWS_PROFILE=sesio__euwest1 terraform init && terraform validate`
Expected: `Terraform has been successfully initialized!` and `Success! The configuration is valid.`

---

### Task 3: DynamoDB lock table

**Files:**
- Modify: `terraform/bootstrap/main.tf`

- [ ] **Step 1: Append lock table**

Append to `/home/david/Work/sesio/sesio__ccx/terraform/bootstrap/main.tf`:

```hcl
resource "aws_dynamodb_table" "state_lock" {
  name         = var.lock_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }
}
```

- [ ] **Step 2: Validate**

Run: `cd /home/david/Work/sesio/sesio__ccx/terraform/bootstrap && terraform validate`
Expected: success.

- [ ] **Step 3: Plan**

Run: `cd /home/david/Work/sesio/sesio__ccx/terraform/bootstrap && AWS_PROFILE=sesio__euwest1 terraform plan -out=bootstrap.tfplan`
Expected: `Plan: 5 to add, 0 to change, 0 to destroy.`
Resources: `aws_s3_bucket.state`, `aws_s3_bucket_versioning.state`, `aws_s3_bucket_server_side_encryption_configuration.state`, `aws_s3_bucket_public_access_block.state`, `aws_dynamodb_table.state_lock`.

---

### Task 4: Outputs + README

**Files:**
- Create: `terraform/bootstrap/outputs.tf`
- Create: `terraform/bootstrap/README.md`

- [ ] **Step 1: outputs.tf**

File `/home/david/Work/sesio/sesio__ccx/terraform/bootstrap/outputs.tf`:

```hcl
output "state_bucket" {
  description = "Name of the S3 bucket for Terraform state."
  value       = aws_s3_bucket.state.bucket
}

output "lock_table" {
  description = "Name of the DynamoDB table for state locking."
  value       = aws_dynamodb_table.state_lock.name
}

output "region" {
  value = var.aws_region
}
```

- [ ] **Step 2: README**

File `/home/david/Work/sesio/sesio__ccx/terraform/bootstrap/README.md`:

````markdown
# ccx terraform bootstrap

Run this once, ever (per AWS account), to create the shared state backend
used by every other sesio Terraform project.

## Usage

```bash
cd terraform/bootstrap
AWS_PROFILE=sesio__euwest1 terraform init
AWS_PROFILE=sesio__euwest1 terraform plan -out=bootstrap.tfplan
AWS_PROFILE=sesio__euwest1 terraform apply bootstrap.tfplan
```

## State

This project's own state lives locally at `terraform.tfstate` and is
committed to the repo. It's tiny (two resources) and the chicken-and-egg
rules it out of using S3 itself.

## What it creates

- S3 bucket `sesio-terraform-state`: versioned, SSE-S3, public access blocked
- DynamoDB table `sesio-terraform-state-lock`: `LockID` hash, pay-per-request
````

---

### Task 5: Apply (user approval gate)

- [ ] **Step 1: Surface plan and pause for approval**

This is a cost-incurring, account-shared action. Show the plan summary to the user and ask explicit approval:

> "Ready to `terraform apply` the bootstrap? This creates `sesio-terraform-state` (S3) and `sesio-terraform-state-lock` (DynamoDB) in account 709389331805, region eu-west-1. Both are essentially free but permanent."

Do not proceed without explicit "yes".

- [ ] **Step 2: Apply**

Run: `cd /home/david/Work/sesio/sesio__ccx/terraform/bootstrap && AWS_PROFILE=sesio__euwest1 terraform apply bootstrap.tfplan`
Expected: `Apply complete! Resources: 5 added, 0 changed, 0 destroyed.`

- [ ] **Step 3: Smoke — bucket**

Run: `AWS_PROFILE=sesio__euwest1 aws s3api head-bucket --bucket sesio-terraform-state`
Expected: no output, exit 0.

- [ ] **Step 4: Smoke — table**

Run: `AWS_PROFILE=sesio__euwest1 aws dynamodb describe-table --table-name sesio-terraform-state-lock --query 'Table.TableStatus' --output text`
Expected: `ACTIVE`.

---

### Task 6: .gitignore + Commit

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Append terraform patterns to `.gitignore`**

Edit `/home/david/Work/sesio/sesio__ccx/.gitignore` to ensure it contains:

```
# Terraform
.terraform/
*.tfplan
*.tfstate.backup
```

`terraform.tfstate` is intentionally **not** ignored — the bootstrap state is committed.

- [ ] **Step 2: Review**

Run: `cd /home/david/Work/sesio/sesio__ccx && git status && git diff --cached --stat`
Expected: `terraform/bootstrap/*.tf`, `terraform/bootstrap/README.md`, `terraform/bootstrap/terraform.tfstate`, updated `.gitignore`. No `.terraform/`, no `*.tfplan`.

- [ ] **Step 3: Commit**

Invoke `/commit`. Suggested message: `feat(terraform): add bootstrap state backend (S3 + DynamoDB)`.

---

## Done when

1. `aws s3api head-bucket --bucket sesio-terraform-state` succeeds.
2. `aws dynamodb describe-table --table-name sesio-terraform-state-lock` reports `ACTIVE`.
3. `terraform/bootstrap/terraform.tfstate` is committed.
4. `.terraform/` and `*.tfplan` are gitignored.
