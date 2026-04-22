resource "aws_route53_record" "ccx" {
  zone_id = data.aws_route53_zone.root.zone_id
  name    = var.hostname
  type    = "A"
  ttl     = 60
  records = [aws_instance.ccx.public_ip]

  # Dynamic IP (no EIP). `ccxctl start` updates this record on each start.
  lifecycle {
    ignore_changes = [records]
  }
}
