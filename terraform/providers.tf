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
