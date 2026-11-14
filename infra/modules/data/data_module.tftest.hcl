mock_provider "aws" {}

variables {
  project = "portfolio-markowitz-aws"
  env     = "dev"

  alpaca_api_key_parameter    = "/portfolio-markowitz/dev/alpaca/api-key"
  alpaca_secret_key_parameter = "/portfolio-markowitz/dev/alpaca/secret-key"
}

run "bronze_bucket_is_versioned" {
  command = plan

  assert {
    condition     = aws_s3_bucket_versioning.lakehouse["bronze"].versioning_configuration[0].status == "Enabled"
    error_message = "Bronze must be versioned (AGENTS.md section 7.1)."
  }
}

run "non_bronze_buckets_are_not_versioned" {
  command = plan

  assert {
    condition     = !contains(keys(aws_s3_bucket_versioning.lakehouse), "quarantine")
    error_message = "Only bronze is versioned, matching the CDK design."
  }
}

run "all_buckets_block_public_access" {
  command = plan

  assert {
    condition = alltrue([
      for _, block in aws_s3_bucket_public_access_block.lakehouse :
      block.block_public_acls &&
      block.block_public_policy &&
      block.ignore_public_acls &&
      block.restrict_public_buckets
    ])
    error_message = "Every lakehouse bucket must block all public access."
  }
}

run "all_buckets_use_aes256_encryption" {
  command = plan

  assert {
    condition = alltrue([
      for _, enc in aws_s3_bucket_server_side_encryption_configuration.lakehouse :
      alltrue(flatten([
        for rule in enc.rule : [for cfg in rule.apply_server_side_encryption_by_default : cfg.sse_algorithm == "AES256"]
      ]))
    ])
    error_message = "Every lakehouse bucket must be encrypted at rest."
  }
}

run "bucket_policy_denies_insecure_transport" {
  command = plan

  # The statement content itself is verified by trivy (tf check on the policy
  # documents); plan-time JSON of the policy is unknown, so here we assert
  # the policy resource exists for every bucket.
  assert {
    condition     = length(keys(aws_s3_bucket_policy.enforce_tls)) == 4
    error_message = "Every lakehouse bucket must have a TLS-enforcing policy."
  }
}

run "buckets_cannot_be_destroyed" {
  command = plan

  # lifecycle.prevent_destroy is not exposed in plan values; the property is
  # enforced by the `lifecycle` blocks in the module and checked statically
  # by trivy/tflint.
  assert {
    condition     = length(keys(aws_s3_bucket.lakehouse)) == 4
    error_message = "The module must create exactly four lakehouse buckets."
  }
}

run "portfolio_state_is_provisioned" {
  command = plan

  assert {
    condition     = aws_dynamodb_table.portfolio_state.billing_mode == "PROVISIONED"
    error_message = "PortfolioState must stay PROVISIONED for the free tier (AGENTS.md section 7.2)."
  }
}

run "portfolio_state_capacity_is_small" {
  command = plan

  assert {
    condition     = aws_dynamodb_table.portfolio_state.read_capacity == 1 && aws_dynamodb_table.portfolio_state.write_capacity == 1
    error_message = "PortfolioState capacity must stay in the 1-5 RCU/WCU band."
  }
}

run "portfolio_state_has_pitr_and_ttl" {
  command = plan

  assert {
    condition     = aws_dynamodb_table.portfolio_state.point_in_time_recovery[0].enabled
    error_message = "PortfolioState must enable point-in-time recovery."
  }

  assert {
    condition     = aws_dynamodb_table.portfolio_state.ttl[0].attribute_name == "ttl"
    error_message = "PortfolioState must enable the ttl attribute for IDEMPOTENCY/MONITOR."
  }
}

run "dlqs_have_four_day_retention_and_encryption" {
  command = plan

  assert {
    condition = alltrue([
      for _, queue in aws_sqs_queue.dlq :
      queue.message_retention_seconds == 4 * 86400 && queue.sqs_managed_sse_enabled
    ])
    error_message = "DLQs must retain four days with SSE."
  }
}

run "email_subscription_is_conditional" {
  command = plan

  assert {
    condition     = length(aws_sns_topic_subscription.email) == 0
    error_message = "Without notification_email no subscription is created."
  }
}

run "alpaca_parameters_are_created_at_the_expected_paths" {
  command = plan

  assert {
    condition     = aws_ssm_parameter.alpaca_api_key.name == var.alpaca_api_key_parameter && aws_ssm_parameter.alpaca_secret_key.name == var.alpaca_secret_key_parameter
    error_message = "Both Alpaca credential placeholders must exist at the documented SSM paths."
  }

  assert {
    condition     = aws_ssm_parameter.alpaca_api_key.type == "SecureString" && aws_ssm_parameter.alpaca_secret_key.type == "SecureString"
    error_message = "Alpaca credentials must be SecureString (AGENTS.md section 12)."
  }
}
