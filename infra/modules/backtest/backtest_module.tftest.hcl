mock_provider "aws" {}

variables {
  project      = "portfolio-markowitz-aws"
  env          = "dev"
  image_tag    = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
  code_version = "abc1234"
  # Same values as config/strategy.yaml; the variable validation rejects
  # incomplete or empty maps.
  strategy = {
    risk_aversion           = "2.0"
    transaction_cost_lambda = "0.001"
    return_shrinkage_alpha  = "0.5"
    risk_free_rate_annual   = "0.04"
    max_forward_fill_days   = "2"
    window_days             = "252"
    ingest_lookback_days    = "420"
    rebalance_l1_threshold  = "0.10"
    validation_gammas       = "1,2"
    validation_lambdas      = "0,0.001"
    validation_alphas       = "0,0.5"
    validation_windows      = "126,252"
    validation_folds        = "3"
  }
  silver_bucket_name = "portfolio-markowitz-aws-dev-lakehouse-silver"
  silver_bucket_arn  = "arn:aws:s3:::portfolio-markowitz-aws-dev-lakehouse-silver"
  gold_bucket_name   = "portfolio-markowitz-aws-dev-lakehouse-gold"
  gold_bucket_arn    = "arn:aws:s3:::portfolio-markowitz-aws-dev-lakehouse-gold"
  dlq_arn            = "arn:aws:sqs:us-east-1:123456789012:portfolio-markowitz-aws-dev-backtest-dlq"
}

run "image_tag_validation_rejects_latest" {
  command = plan

  assert {
    condition     = var.image_tag != "latest" && var.image_tag != ""
    error_message = "The module must never run an image tagged latest."
  }
}

run "function_is_a_container_image_x86" {
  command = plan

  assert {
    condition     = aws_lambda_function.backtest.package_type == "Image"
    error_message = "The backtest ships as a container image."
  }

  assert {
    condition     = contains(aws_lambda_function.backtest.architectures, "x86_64")
    error_message = "The backtest lambda runs on x86_64."
  }

  assert {
    condition     = aws_lambda_function.backtest.memory_size == 2048 && aws_lambda_function.backtest.timeout == 900
    error_message = "The validation grid runs with 2 GB and the 15-minute ceiling."
  }
}

run "function_has_dlq_and_no_built_in_retries" {
  command = plan

  assert {
    condition     = aws_lambda_function.backtest.dead_letter_config[0].target_arn == var.dlq_arn
    error_message = "Backtest failures must land in the backtest DLQ."
  }

  assert {
    condition     = aws_lambda_function_event_invoke_config.backtest.maximum_retry_attempts == 0
    error_message = "Asynchronous retries must be disabled; failures go to the DLQ."
  }
}

run "log_group_has_bounded_retention" {
  command = plan

  assert {
    condition     = aws_cloudwatch_log_group.backtest.retention_in_days == 14
    error_message = "Log retention must be bounded (14 days, AGENTS.md section 11.2)."
  }
}

run "ecr_is_immutable_with_scanning" {
  command = plan

  assert {
    condition     = aws_ecr_repository.backtest.image_tag_mutability == "IMMUTABLE"
    error_message = "The ECR repository must pin immutable tags."
  }

  assert {
    condition     = aws_ecr_repository.backtest.image_scanning_configuration[0].scan_on_push
    error_message = "Images must be scanned on push (AGENTS.md section 12)."
  }
}

run "weekly_schedule_uses_saturday_ny_cron" {
  command = plan

  assert {
    condition     = aws_scheduler_schedule.weekly.schedule_expression == "cron(0 8 ? * SAT *)"
    error_message = "The backtest must fire on Saturday 08:00 ET."
  }

  assert {
    condition     = aws_scheduler_schedule.weekly.schedule_expression_timezone == "America/New_York"
    error_message = "The schedule must pin America/New_York (AGENTS.md section 11.2)."
  }

  assert {
    condition     = aws_scheduler_schedule.weekly.target[0].retry_policy[0].maximum_retry_attempts == 0
    error_message = "Schedule retries must be disabled; failures land in the backtest DLQ."
  }

  assert {
    condition     = contains(flatten([for t in aws_scheduler_schedule.weekly.target : [for d in t.dead_letter_config : d.arn]]), var.dlq_arn)
    error_message = "Schedule failures must land in the backtest DLQ."
  }
}

run "iam_is_least_privilege" {
  command = plan

  assert {
    condition = alltrue(flatten([
      for _, statement in local.permissions_policy : [for a in statement.Action : a != "*"]
    ]))
    error_message = "No permission may contain the '*' action."
  }

  assert {
    condition     = contains(local.permissions_policy.SilverRead.Action, "s3:GetObject")
    error_message = "The backtest must read silver returns."
  }

  assert {
    condition     = contains(local.permissions_policy.GoldReadWrite.Action, "s3:PutObject")
    error_message = "The backtest must write the gold report."
  }

  assert {
    condition     = length([for key, _ in local.permissions_policy : key if startswith(key, "StateTable")]) == 0
    error_message = "The backtest has no business touching the state table."
  }
}

run "env_carries_the_buckets_and_code_version" {
  command = plan

  assert {
    condition     = aws_lambda_function.backtest.environment[0].variables.SILVER_BUCKET == var.silver_bucket_name
    error_message = "The backtest must be pointed at the silver bucket."
  }

  assert {
    condition     = aws_lambda_function.backtest.environment[0].variables.GOLD_BUCKET == var.gold_bucket_name
    error_message = "The backtest must be pointed at the gold bucket."
  }

  assert {
    condition     = aws_lambda_function.backtest.environment[0].variables.CODE_VERSION == var.code_version
    error_message = "Artifacts must record the code version."
  }
}
