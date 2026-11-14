mock_provider "aws" {}

variables {
  project                = "portfolio-markowitz-aws"
  env                    = "dev"
  image_tag              = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
  code_version           = "abc1234"
  spa_allowed_origin     = "https://spa.example.com"
  bronze_bucket_name     = "portfolio-markowitz-aws-dev-lakehouse-bronze"
  quarantine_bucket_name = "portfolio-markowitz-aws-dev-lakehouse-quarantine"
  silver_bucket_name     = "portfolio-markowitz-aws-dev-lakehouse-silver"
  gold_bucket_name       = "portfolio-markowitz-aws-dev-lakehouse-gold"
  gold_bucket_arn        = "arn:aws:s3:::portfolio-markowitz-aws-dev-lakehouse-gold"
  state_table_name       = "portfolio-markowitz-aws-dev-portfolio-state"
  state_table_arn        = "arn:aws:dynamodb:us-east-1:123456789012:table/portfolio-markowitz-aws-dev-portfolio-state"
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

run "cors_is_restricted_to_the_spa_origin" {
  command = plan

  assert {
    condition     = contains(flatten([for c in aws_apigatewayv2_api.reader.cors_configuration : c.allow_origins]), "https://spa.example.com")
    error_message = "CORS must be restricted to the configured SPA origin."
  }
}
