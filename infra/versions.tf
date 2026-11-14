# Canonical IaC pins for the whole repository (AGENTS.md sections 2 and 11).
#
# Terraform requires the `terraform { required_* }` block inside every root
# working directory, so each root under infra/envs/* mirrors this block
# exactly. Change the pins here first, then in the environment roots, and
# regenerate `.terraform.lock.hcl` with `terraform init` (never with
# -upgrade without human confirmation, AGENTS.md section 11.1).

terraform {
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
