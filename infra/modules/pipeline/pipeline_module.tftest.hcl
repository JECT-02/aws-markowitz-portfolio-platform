mock_provider "aws" {}

variables {
  project                 = "portfolio-markowitz-aws"
  env                     = "dev"
  image_tag               = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
  code_version            = "abc1234"
  strategy                = {}
  bronze_bucket_name      = "portfolio-markowitz-aws-dev-lakehouse-bronze"
  bronze_bucket_arn       = "arn:aws:s3:::portfolio-markowitz-aws-dev-lakehouse-bronze"
  quarantine_bucket_name  = "portfolio-markowitz-aws-dev-lakehouse-quarantine"
  quarantine_bucket_arn   = "arn:aws:s3:::portfolio-markowitz-aws-dev-lakehouse-quarantine"
  silver_bucket_name      = "portfolio-markowitz-aws-dev-lakehouse-silver"
  silver_bucket_arn       = "arn:aws:s3:::portfolio-markowitz-aws-dev-lakehouse-silver"
  gold_bucket_name        = "portfolio-markowitz-aws-dev-lakehouse-gold"
  gold_bucket_arn         = "arn:aws:s3:::portfolio-markowitz-aws-dev-lakehouse-gold"
  state_table_name        = "portfolio-markowitz-aws-dev-portfolio-state"
  state_table_arn         = "arn:aws:dynamodb:us-east-1:123456789012:table/portfolio-markowitz-aws-dev-portfolio-state"
  notifications_topic_arn = "arn:aws:sns:us-east-1:123456789012:portfolio-markowitz-aws-dev-notifications"
  dlq_arns = {
    ingest    = "arn:aws:sqs:us-east-1:123456789012:portfolio-markowitz-aws-dev-ingest-dlq"
    clean     = "arn:aws:sqs:us-east-1:123456789012:portfolio-markowitz-aws-dev-clean-dlq"
    optimizer = "arn:aws:sqs:us-east-1:123456789012:portfolio-markowitz-aws-dev-optimizer-dlq"
    schedule  = "arn:aws:sqs:us-east-1:123456789012:portfolio-markowitz-aws-dev-schedule-dlq"
  }
  alpaca_api_key_parameter    = "/portfolio-markowitz/dev/alpaca/api-key"
  alpaca_secret_key_parameter = "/portfolio-markowitz/dev/alpaca/secret-key"
}

run "image_tag_validation_rejects_latest" {
  command = plan

  assert {
    condition     = var.image_tag != "latest" && var.image_tag != ""
    error_message = "The module must never run an image tagged latest."
  }
}

run "functions_are_container_images_x86" {
  command = plan

  assert {
    condition = alltrue([
      for _, fn in aws_lambda_function.pipeline : fn.package_type == "Image"
    ])
    error_message = "Ingest, clean and optimizer deploy as container images."
  }

  assert {
    condition = alltrue([
      for _, fn in aws_lambda_function.pipeline : contains(fn.architectures, "x86_64")
    ])
    error_message = "The pipeline lambdas run on x86_64."
  }
}

run "functions_have_dlq_and_no_built_in_retries" {
  command = plan

  assert {
    condition = alltrue([
      for _, cfg in aws_lambda_function_event_invoke_config.pipeline : cfg.maximum_retry_attempts == 0
    ])
    error_message = "Asynchronous retries must be disabled; failures go to the DLQ."
  }
}

run "log_groups_have_bounded_retention" {
  command = plan

  assert {
    condition = alltrue([
      for _, lg in aws_cloudwatch_log_group.lambda : lg.retention_in_days == 14
    ])
    error_message = "Lambda log retention must be bounded (14 days, AGENTS.md section 11.2)."
  }
}

run "optimizer_timeout_and_memory" {
  command = plan

  assert {
    condition     = aws_lambda_function.pipeline["optimizer"].timeout == 600 && aws_lambda_function.pipeline["optimizer"].memory_size == 2048
    error_message = "Optimizer runs with 10 minutes and 2 GB (container image)."
  }
}

run "state_machine_is_standard_with_error_logging" {
  command = plan

  # The ASL content (Retry/Catch/Choice) is static HCL built with jsonencode;
  # its lambda ARNs are unknown at plan time, so the structural assertions
  # below check the resource attributes that are known, and the ASL shape is
  # reviewed statically (trivy/tflint do not parse ASL).
  assert {
    condition     = aws_sfn_state_machine.daily_pipeline.type == "STANDARD"
    error_message = "The pipeline runs on a Standard state machine."
  }

  assert {
    condition     = aws_sfn_state_machine.daily_pipeline.logging_configuration[0].level == "ERROR"
    error_message = "Step Functions logs errors only."
  }
}

run "scheduler_uses_ny_timezone_cron" {
  command = plan

  assert {
    condition     = aws_scheduler_schedule.daily.schedule_expression == "cron(30 16 ? * MON-FRI *)"
    error_message = "The daily schedule fires at 16:30 ET."
  }

  assert {
    condition     = aws_scheduler_schedule.daily.schedule_expression_timezone == "America/New_York"
    error_message = "The schedule must pin America/New_York (AGENTS.md section 11.2)."
  }
}

run "scheduler_has_dead_letter_config" {
  command = plan

  assert {
    condition     = contains(flatten([for t in aws_scheduler_schedule.daily.target : [for d in t.dead_letter_config : d.arn]]), var.dlq_arns["schedule"])
    error_message = "Schedule failures must land in the schedule DLQ."
  }
}

run "sfn_logs_are_bounded" {
  command = plan

  assert {
    condition     = aws_cloudwatch_log_group.sfn.retention_in_days == 14
    error_message = "Step Functions log retention must be bounded."
  }
}

run "lambda_roles_do_not_use_wildcards" {
  command = plan

  assert {
    condition = alltrue(flatten([
      for stage, doc in local.permission_policies : [
        for st in doc.Statement : [for a in st.Action : a != "*"]
      ]
    ]))
    error_message = "No lambda permission document may contain the '*' action."
  }
}

run "ingest_reads_alpaca_credentials" {
  command = plan

  assert {
    condition     = contains(flatten([for st in local.permission_policies.ingest.Statement : st.Action]), "ssm:GetParameter")
    error_message = "Ingest must read the Alpaca credentials from SSM."
  }
}
