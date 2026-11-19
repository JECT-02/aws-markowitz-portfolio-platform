mock_provider "aws" {
  mock_resource "aws_ecr_repository" {
    defaults = {
      # The URL is computed at apply time; pinning it lets the image_uri
      # assertions run at plan time (QA-002/QA-003).
      repository_url = "123456789012.dkr.ecr.us-east-1.amazonaws.com/portfolio-markowitz"
    }
  }
}

variables {
  project = "portfolio-markowitz-aws"
  env     = "dev"
  image_digests = {
    ingest    = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
    clean     = "sha256:2222222222222222222222222222222222222222222222222222222222222222"
    optimizer = "sha256:3333333333333333333333333333333333333333333333333333333333333333"
    monitor   = "sha256:4444444444444444444444444444444444444444444444444444444444444444"
  }
  code_version = "abc1234"
  # Same values as config/strategy.yaml; the variable validation rejects
  # incomplete or empty maps (see strategy_must_be_complete below).
  strategy = {
    risk_aversion           = "2.0"
    transaction_cost_lambda = "0.001"
    return_shrinkage_alpha  = "0.5"
    risk_free_rate_annual   = "0.04"
    max_forward_fill_days   = "2"
    window_days             = "252"
    ingest_lookback_days    = "1000"
    rebalance_l1_threshold  = "0.10"
    validation_gammas       = "1,2"
    validation_lambdas      = "0,0.001"
    validation_alphas       = "0,0.5"
    validation_windows      = "126,252"
    validation_folds        = "3"
  }
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
    monitor   = "arn:aws:sqs:us-east-1:123456789012:portfolio-markowitz-aws-dev-monitor-dlq"
  }
  alpaca_api_key_parameter    = "/portfolio-markowitz/dev/alpaca/api-key"
  alpaca_secret_key_parameter = "/portfolio-markowitz/dev/alpaca/secret-key"
}

run "image_digests_are_never_mutable_tags" {
  command = plan

  assert {
    condition     = alltrue([for digest in values(var.image_digests) : digest != "latest" && digest != ""])
    error_message = "The module must never run an image tagged latest."
  }

  assert {
    condition     = length(var.image_digests) == 4
    error_message = "One digest per function (ingest, clean, optimizer, monitor) is required."
  }
}

run "each_function_keeps_its_own_digest" {
  command = plan

  # QA-003: each function boots the image published in its own repository, so
  # no two functions may receive the same digest.
  assert {
    condition     = alltrue([for name in ["ingest", "clean", "optimizer", "monitor"] : contains(keys(var.image_digests), name)])
    error_message = "Every function needs its own image digest."
  }

  assert {
    condition     = length(distinct(values(var.image_digests))) == length(var.image_digests)
    error_message = "Two functions cannot share one image digest."
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
  # reviewed statically (trivy/tflint do not parse ASL): every lambda task
  # lifts its response with ResultSelector, every SNS publish sends
  # States.JsonToString($) and carries an explicit Catch.
  assert {
    condition     = aws_sfn_state_machine.daily_pipeline.type == "STANDARD"
    error_message = "The pipeline runs on a Standard state machine."
  }

  assert {
    condition     = aws_sfn_state_machine.daily_pipeline.logging_configuration[0].level == "ERROR"
    error_message = "Step Functions logs errors only."
  }
}

run "state_machine_reads_the_published_stage_contract" {
  command = plan

  assert {
    condition = output.stage_output_contract == {
      ingest = ["status", "run_id", "run_date", "source", "accepted"]
      clean  = ["status", "run_id", "run_date", "source"]
      optimize = [
        "status",
        "proposal_id",
        "run_id",
        "run_date",
        "weights",
        "drift_l1",
        "rebalance_required",
        "baseline_source",
      ]
    }
    error_message = "The stage output contract must stay aligned with the ResultSelector paths in the ASL and the handler tests."
  }
}

run "strategy_must_be_complete" {
  command = plan

  variables {
    strategy = {
      risk_aversion    = "2.0"
      window_days      = "252"
      validation_folds = "3"
    }
  }

  expect_failures = [var.strategy]
}

run "strategy_must_not_have_empty_values" {
  command = plan

  variables {
    strategy = {
      risk_aversion           = "2.0"
      transaction_cost_lambda = "0.001"
      return_shrinkage_alpha  = "0.5"
      risk_free_rate_annual   = "0.04"
      max_forward_fill_days   = "2"
      window_days             = "252"
      ingest_lookback_days    = "1000"
      rebalance_l1_threshold  = "0.10"
      validation_gammas       = "1,2"
      validation_lambdas      = "0,0.001"
      validation_alphas       = "0,0.5"
      validation_windows      = "126,252"
      validation_folds        = ""
    }
  }

  expect_failures = [var.strategy]
}

run "lambda_roles_can_write_their_own_dlq" {
  command = plan

  # N-02: an asynchronous invoke failure must land in the stage DLQ, so each
  # role needs SendMessage on its own queue.
  assert {
    condition = alltrue(flatten([
      for stage in ["ingest", "clean", "optimizer", "monitor"] : [
        contains(
          flatten([for st in local.permission_policies[stage].Statement : st.Action]),
          "sqs:SendMessage",
        ),
        contains(
          flatten([for st in local.permission_policies[stage].Statement : st.Resource]),
          var.dlq_arns[stage],
        ),
      ]
    ]))
    error_message = "Every pipeline lambda role must be able to send to its own DLQ."
  }
}

run "state_machine_timeout_covers_the_stage_budget" {
  command = plan

  # N-13: the state machine budget must cover the sum of the stage lambda
  # timeouts (plus the 60s scheduling buffer), never less; the ASL
  # definition embeds local.stage_timeout_total as its TimeoutSeconds.
  assert {
    condition = local.stage_timeout_total == (
      aws_lambda_function.pipeline["ingest"].timeout +
      aws_lambda_function.pipeline["clean"].timeout +
      aws_lambda_function.pipeline["optimizer"].timeout +
      60
    )
    error_message = "The state machine timeout must cover the whole stage budget."
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

run "monitor_is_a_bounded_container_image" {
  command = plan

  assert {
    condition     = aws_lambda_function.pipeline["monitor"].package_type == "Image"
    error_message = "The monitor ships as a container image."
  }

  assert {
    condition     = aws_lambda_function.pipeline["monitor"].memory_size == 512 && aws_lambda_function.pipeline["monitor"].timeout == 60
    error_message = "The monitor runs with 512 MB and a 60s timeout."
  }
}

run "monitor_paper_mode_is_forced" {
  command = plan

  assert {
    condition     = aws_lambda_function.pipeline["monitor"].environment[0].variables.ALPACA_PAPER == "true"
    error_message = "ALPACA_PAPER must be the immutable literal 'true' (AGENTS.md section 6.6)."
  }
}

run "monitor_iam_is_least_privilege" {
  command = plan

  assert {
    condition = alltrue(flatten([
      for st in local.permission_policies.monitor.Statement : [for a in st.Action : a != "*"]
    ]))
    error_message = "The monitor policy must not use wildcard actions."
  }

  assert {
    condition     = contains(flatten([for st in local.permission_policies.monitor.Statement : st.Action]), "dynamodb:PutItem")
    error_message = "The monitor may only append MONITOR records."
  }

  assert {
    condition     = length([for st in local.permission_policies.monitor.Statement : st.Action if contains(st.Action, "dynamodb:GetItem")]) == 0
    error_message = "The monitor must not read proposal or state items."
  }

  assert {
    condition     = length([for st in local.permission_policies.monitor.Statement : st.Action if contains(st.Action, "s3:PutObject")]) == 0
    error_message = "The monitor must not write to the lakehouse."
  }
}

run "monitor_schedule_uses_the_15_minute_ny_cron" {
  command = plan

  assert {
    condition     = aws_scheduler_schedule.monitor.schedule_expression == "cron(0/15 9-15 ? * MON-FRI *)"
    error_message = "The fast loop must fire every 15 minutes on market weekdays."
  }

  assert {
    condition     = aws_scheduler_schedule.monitor.schedule_expression_timezone == "America/New_York"
    error_message = "The schedule must pin America/New_York (AGENTS.md section 11.2)."
  }

  assert {
    condition     = aws_scheduler_schedule.monitor.target[0].retry_policy[0].maximum_retry_attempts == 0
    error_message = "Schedule retries must be disabled; failures land in the monitor DLQ."
  }

  assert {
    condition     = contains(flatten([for t in aws_scheduler_schedule.monitor.target : [for d in t.dead_letter_config : d.arn]]), var.dlq_arns["monitor"])
    error_message = "Schedule failures must land in the monitor DLQ."
  }
}

run "image_digests_must_be_an_immutable_digest" {
  command = plan

  variables {
    image_digests = {
      ingest    = "v1.2.3"
      clean     = "sha256:2222222222222222222222222222222222222222222222222222222222222222"
      optimizer = "sha256:3333333333333333333333333333333333333333333333333333333333333333"
      monitor   = "sha256:4444444444444444444444444444444444444444444444444444444444444444"
    }
  }

  expect_failures = [var.image_digests]
}
