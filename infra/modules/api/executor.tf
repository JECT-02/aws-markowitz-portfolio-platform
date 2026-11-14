# Executor lambda behind the authenticated POST /rebalance route.
# Mirrors the reader packaging: a pinned container image in ECR, a
# least-privilege role and a 14-day log group. The Lambda is invoked
# synchronously by the HTTP API, so no DLQ/event-invoke config applies.

variable "alpaca_api_key_parameter" {
  type        = string
  description = "SSM SecureString path holding the Alpaca API key id."
}

variable "alpaca_secret_key_parameter" {
  type        = string
  description = "SSM SecureString path holding the Alpaca secret key."
}

variable "notifications_topic_arn" {
  type        = string
  description = "SNS topic ARN where final rebalance outcomes are published."
}

variable "executor_min_order_notional" {
  type        = number
  description = "Dust filter: orders below this USD notional are skipped."
  default     = 1.0
  validation {
    condition     = var.executor_min_order_notional > 0
    error_message = "The minimum order notional must be positive."
  }
}

variable "executor_max_price_drift_pct" {
  type        = number
  description = "Maximum allowed price drift between the proposal and now (0.02 = 2%)."
  default     = 0.02
  validation {
    condition     = var.executor_max_price_drift_pct > 0 && var.executor_max_price_drift_pct <= 1
    error_message = "The price drift threshold must be in (0, 1]."
  }
}

variable "executor_execution_timeout_seconds" {
  type        = number
  description = "How long the executor polls for order fills; must stay below the 25s Lambda timeout."
  default     = 15
  validation {
    condition     = var.executor_execution_timeout_seconds > 0 && var.executor_execution_timeout_seconds < 25
    error_message = "The execution timeout must be between 1 and 24 seconds."
  }
}

resource "aws_ecr_repository" "executor" {
  name                 = "${var.project}-executor-${var.env}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.tags
}

resource "aws_ecr_lifecycle_policy" "executor" {
  repository = aws_ecr_repository.executor.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged executor images after 14 days"
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

resource "aws_iam_role" "executor" {
  name = "${var.project}-${var.env}-executor-lambda"
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

resource "aws_cloudwatch_log_group" "executor" {
  name              = "/aws/lambda/${var.project}-executor-${var.env}"
  retention_in_days = local.log_retention_days

  tags = local.tags
}

resource "aws_iam_role_policy" "executor_logs" {
  name = "logs"
  role = aws_iam_role.executor.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["${aws_cloudwatch_log_group.executor.arn}:*"]
      },
      {
        Effect   = "Allow"
        Action   = "logs:CreateLogGroup"
        Resource = ["arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project}-executor-${var.env}"]
      },
    ]
  })
}

resource "aws_iam_role_policy" "executor_permissions" {
  name = "permissions"
  role = aws_iam_role.executor.id
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
        Sid      = "StateTableReadWrite"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = [var.state_table_arn]
      },
      {
        Sid    = "AlpacaCredentialsRead"
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:GetParameterHistory"]
        Resource = [
          "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter${var.alpaca_api_key_parameter}",
          "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter${var.alpaca_secret_key_parameter}",
        ]
      },
      {
        Sid      = "NotifyRebalanceOutcome"
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = [var.notifications_topic_arn]
      },
    ]
  })
}

resource "aws_lambda_function" "executor" {
  function_name = "${var.project}-executor-${var.env}"
  role          = aws_iam_role.executor.arn
  description   = "Executes approved rebalance proposals against the paper broker"

  package_type  = "Image"
  architectures = ["x86_64"]
  image_uri     = "${aws_ecr_repository.executor.repository_url}:${var.image_tag}"

  image_config {
    command = ["executor.handler.handler"]
  }

  # HTTP API v2 synchronous invocations cap at 29s; leave headroom for the
  # platform while the fill polling deadline (15s) stays inside the window.
  memory_size = 512
  timeout     = 25

  environment {
    variables = {
      CODE_VERSION                       = var.code_version
      CONFIG_DIR                         = "/var/task"
      BRONZE_BUCKET                      = var.bronze_bucket_name
      QUARANTINE_BUCKET                  = var.quarantine_bucket_name
      SILVER_BUCKET                      = var.silver_bucket_name
      GOLD_BUCKET                        = var.gold_bucket_name
      STATE_TABLE                        = var.state_table_name
      ALPACA_PAPER                       = "true"
      ALPACA_API_KEY_PARAMETER           = var.alpaca_api_key_parameter
      ALPACA_SECRET_KEY_PARAMETER        = var.alpaca_secret_key_parameter
      NOTIFICATIONS_TOPIC_ARN            = var.notifications_topic_arn
      EXECUTOR_MIN_ORDER_NOTIONAL        = tostring(var.executor_min_order_notional)
      EXECUTOR_MAX_PRICE_DRIFT_PCT       = tostring(var.executor_max_price_drift_pct)
      EXECUTOR_EXECUTION_TIMEOUT_SECONDS = tostring(var.executor_execution_timeout_seconds)
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.executor,
    aws_iam_role_policy.executor_logs,
    aws_iam_role_policy.executor_permissions,
  ]

  tags = local.tags
}

resource "aws_apigatewayv2_integration" "executor" {
  api_id           = aws_apigatewayv2_api.reader.id
  integration_type = "AWS_PROXY"

  integration_uri        = "arn:aws:apigateway:${data.aws_region.current.name}:lambda:path/2015-03-31/functions/${aws_lambda_function.executor.arn}/invocations"
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "executor_rebalance" {
  api_id             = aws_apigatewayv2_api.reader.id
  route_key          = "POST /rebalance"
  target             = "integrations/${aws_apigatewayv2_integration.executor.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_lambda_permission" "api_executor" {
  statement_id  = "AllowExecutionFromHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.executor.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.reader.execution_arn}/*/*/*"
}

output "executor_function_name" {
  description = "Executor lambda name."
  value       = aws_lambda_function.executor.function_name
}

output "executor_ecr_repository_uri" {
  description = "Executor ECR repository URI."
  value       = aws_ecr_repository.executor.repository_url
}
