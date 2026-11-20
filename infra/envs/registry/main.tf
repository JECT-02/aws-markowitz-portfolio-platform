# Registry root (infra/envs/registry): creates the seven ECR repositories in
# their own state (AGENTS.md section 11.1: one state key per environment).
# It is applied BEFORE infra/envs/dev so the images can be published into
# existing repositories (ADR 0004): apply this, push the images, then plan and
# apply dev with TF_VAR_image_digests.

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      project     = "portfolio-markowitz-aws"
      env         = var.env
      owner       = "JECT"
      cost-center = "personal-portfolio"
    }
  }
}

module "registry" {
  source = "../../modules/registry"

  project = "portfolio-markowitz-aws"
  env     = var.env
}

variable "env" {
  type        = string
  description = "Deployment environment name."
  default     = "dev"
}

variable "aws_region" {
  type        = string
  description = "AWS region for the registry."
  default     = "us-east-1"
}

output "repository_urls" {
  description = "ECR repository URL per image (publish targets for make build-*)."
  value       = module.registry.repository_urls
}
