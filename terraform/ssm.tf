resource "aws_ssm_parameter" "claude_config_deploy_key" {
  name        = "/${var.name}/claude_config_deploy_key"
  description = "Read-only deploy key for dsylla/claude-config. Seeded manually post-apply."
  type        = "SecureString"
  value       = "PLACEHOLDER__seed_via_aws_ssm_put-parameter__"

  lifecycle {
    ignore_changes = [value]
  }
}
