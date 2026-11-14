# dev environment root. Composes the Terraform modules with dev values.
# One remote state per environment (AGENTS.md section 11.1): the bucket is
# created by infra/bootstrap; the human fills the name below after applying
# bootstrap once.

terraform {
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

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
