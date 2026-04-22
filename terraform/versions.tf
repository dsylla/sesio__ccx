terraform {
  required_version = ">= 1.11"
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
    bucket       = "sesio-terraform-state"
    key          = "ccx/terraform.tfstate"
    region       = "eu-west-1"
    encrypt      = true
    profile      = "sesio__euwest1"
    use_lockfile = true
  }
}
