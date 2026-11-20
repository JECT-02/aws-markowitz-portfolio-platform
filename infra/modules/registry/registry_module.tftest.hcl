mock_provider "aws" {}

variables {
  project = "portfolio-markowitz-aws"
  env     = "dev"
}

run "one_immutable_scanned_repository_per_image" {
  command = plan

  assert {
    condition     = length(aws_ecr_repository.images) == 7
    error_message = "Exactly one repository per image (ingest, clean, optimizer, monitor, backtest, reader, executor)."
  }

  assert {
    condition = alltrue([
      for name, repo in aws_ecr_repository.images : repo.image_tag_mutability == "IMMUTABLE"
    ])
    error_message = "Every repository must pin immutable tags (AGENTS.md section 11.2)."
  }

  assert {
    condition = alltrue([
      for name, repo in aws_ecr_repository.images : repo.image_scanning_configuration[0].scan_on_push
    ])
    error_message = "Images must be scanned on push (AGENTS.md section 12)."
  }
}

run "every_repository_expires_untagged_images" {
  command = plan

  assert {
    condition = alltrue([
      for name, policy in aws_ecr_lifecycle_policy.images :
      jsondecode(policy.policy).rules[0].selection.countNumber == 14
    ])
    error_message = "Untagged images must expire after 14 days."
  }
}

run "repository_names_carry_the_env_suffix" {
  command = plan

  assert {
    condition     = aws_ecr_repository.images["reader"].name == "portfolio-markowitz-aws-reader-dev"
    error_message = "Repository names must include the project and the environment."
  }
}
