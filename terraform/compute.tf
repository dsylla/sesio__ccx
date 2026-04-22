resource "aws_key_pair" "ccx" {
  key_name   = "${var.name}-admin"
  public_key = trimspace(file("${path.module}/../ansible/roles/user/files/authorized_keys"))
}

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
  # Nitro NVMe by-id format is `nvme-Amazon_Elastic_Block_Store_vol<16hex>`
  # (no hyphen after `vol`). Strip the hyphen, keep the `vol` prefix.
  home_volume_id_nohyphen = replace(aws_ebs_volume.home.id, "-", "")

  user_data = templatefile("${path.module}/user_data.tftpl", {
    home_volume_id_nohyphen = local.home_volume_id_nohyphen
    repo_url                = var.repo_url
  })
}

resource "aws_instance" "ccx" {
  ami                    = data.aws_ami.debian12_arm64.id
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnet.picked.id
  vpc_security_group_ids = [aws_security_group.instance.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  key_name               = aws_key_pair.ccx.key_name
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

# EIP dropped in v1: sesio account's EIP quota (5/region) is fully allocated by
# other projects. Route 53 points at the instance's dynamic public IP instead.
# Consequence: stop/start cycles give a new IP, so `ccxctl start` must update
# the Route 53 A record. Re-add the EIP once the quota is raised.
