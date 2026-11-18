# Weekly backtest loop (module `backtest`): nested temporal validation over the
# silver lakehouse with a gold-layer report, triggered by its own weekly
# EventBridge Scheduler. Kept out of the daily pipeline by design (AGENTS.md
# section 5.6: the backtest never runs inside the daily optimizer lambda).

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
  schedule_expression = "cron(0 8 ? * SAT *)"
  schedule_timezone   = "America/New_York"
  log_retention_days  = 14

  # Mirrors infra/modules/pipeline/main.tf strategy_env_names: config/strategy.yaml
  # fields -> lambda environment variable names, rendered from `var.strategy`.
  strategy_env_names = {
    risk_aversion           = "RISK_AVERSION"
    transaction_cost_lambda = "TRANSACTION_COST_LAMBDA"
    return_shrinkage_alpha  = "RETURN_SHRINKAGE_ALPHA"
    risk_free_rate_annual   = "RISK_FREE_RATE_ANNUAL"
    max_forward_fill_days   = "MAX_FORWARD_FILL_DAYS"
    window_days             = "WINDOW_DAYS"
    ingest_lookback_days    = "INGEST_LOOKBACK_DAYS"
    rebalance_l1_threshold  = "REBALANCE_L1_THRESHOLD"
    validation_gammas       = "VALIDATION_GAMMAS"
    validation_lambdas      = "VALIDATION_LAMBDAS"
    validation_alphas       = "VALIDATION_ALPHAS"
    validation_windows      = "VALIDATION_WINDOWS"
    validation_folds        = "VALIDATION_FOLDS"
  }

  strategy_environment = {
    for field, env_name in local.strategy_env_names : env_name => var.strategy[field]
    if contains(keys(var.strategy), field)
  }

  permissions_policy = {
    SilverRead = {
      Sid      = "SilverRead"
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:ListBucket"]
      Resource = [var.silver_bucket_arn, "${var.silver_bucket_arn}/*"]
    }
    GoldReadWrite = {
      Sid      = "GoldReadWrite"
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
      Resource = [var.gold_bucket_arn, "${var.gold_bucket_arn}/*"]
    }
  }

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
  description = "Pinned ECR tag or digest for the backtest image. NEVER 'latest' (AGENTS.md section 11.2)."

  validation {
    condition     = var.image_tag != "" && var.image_tag != "latest"
    error_message = "image_tag must be a pinned tag or digest; 'latest' is forbidden."
  }
}

variable "code_version" {
  type        = string
  description = "Git SHA recorded in logs and written artifacts."
}

variable "strategy" {
  type        = map(string)
  description = "Values from config/strategy.yaml rendered as strings."

  validation {
    # Null-tolerant: terraform validate evaluates the rule with no value
    # assigned; an unset required variable still fails at plan/apply with
    # the standard "required variable not set" error. Key sets are compared
    # sorted (keys() does not guarantee order across object literals).
    condition = var.strategy == null || (sort(keys(var.strategy)) == sort(keys(local.strategy_env_names)) && alltrue([
      for value in values(var.strategy) : trimspace(value) != ""
    ]))
    error_message = "strategy must map exactly the 13 strategy.yaml fields (risk_aversion, transaction_cost_lambda, return_shrinkage_alpha, risk_free_rate_annual, max_forward_fill_days, window_days, ingest_lookback_days, rebalance_l1_threshold, validation_gammas, validation_lambdas, validation_alphas, validation_windows, validation_folds) to non-empty strings; empty values usually mean the YAML has keys without values."
  }
}

variable "silver_bucket_name" {
  type        = string
  description = "Silver bucket name."
}

variable "silver_bucket_arn" {
  type        = string
  description = "Silver bucket ARN."
}

variable "gold_bucket_name" {
  type        = string
  description = "Gold bucket name."
}

variable "gold_bucket_arn" {
  type        = string
  description = "Gold bucket ARN."
}

variable "dlq_arn" {
  type        = string
  description = "Backtest dead-letter queue ARN."
}

# --- ECR repository ------------------------------------------------------------

resource "aws_ecr_repository" "backtest" {
  name                 = "${var.project}-backtest-${var.env}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.tags
}

resource "aws_ecr_lifecycle_policy" "backtest" {
  repository = aws_ecr_repository.backtest.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 14 days"
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

# --- Lambda execution role (least privilege) -----------------------------------

resource "aws_iam_role" "backtest" {
  name = "${var.project}-${var.env}-backtest-lambda"
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

data "aws_iam_policy_document" "logs" {
  statement {
    sid    = "WriteFunctionLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.backtest.arn}:*"]
  }

  statement {
    sid       = "CreateNamedLogGroup"
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup"]
    resources = ["arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project}-backtest-${var.env}"]
  }
}

resource "aws_iam_role_policy" "logs" {
  name   = "logs"
  role   = aws_iam_role.backtest.id
  policy = data.aws_iam_policy_document.logs.json
}

resource "aws_iam_role_policy" "permissions" {
  name   = "permissions"
  role   = aws_iam_role.backtest.id
  policy = jsonencode(local.permissions_policy)
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# --- Lambda function (container image) -----------------------------------------

resource "aws_cloudwatch_log_group" "backtest" {
  name              = "/aws/lambda/${var.project}-backtest-${var.env}"
  retention_in_days = local.log_retention_days

  tags = local.tags
}

resource "aws_lambda_function" "backtest" {
  function_name = "${var.project}-backtest-${var.env}"
  role          = aws_iam_role.backtest.arn
  description   = "Weekly nested temporal validation with the gold-layer report"

  package_type  = "Image"
  architectures = ["x86_64"]
  image_uri     = "${aws_ecr_repository.backtest.repository_url}:${var.image_tag}"

  image_config {
    command = ["backtest.handler.handler"]
  }

  # The validation grid runs one backtest per candidate over the full history;
  # 15 minutes is the Lambda ceiling and 2 GB keeps the matrix work in memory.
  memory_size = 2048
  timeout     = 900

  environment {
    variables = merge(
      {
        CODE_VERSION  = var.code_version
        CONFIG_DIR    = "/var/task/config"
        SILVER_BUCKET = var.silver_bucket_name
        GOLD_BUCKET   = var.gold_bucket_name
      },
      local.strategy_environment,
    )
  }

  dead_letter_config {
    target_arn = var.dlq_arn
  }

  depends_on = [
    aws_cloudwatch_log_group.backtest,
    aws_iam_role_policy.logs,
    aws_iam_role_policy.permissions,
  ]

  tags = local.tags
}

# Asynchronous invocation: no built-in retries beyond the DLQ hand-off.
resource "aws_lambda_function_event_invoke_config" "backtest" {
  function_name = aws_lambda_function.backtest.function_name

  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 3600
}

# --- Weekly EventBridge Scheduler ----------------------------------------------

resource "aws_iam_role" "scheduler" {
  name = "${var.project}-${var.env}-backtest-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "sts:AssumeRole"
        Principal = {
          Service = "scheduler.amazonaws.com"
        }
      },
    ]
  })

  tags = local.tags
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    sid       = "InvokeBacktestLambda"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.backtest.arn]
  }

  statement {
    sid       = "SendScheduleDeadLetters"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [var.dlq_arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "schedule"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

resource "aws_scheduler_schedule" "weekly" {
  name        = "${var.project}-backtest-weekly-${var.env}"
  description = "Weekly backtest on Saturday morning, after the Friday close"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = local.schedule_expression
  schedule_expression_timezone = local.schedule_timezone

  target {
    arn      = aws_lambda_function.backtest.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({})

    dead_letter_config {
      arn = var.dlq_arn
    }

    retry_policy {
      maximum_retry_attempts       = 0
      maximum_event_age_in_seconds = 3600
    }
  }
}

# --- Outputs --------------------------------------------------------------------

output "function_arn" {
  description = "Weekly backtest lambda ARN."
  value       = aws_lambda_function.backtest.arn
}

output "function_name" {
  description = "Weekly backtest lambda name."
  value       = aws_lambda_function.backtest.function_name
}

output "ecr_repository_uri" {
  description = "ECR repository URI for the backtest image."
  value       = aws_ecr_repository.backtest.repository_url
}

output "schedule_arn" {
  description = "Weekly backtest schedule ARN."
  value       = aws_scheduler_schedule.weekly.arn
}
