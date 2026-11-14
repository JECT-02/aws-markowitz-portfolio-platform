# Cognito authentication and the read-only HTTP API for the Reader
# (module `api`). Mirrors the CDK ApiStack. The Reader ships as a container
# image because its requirements include pyarrow (Parquet reads), too heavy
# for a zip package (AGENTS.md section 11.2 archive_file applies only to
# lightweight lambdas).

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
  log_retention_days = 14

  tags = {
    project     = var.project
    env         = var.env
    owner       = "JECT"
    cost-center = "personal-portfolio"
  }
}

variable "project" {
  type        = string
  description = "Project identifier used for resource names and tags."
}

variable "env" {
  type        = string
  description = "Deployment environment (dev or prod)."
}

variable "image_tag" {
  type        = string
  description = "Pinned ECR tag or digest for the Reader image. NEVER 'latest' (AGENTS.md section 11.2)."

  validation {
    condition     = var.image_tag != "" && var.image_tag != "latest"
    error_message = "image_tag must be a pinned tag or digest; 'latest' is forbidden."
  }
}

variable "code_version" {
  type        = string
  description = "Git SHA recorded in logs."
}

variable "spa_allowed_origin" {
  type        = string
  description = "HTTPS origin of the SPA; empty disables OAuth flows, CORS and restricts nothing else."
  default     = ""
}

variable "bronze_bucket_name" {
  type        = string
  description = "Bronze bucket name (exposed to the Reader for error context)."
}

variable "quarantine_bucket_name" {
  type        = string
  description = "Quarantine bucket name."
}

variable "silver_bucket_name" {
  type        = string
  description = "Silver bucket name."
}

variable "gold_bucket_name" {
  type        = string
  description = "Gold bucket name (read by the Reader)."
}

variable "gold_bucket_arn" {
  type        = string
  description = "Gold bucket ARN."
}

variable "state_table_name" {
  type        = string
  description = "PortfolioState DynamoDB table name."
}

variable "state_table_arn" {
  type        = string
  description = "PortfolioState DynamoDB table ARN."
}

# --- Cognito --------------------------------------------------------------------

resource "aws_cognito_user_pool" "users" {
  name = "${var.project}-users-${var.env}"

  # Sign up is human-administered only: no public self-registration.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  auto_verified_attributes = ["email"]
  username_attributes      = ["email"]

  mfa_configuration = "OPTIONAL"
  software_token_mfa_configuration {
    enabled = true
  }

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "admin_only"
      priority = 1
    }
  }

  deletion_protection = "ACTIVE"

  tags = local.tags
}

resource "aws_cognito_user_pool_domain" "spa" {
  domain       = "${var.project}-${var.env}"
  user_pool_id = aws_cognito_user_pool.users.id
}

resource "aws_cognito_user_pool_client" "spa" {
  name                = "${var.project}-spa-${var.env}"
  user_pool_id        = aws_cognito_user_pool.users.id
  generate_secret     = false
  explicit_auth_flows = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]

  # PKCE authorization code grant for the SPA, only when the SPA origin is
  # configured (same conditional as the CDK web client).
  allowed_oauth_flows_user_pool_client = var.spa_allowed_origin != ""
  allowed_oauth_flows                  = var.spa_allowed_origin != "" ? ["code"] : null
  allowed_oauth_scopes                 = var.spa_allowed_origin != "" ? ["openid", "email", "profile"] : null
  callback_urls                        = var.spa_allowed_origin != "" ? [var.spa_allowed_origin] : null
  logout_urls                          = var.spa_allowed_origin != "" ? [var.spa_allowed_origin] : null
  supported_identity_providers         = var.spa_allowed_origin != "" ? ["COGNITO"] : null

  prevent_user_existence_errors = "ENABLED"
}

# --- Reader lambda ----------------------------------------------------------------

resource "aws_ecr_repository" "reader" {
  name                 = "${var.project}-reader-${var.env}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.tags
}

resource "aws_ecr_lifecycle_policy" "reader" {
  repository = aws_ecr_repository.reader.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged reader images after 14 days"
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

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

resource "aws_iam_role" "reader" {
  name = "${var.project}-${var.env}-reader-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "sts:AssumeRole"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      },
    ]
  })

  tags = local.tags
}

resource "aws_cloudwatch_log_group" "reader" {
  name              = "/aws/lambda/${var.project}-reader-${var.env}"
  retention_in_days = local.log_retention_days

  tags = local.tags
}

resource "aws_iam_role_policy" "reader_logs" {
  name = "logs"
  role = aws_iam_role.reader.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["${aws_cloudwatch_log_group.reader.arn}:*"]
      },
      {
        Effect   = "Allow"
        Action   = "logs:CreateLogGroup"
        Resource = ["arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project}-reader-${var.env}"]
      },
    ]
  })
}

resource "aws_iam_role_policy" "reader_permissions" {
  name = "permissions"
  role = aws_iam_role.reader.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "GoldRead"
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = ["${var.gold_bucket_arn}/*"]
      },
      {
        Sid      = "StateTableRead"
        Effect   = "Allow"
        Action   = "dynamodb:GetItem"
        Resource = [var.state_table_arn]
      },
    ]
  })
}

resource "aws_lambda_function" "reader" {
  function_name = "${var.project}-reader-${var.env}"
  role          = aws_iam_role.reader.arn
  description   = "Read-only portfolio API"

  package_type  = "Image"
  architectures = ["x86_64"]
  image_uri     = "${aws_ecr_repository.reader.repository_url}:${var.image_tag}"

  image_config {
    command = ["reader.handler.handler"]
  }

  memory_size = 256
  timeout     = 10

  environment {
    variables = {
      CODE_VERSION      = var.code_version
      CONFIG_DIR        = "/var/task"
      BRONZE_BUCKET     = var.bronze_bucket_name
      QUARANTINE_BUCKET = var.quarantine_bucket_name
      SILVER_BUCKET     = var.silver_bucket_name
      GOLD_BUCKET       = var.gold_bucket_name
      STATE_TABLE       = var.state_table_name
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.reader,
    aws_iam_role_policy.reader_logs,
    aws_iam_role_policy.reader_permissions,
  ]

  tags = local.tags
}

# --- HTTP API ----------------------------------------------------------------------

resource "aws_apigatewayv2_api" "reader" {
  name          = "${var.project}-reader-${var.env}"
  protocol_type = "HTTP"

  dynamic "cors_configuration" {
    for_each = var.spa_allowed_origin != "" ? [1] : []
    content {
      allow_origins = [var.spa_allowed_origin]
      allow_methods = ["GET"]
      allow_headers = ["Authorization"]
      max_age       = 3600
    }
  }

  tags = local.tags
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.reader.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "cognito"

  jwt_configuration {
    audience = [aws_cognito_user_pool_client.spa.id]
    issuer   = "https://cognito-idp.${data.aws_region.current.name}.amazonaws.com/${aws_cognito_user_pool.users.id}"
  }
}

resource "aws_apigatewayv2_integration" "reader" {
  api_id           = aws_apigatewayv2_api.reader.id
  integration_type = "AWS_PROXY"

  integration_uri        = "arn:aws:apigateway:${data.aws_region.current.name}:lambda:path/2015-03-31/functions/${aws_lambda_function.reader.arn}/invocations"
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "reader_get" {
  for_each = toset(["/proposal", "/metrics", "/monitor", "/backtest"])

  api_id             = aws_apigatewayv2_api.reader.id
  route_key          = "GET ${each.key}"
  target             = "integrations/${aws_apigatewayv2_integration.reader.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_stage" "default" {
  api_id = aws_apigatewayv2_api.reader.id
  name   = "$default"

  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 5
    throttling_rate_limit  = 10.0
  }

  tags = local.tags
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowExecutionFromHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.reader.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.reader.execution_arn}/*/*/*"
}

# --- Outputs -------------------------------------------------------------------------

output "api_endpoint" {
  description = "Base URL of the reader HTTP API."
  value       = aws_apigatewayv2_api.reader.api_endpoint
}

output "user_pool_id" {
  description = "Cognito user pool id."
  value       = aws_cognito_user_pool.users.id
}

output "web_client_id" {
  description = "Cognito app client id for the SPA."
  value       = aws_cognito_user_pool_client.spa.id
}

output "cognito_domain" {
  description = "Cognito hosted UI domain."
  value       = aws_cognito_user_pool_domain.spa.domain
}

output "reader_function_name" {
  description = "Reader lambda name."
  value       = aws_lambda_function.reader.function_name
}

output "reader_ecr_repository_uri" {
  description = "Reader ECR repository URI."
  value       = aws_ecr_repository.reader.repository_url
}
