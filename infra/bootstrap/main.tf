# One-time bootstrap, applied ONLY by the human (AGENTS.md section 11.1).
# Creates the remote state bucket and the OIDC roles for GitHub Actions CI.
# This directory has its own state expectations: apply it locally, once,
# before any environment is initialized.

terraform {
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # The bootstrap state lives locally (it creates the remote bucket itself);
  # keep this single file versioned after the first apply.
}

data "aws_caller_identity" "current" {}

locals {
  state_bucket_name = "portfolio-markowitz-tfstate-${data.aws_caller_identity.current.account_id}"
  tags = {
    project     = "portfolio-markowitz-aws"
    env         = "bootstrap"
    owner       = "JECT"
    cost-center = "personal-portfolio"
  }
}

# --- Remote state bucket ---------------------------------------------------

resource "aws_s3_bucket" "terraform_state" {
  bucket = local.state_bucket_name

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.terraform_state.arn,
          "${aws_s3_bucket.terraform_state.arn}/*",
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
    ]
  })
}

# --- GitHub Actions OIDC ----------------------------------------------------

# Both GitHub certificate thumbprints published by GitHub (docs.github.com,
# "updating the certificate thumbprints"); modern STS validates the root CA,
# the list is kept for API compatibility.
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b450d4d9f22e9b76",
  ]

  tags = local.tags
}

variable "github_owner" {
  type        = string
  description = "GitHub organization or user that owns the repository."
  default     = "JECT-02"
}

variable "github_repository" {
  type        = string
  description = "Repository name allowed to assume the CI roles."
  default     = "aws-markowitz-portfolio-platform"
}

variable "github_branch" {
  type        = string
  description = "Branch allowed to assume the plan role (pull requests)."
  default     = "main"
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_owner}/${var.github_repository}:ref:refs/heads/${var.github_branch}",
        "repo:${var.github_owner}/${var.github_repository}:pull_request",
      ]
    }
  }
}

# CI roles. Managed policies are used on purpose: a Terraform plan needs read
# access across every service the stack manages (documented exception to the
# "no wildcard actions" rule of AGENTS.md section 11.2; approved with this
# ADR-linked PR). Writes are NOT in the plan role.
resource "aws_iam_role" "terraform_plan" {
  name               = "portfolio-markowitz-ci-plan"
  assume_role_policy = data.aws_iam_policy_document.github_assume.json

  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "terraform_plan" {
  role       = aws_iam_role.terraform_plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# The apply role is created now but kept without permissions; the human grants
# the minimum required policy set after the first reviewed plan. Applying is
# always manual (AGENTS.md section 11.1), so an empty apply role cannot mutate
# anything by accident.
resource "aws_iam_role" "terraform_apply" {
  name               = "portfolio-markowitz-ci-apply"
  assume_role_policy = data.aws_iam_policy_document.github_assume.json

  tags = local.tags
}

# --- Outputs ----------------------------------------------------------------

output "state_bucket_name" {
  description = "Name of the remote state bucket for infra/envs/* backends."
  value       = aws_s3_bucket.terraform_state.id
}

output "state_bucket_region" {
  description = "Region where the state bucket lives (set with the apply region)."
  value       = var.aws_region
}

output "github_plan_role_arn" {
  description = "Role ARN that GitHub Actions assumes for terraform plan."
  value       = aws_iam_role.terraform_plan.arn
}

output "github_apply_role_arn" {
  description = "Role ARN that GitHub Actions assumes for manual terraform apply."
  value       = aws_iam_role.terraform_apply.arn
}

variable "aws_region" {
  type        = string
  description = "AWS region for the bootstrap resources."
  default     = "us-east-1"
}
