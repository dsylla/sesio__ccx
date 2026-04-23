# Latest official Debian 13 (trixie) arm64 AMI.
# Moved from Debian 12 after hitting GLIBC 2.36 friction (rtk arm64-gnu needs
# 2.39) and Debian 12's default no-rsyslog/no-auth.log fail2ban trap.
data "aws_ami" "debian13_arm64" {
  most_recent = true
  owners      = ["136693071363"] # Debian
  filter {
    name   = "name"
    values = ["debian-13-arm64-*"]
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
