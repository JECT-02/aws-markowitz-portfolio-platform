# Daily slow loop (module `pipeline`): ingest -> clean -> optimize -> notify,
# orchestrated by Step Functions and triggered by an EventBridge Scheduler.
#
# Packaging deviation, documented in the branch PR: ingest, clean and
# optimizer all carry pandas/pyarrow requirements, so all three are deployed
# as container images in ECR with a pinned tag (AGENTS.md section 11.2 makes
# archive_file mandatory only for lightweight lambdas; these are not).

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
  schedule_expression = "cron(30 16 ? * MON-FRI *)"
  schedule_timezone   = "America/New_York"
  log_retention_days  = 14

  transient_lambda_errors = [
    "Lambda.ServiceException",
    "Lambda.AWSLambdaException",
    "Lambda.SdkClientException",
    "Lambda.TooManyRequestsException",
  ]

  # Mirrors infra/config.py STRATEGY_ENVIRONMENT: config/strategy.yaml fields
  # -> lambda environment variable names. Values come from `var.strategy`.
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

  lambda_configs = {
    ingest = {
      description = "Daily market data ingestion"
      cmd         = ["ingest.handler.handler"]
      timeout     = 300
      memory      = 1024
    }
    clean = {
      description = "Silver returns cleaning"
      cmd         = ["clean.handler.handler"]
      timeout     = 300
      memory      = 1024
    }
    optimizer = {
      description = "Markowitz optimizer"
      cmd         = ["optimizer.handler.handler"]
      timeout     = 600
      memory      = 2048
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
  description = "Pinned ECR tag or digest for the three function images. NEVER 'latest' (AGENTS.md section 11.2)."

  validation {
    condition     = var.image_tag != "" && var.image_tag != "latest"
    error_message = "image_tag must be a pinned tag or digest; 'latest' is forbidden."
  }
}

variable "code_version" {
  type        = string
  description = "Git SHA recorded in logs and written artifacts."
}

variable "alpaca_api_key_parameter" {
  type        = string
  description = "SSM SecureString path holding the Alpaca API key id."
}

variable "alpaca_secret_key_parameter" {
  type        = string
  description = "SSM SecureString path holding the Alpaca secret key."
}

variable "strategy" {
  type        = map(string)
  description = "Values from config/strategy.yaml rendered as strings."
}

variable "bronze_bucket_name" {
  type        = string
  description = "Bronze bucket name."
}

variable "bronze_bucket_arn" {
  type        = string
  description = "Bronze bucket ARN."
}

variable "quarantine_bucket_name" {
  type        = string
  description = "Quarantine bucket name."
}

variable "quarantine_bucket_arn" {
  type        = string
  description = "Quarantine bucket ARN."
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

variable "state_table_name" {
  type        = string
  description = "PortfolioState DynamoDB table name."
}

variable "state_table_arn" {
  type        = string
  description = "PortfolioState DynamoDB table ARN."
}

variable "notifications_topic_arn" {
  type        = string
  description = "SNS notifications topic ARN."
}

variable "dlq_arns" {
  type        = map(string)
  description = "Dead-letter queue ARNs keyed by pipeline stage (ingest/clean/optimizer/schedule)."
}

# --- ECR repositories ---------------------------------------------------------

resource "aws_ecr_repository" "functions" {
  for_each = local.lambda_configs

  name                 = "${var.project}-${each.key}-${var.env}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.tags
}

resource "aws_ecr_lifecycle_policy" "functions" {
  for_each = aws_ecr_repository.functions

  repository = each.value.name
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

# --- Lambda execution roles (one per function, least privilege) ----------------

locals {
  lambda_assume_policy = jsonencode({
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

  permission_policies = {
    ingest = {
      Statement = [
        {
          Sid    = "BronzeReadWrite"
          Effect = "Allow"
          Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
          Resource = [
            var.bronze_bucket_arn,
            "${var.bronze_bucket_arn}/*",
          ]
        },
        {
          Sid    = "QuarantineWrite"
          Effect = "Allow"
          Action = ["s3:PutObject", "s3:ListBucket"]
          Resource = [
            var.quarantine_bucket_arn,
            "${var.quarantine_bucket_arn}/*",
          ]
        },
        {
          Sid    = "ReadAlpacaCredentials"
          Effect = "Allow"
          Action = ["ssm:GetParameter"]
          Resource = [
            "arn:${data.aws_partition.current.partition}:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter${var.alpaca_api_key_parameter}",
            "arn:${data.aws_partition.current.partition}:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter${var.alpaca_secret_key_parameter}",
          ]
        },
      ]
    }
    clean = {
      Statement = [
        {
          Sid    = "BronzeRead"
          Effect = "Allow"
          Action = ["s3:GetObject", "s3:ListBucket"]
          Resource = [
            var.bronze_bucket_arn,
            "${var.bronze_bucket_arn}/*",
          ]
        },
        {
          Sid    = "SilverReadWrite"
          Effect = "Allow"
          Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
          Resource = [
            var.silver_bucket_arn,
            "${var.silver_bucket_arn}/*",
          ]
        },
      ]
    }
    optimizer = {
      Statement = [
        {
          Sid    = "BronzeRead"
          Effect = "Allow"
          Action = ["s3:GetObject", "s3:ListBucket"]
          Resource = [
            var.bronze_bucket_arn,
            "${var.bronze_bucket_arn}/*",
          ]
        },
        {
          Sid    = "SilverRead"
          Effect = "Allow"
          Action = ["s3:GetObject", "s3:ListBucket"]
          Resource = [
            var.silver_bucket_arn,
            "${var.silver_bucket_arn}/*",
          ]
        },
        {
          Sid    = "GoldReadWrite"
          Effect = "Allow"
          Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
          Resource = [
            var.gold_bucket_arn,
            "${var.gold_bucket_arn}/*",
          ]
        },
        {
          Sid    = "StateTableReadWrite"
          Effect = "Allow"
          Action = [
            "dynamodb:GetItem",
            "dynamodb:PutItem",
            "dynamodb:UpdateItem",
            "dynamodb:Query",
            "dynamodb:ConditionCheckItem",
          ]
          Resource = [var.state_table_arn]
        },
      ]
    }
  }
}

resource "aws_iam_role" "lambda" {
  for_each = local.lambda_configs

  name               = "${var.project}-${var.env}-${each.key}-lambda"
  assume_role_policy = local.lambda_assume_policy

  tags = local.tags
}

data "aws_iam_policy_document" "lambda_logs" {
  for_each = local.lambda_configs

  statement {
    sid    = "WriteFunctionLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.lambda[each.key].arn}:*"]
  }

  statement {
    sid       = "CreateNamedLogGroup"
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup"]
    resources = ["arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project}-${each.key}-${var.env}"]
  }
}

resource "aws_iam_role_policy" "lambda_logs" {
  for_each = local.lambda_configs

  name   = "logs"
  role   = aws_iam_role.lambda[each.key].id
  policy = data.aws_iam_policy_document.lambda_logs[each.key].json
}

resource "aws_iam_role_policy" "lambda_permissions" {
  for_each = local.lambda_configs

  name   = "permissions"
  role   = aws_iam_role.lambda[each.key].id
  policy = jsonencode(local.permission_policies[each.key])
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# --- Lambda functions (container images) --------------------------------------

resource "aws_cloudwatch_log_group" "lambda" {
  for_each = local.lambda_configs

  name              = "/aws/lambda/${var.project}-${each.key}-${var.env}"
  retention_in_days = local.log_retention_days

  tags = local.tags
}

resource "aws_lambda_function" "pipeline" {
  for_each = local.lambda_configs

  function_name = "${var.project}-${each.key}-${var.env}"
  role          = aws_iam_role.lambda[each.key].arn
  description   = each.value.description

  package_type  = "Image"
  architectures = ["x86_64"]
  image_uri     = "${aws_ecr_repository.functions[each.key].repository_url}:${var.image_tag}"

  image_config {
    command = each.value.cmd
  }

  memory_size = each.value.memory
  timeout     = each.value.timeout

  environment {
    variables = merge(
      {
        CODE_VERSION = var.code_version
        CONFIG_DIR   = "/var/task/config"
      },
      each.key == "ingest" ? {
        BRONZE_BUCKET           = var.bronze_bucket_name
        QUARANTINE_BUCKET       = var.quarantine_bucket_name
        ALPACA_API_KEY_PARAM    = var.alpaca_api_key_parameter
        ALPACA_SECRET_KEY_PARAM = var.alpaca_secret_key_parameter
      } : {},
      each.key == "clean" ? {
        BRONZE_BUCKET = var.bronze_bucket_name
        SILVER_BUCKET = var.silver_bucket_name
      } : {},
      each.key == "optimizer" ? {
        BRONZE_BUCKET = var.bronze_bucket_name
        SILVER_BUCKET = var.silver_bucket_name
        GOLD_BUCKET   = var.gold_bucket_name
        STATE_TABLE   = var.state_table_name
      } : {},
      local.strategy_environment,
    )
  }

  dead_letter_config {
    target_arn = var.dlq_arns[each.key]
  }

  depends_on = [
    aws_cloudwatch_log_group.lambda,
    aws_iam_role_policy.lambda_logs,
    aws_iam_role_policy.lambda_permissions,
  ]

  tags = local.tags
}

# Asynchronous invocation: no built-in retries beyond the DLQ hand-off.
resource "aws_lambda_function_event_invoke_config" "pipeline" {
  for_each = aws_lambda_function.pipeline

  function_name = each.value.function_name

  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 3600
}
