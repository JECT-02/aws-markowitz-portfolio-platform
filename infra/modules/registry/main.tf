# ECR registry (module `registry`): the seven container-image repositories.
#
# The repositories must exist before the images are pushed, and the images
# must be published before the pipeline/backtest/api Lambdas are created
# (ADR 0004). Keeping them in their own module composed by infra/envs/registry
# breaks that cycle with a first, cheap apply that never needs -target or a
# deliberately failed plan.

terraform {
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

locals {
  tags = {
    project     = var.project
    env         = var.env
    owner       = "JECT"
    cost-center = "personal-portfolio"
  }
}

variable "project" {
  type        = string
  description = "Project identifier used for repository names and tags."
}

variable "env" {
  type        = string
  description = "Deployment environment (dev or prod)."
}

variable "image_names" {
  type        = list(string)
  description = "One repository per Lambda image, in build order."
  default = [
    "ingest",
    "clean",
    "optimizer",
    "monitor",
    "backtest",
    "reader",
    "executor",
  ]

  validation {
    condition     = length(var.image_names) == length(distinct(var.image_names)) && length(var.image_names) == 7
    error_message = "Exactly one repository per image is required: ingest, clean, optimizer, monitor, backtest, reader, executor."
  }
}

resource "aws_ecr_repository" "images" {
  for_each = toset(var.image_names)

  name                 = "${var.project}-${each.key}-${var.env}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.tags
}

# Untagged images expire after 14 days: digests are pinned per deployment and
# the untagged leftovers would otherwise accumulate storage costs.
resource "aws_ecr_lifecycle_policy" "images" {
  for_each = aws_ecr_repository.images

  repository = each.value.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 14 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 14
        }
        action = {
          type = "expire"
        }
      },
    ]
  })
}

output "repository_urls" {
  description = "ECR repository URL per image."
  value       = { for name, repo in aws_ecr_repository.images : name => repo.repository_url }
}

output "repository_arns" {
  description = "ECR repository ARN per image."
  value       = { for name, repo in aws_ecr_repository.images : name => repo.arn }
}
