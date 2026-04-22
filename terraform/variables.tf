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
