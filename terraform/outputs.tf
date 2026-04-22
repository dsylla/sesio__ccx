output "instance_id" {
  value = aws_instance.ccx.id
}

output "public_ip" {
  value = aws_instance.ccx.public_ip
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
