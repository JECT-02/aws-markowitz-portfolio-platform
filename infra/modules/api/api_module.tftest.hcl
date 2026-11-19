mock_provider "aws" {
  mock_resource "aws_ecr_repository" {
    defaults = {
      # The URL is computed at apply time; pinning it lets the image_uri
      # assertions run at plan time (QA-002/QA-003).
      repository_url = "123456789012.dkr.ecr.us-east-1.amazonaws.com/portfolio-markowitz"
    }
  }
  mock_data "aws_region" {
    defaults = {
      name = "us-east-1"
    }
  }
}

variables {
  project = "portfolio-markowitz-aws"
  env     = "dev"
  image_digests = {
    reader   = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
    executor = "sha256:2222222222222222222222222222222222222222222222222222222222222222"
  }
  code_version           = "abc1234"
  spa_allowed_origin     = "https://spa.example.com"
  bronze_bucket_name     = "portfolio-markowitz-aws-dev-lakehouse-bronze"
  quarantine_bucket_name = "portfolio-markowitz-aws-dev-lakehouse-quarantine"
  silver_bucket_name     = "portfolio-markowitz-aws-dev-lakehouse-silver"
  gold_bucket_name       = "portfolio-markowitz-aws-dev-lakehouse-gold"
  gold_bucket_arn        = "arn:aws:s3:::portfolio-markowitz-aws-dev-lakehouse-gold"
  state_table_name       = "portfolio-markowitz-aws-dev-portfolio-state"
  state_table_arn        = "arn:aws:dynamodb:us-east-1:123456789012:table/portfolio-markowitz-aws-dev-portfolio-state"

  alpaca_api_key_parameter    = "/portfolio-markowitz/dev/alpaca/api-key"
  alpaca_secret_key_parameter = "/portfolio-markowitz/dev/alpaca/secret-key"
  notifications_topic_arn     = "arn:aws:sns:us-east-1:123456789012:portfolio-markowitz-aws-dev-notifications"
}

run "reader_is_a_bounded_container_image" {
  command = plan

  assert {
    condition     = aws_lambda_function.reader.package_type == "Image"
    error_message = "The Reader ships as a container image (pyarrow)."
  }

  assert {
    condition     = aws_lambda_function.reader.memory_size == 256 && aws_lambda_function.reader.timeout == 10
    error_message = "Reader runs with 256 MB and 10 seconds."
  }
}

run "reader_log_group_is_bounded" {
  command = plan

  assert {
    condition     = aws_cloudwatch_log_group.reader.retention_in_days == 14
    error_message = "Reader log retention must be bounded (14 days)."
  }
}

run "reader_iam_is_least_privilege" {
  command = plan

  assert {
    condition = alltrue(flatten([
      for st in jsondecode(aws_iam_role_policy.reader_permissions.policy).Statement : [for a in try(tolist(st.Action), [st.Action]) : a != "*"]
    ]))
    error_message = "The Reader policy must not use wildcard actions."
  }

  assert {
    condition = alltrue(flatten([
      for st in jsondecode(aws_iam_role_policy.reader_permissions.policy).Statement : [for r in try(tolist(st.Resource), [st.Resource]) : r != "*"]
    ]))
    error_message = "The Reader policy must not use wildcard resources."
  }
}

run "all_get_routes_require_jwt" {
  command = plan

  assert {
    condition     = length(keys(aws_apigatewayv2_route.reader_get)) == 4
    error_message = "Exactly the four reader routes must exist."
  }

  assert {
    condition = alltrue([
      for _, route in aws_apigatewayv2_route.reader_get : route.authorization_type == "JWT"
    ])
    error_message = "Every reader route must require the Cognito JWT authorizer."
  }
}

run "stage_is_throttled" {
  command = plan

  assert {
    condition     = aws_apigatewayv2_stage.default.default_route_settings[0].throttling_burst_limit == 5 && aws_apigatewayv2_stage.default.default_route_settings[0].throttling_rate_limit == 10.0
    error_message = "The default stage must be throttled (5 burst / 10 rate)."
  }
}

run "cognito_password_policy" {
  command = plan

  assert {
    condition     = aws_cognito_user_pool.users.password_policy[0].minimum_length == 12
    error_message = "Cognito passwords must be at least 12 characters."
  }
}

run "cognito_mfa_is_optional_with_software_token" {
  command = plan

  assert {
    condition     = aws_cognito_user_pool.users.mfa_configuration == "OPTIONAL"
    error_message = "MFA stays OPTIONAL (recommended) for the single-user workload."
  }

  assert {
    condition     = aws_cognito_user_pool.users.software_token_mfa_configuration[0].enabled
    error_message = "The software token (OTP) second factor must be enabled."
  }
}

run "self_sign_up_is_disabled" {
  command = plan

  assert {
    condition     = aws_cognito_user_pool.users.admin_create_user_config[0].allow_admin_create_user_only
    error_message = "No public self-registration: users are created by the admin."
  }
}

run "spa_client_has_pkce_code_flow_when_origin_configured" {
  command = plan

  assert {
    condition     = aws_cognito_user_pool_client.spa.allowed_oauth_flows_user_pool_client && contains(aws_cognito_user_pool_client.spa.allowed_oauth_flows, "code")
    error_message = "With a SPA origin the client must use the authorization code grant (PKCE)."
  }

  assert {
    condition     = !aws_cognito_user_pool_client.spa.generate_secret
    error_message = "The SPA client must not have a secret (public PKCE client)."
  }
}

run "looker_connector_client_is_a_confidential_code_flow_client" {
  command = plan

  assert {
    condition     = aws_cognito_user_pool_client.looker_connector.generate_secret
    error_message = "The Looker Studio connector must be a confidential client with a secret."
  }

  assert {
    condition     = length(var.looker_connector_callback_urls) == 0 ? aws_cognito_user_pool_client.looker_connector.allowed_oauth_flows_user_pool_client == false : contains(aws_cognito_user_pool_client.looker_connector.allowed_oauth_flows, "code")
    error_message = "The connector OAuth flows stay disabled until callback URLs are registered."
  }

  assert {
    condition     = aws_cognito_user_pool_client.looker_connector.refresh_token_validity == 90
    error_message = "The connector refresh token must be capped at 90 days."
  }
}

# The JWT authorizer audience composition ([spa.id, looker_connector.id]) and
# resource ids stay unknown under the mock provider, exactly like the state
# machine ASL; the composition is static HCL and is reviewed in the plan.

run "cors_is_restricted_to_the_spa_origin" {
  command = plan

  assert {
    condition     = contains(flatten([for c in aws_apigatewayv2_api.reader.cors_configuration : c.allow_origins]), "https://spa.example.com")
    error_message = "CORS must be restricted to the configured SPA origin."
  }

  assert {
    condition     = contains(flatten([for c in aws_apigatewayv2_api.reader.cors_configuration : c.allow_methods]), "POST")
    error_message = "CORS must allow POST for the rebalance route."
  }
}

run "executor_is_a_bounded_container_image" {
  command = plan

  assert {
    condition     = aws_lambda_function.executor.package_type == "Image"
    error_message = "The Executor ships as a container image."
  }

  assert {
    condition     = aws_lambda_function.executor.memory_size == 512 && aws_lambda_function.executor.timeout == 25
    error_message = "The Executor runs with 512 MB and a 25s timeout (below the 29s HTTP API sync cap)."
  }
}

run "executor_log_group_is_bounded" {
  command = plan

  assert {
    condition     = aws_cloudwatch_log_group.executor.retention_in_days == 14
    error_message = "Executor log retention must be bounded (14 days)."
  }
}

run "executor_iam_is_least_privilege" {
  command = plan

  assert {
    condition = alltrue(flatten([
      for st in jsondecode(aws_iam_role_policy.executor_permissions.policy).Statement : [for a in try(tolist(st.Action), [st.Action]) : a != "*"]
    ]))
    error_message = "The Executor policy must not use wildcard actions."
  }

  assert {
    condition = alltrue(flatten([
      for st in jsondecode(aws_iam_role_policy.executor_permissions.policy).Statement : [for r in try(tolist(st.Resource), [st.Resource]) : r != "*"]
    ]))
    error_message = "The Executor policy must not use wildcard resources."
  }
}

run "executor_reads_only_the_current_alpaca_parameters" {
  command = plan

  assert {
    condition = alltrue(flatten([
      for st in jsondecode(aws_iam_role_policy.executor_permissions.policy).Statement : [for a in try(tolist(st.Action), [st.Action]) : a != "ssm:GetParameterHistory"]
    ]))
    error_message = "The executor must not read parameter history (superseded credentials)."
  }
}

run "executor_paper_mode_is_forced" {
  command = plan

  assert {
    condition     = aws_lambda_function.executor.environment[0].variables.ALPACA_PAPER == "true"
    error_message = "ALPACA_PAPER must be the immutable literal 'true' (AGENTS.md section 6.6)."
  }

  assert {
    condition     = aws_lambda_function.executor.environment[0].variables.ALPACA_API_KEY_PARAMETER == var.alpaca_api_key_parameter
    error_message = "The executor must read the Alpaca key from the documented SSM path."
  }
}

run "rebalance_route_requires_jwt" {
  command = plan

  assert {
    condition     = aws_apigatewayv2_route.executor_rebalance.route_key == "POST /rebalance"
    error_message = "Exactly one rebalance route must exist."
  }

  assert {
    condition     = aws_apigatewayv2_route.executor_rebalance.authorization_type == "JWT"
    error_message = "The rebalance route must require the Cognito JWT authorizer."
  }
}

run "executor_execution_deadline_stays_inside_the_lambda_timeout" {
  command = plan

  assert {
    condition     = var.executor_execution_timeout_seconds < aws_lambda_function.executor.timeout
    error_message = "The fill polling deadline must stay below the Lambda timeout."
  }
}

run "executor_carries_the_allowed_groups_and_the_cognito_group" {
  command = plan

  assert {
    condition     = aws_lambda_function.executor.environment[0].variables.EXECUTOR_ALLOWED_GROUPS == var.executor_allowed_groups && aws_cognito_user_group.executors.name == var.executor_allowed_groups
    error_message = "The executor must declare the allowed Cognito group and the group must exist (N-16)."
  }
}

run "image_digests_must_be_an_immutable_digest" {
  command = plan

  variables {
    image_digests = {
      reader   = "v1.2.3"
      executor = "sha256:2222222222222222222222222222222222222222222222222222222222222222"
    }
  }

  expect_failures = [var.image_digests]
}

run "reader_and_executor_keep_separate_digests" {
  command = plan

  # QA-003: one digest identifies a manifest inside a single repository, so the
  # Reader and the Executor can never share it.
  assert {
    condition     = contains(keys(var.image_digests), "reader") && contains(keys(var.image_digests), "executor")
    error_message = "Every function needs its own image digest."
  }

  assert {
    condition     = var.image_digests["reader"] != var.image_digests["executor"]
    error_message = "The Reader and the Executor cannot share one digest."
  }
}

run "cognito_hosted_ui_url_uses_the_regional_domain" {
  command = plan

  # The provider attribute is only the prefix; the hosted UI needs the
  # ".auth.<region>.amazoncognito.com" host (mistakes.md QA-006).
  assert {
    condition     = output.cognito_hosted_ui_url == "https://portfolio-markowitz.auth.us-east-1.amazoncognito.com"
    error_message = "The hosted UI URL must be https://<prefix>.auth.<region>.amazoncognito.com."
  }
}
