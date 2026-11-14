# SSM SecureString placeholders for the Alpaca paper trading credentials.
# Terraform creates the parameters so `terraform plan` and the Lambdas find a
# well-known path at runtime; the real secret value is written by the human
# once (console or `aws ssm put-parameter --overwrite`) and never lives in
# this repository. Standard parameters are free tier, encrypted with the
# AWS-managed aws/ssm key.
#
# AGENTS.md section 11.2: reference the parameter or create a placeholder
# with `lifecycle { ignore_changes = [value] }`; here we do the latter so
# re-applying terraform can never overwrite the real credential.

resource "aws_ssm_parameter" "alpaca_api_key" {
  name        = var.alpaca_api_key_parameter
  description = "Alpaca PAPER trading API key id (placeholder; the human sets the value, terraform ignores it)."
  type        = "SecureString"
  value       = "REPLACE_WITH_ALPACA_API_KEY_ID"

  lifecycle {
    ignore_changes = [value]
  }

  tags = {
    project = var.project
    env     = var.env
    owner   = "JECT"
  }
}

resource "aws_ssm_parameter" "alpaca_secret_key" {
  name        = var.alpaca_secret_key_parameter
  description = "Alpaca PAPER trading secret key (placeholder; the human sets the value, terraform ignores it)."
  type        = "SecureString"
  value       = "REPLACE_WITH_ALPACA_SECRET_KEY"

  lifecycle {
    ignore_changes = [value]
  }

  tags = {
    project = var.project
    env     = var.env
    owner   = "JECT"
  }
}
