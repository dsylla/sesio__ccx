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
