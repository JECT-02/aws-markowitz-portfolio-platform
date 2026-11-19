locals {
  # config/strategy.yaml rendered to the lambda environment variables the
  # pipeline module expects (same translation as infra/config.py: scalars
  # become strings, lists join with commas). Null values render as empty
  # strings so the module variable validation fails with a clear message
  # instead of a cryptic tostring(null) crash.
  strategy_raw = yamldecode(file("${path.module}/../../../config/strategy.yaml"))
  strategy_values = {
    for field, value in local.strategy_raw :
    field => value == null ? "" : (can(tostring(value)) ? tostring(value) : join(",", [for item in value : tostring(item)]))
  }

  # Backtest history guard (mistakes.md G-10): the weekly backtest needs
  # 0.8 * n_observations >= 2 * longest validation window, and the daily
  # ingest accumulates one trading day per run. 252/365 converts the
  # calendar lookback to trading days; +30 keeps a safety margin.
  ingest_lookback_days      = parseint(local.strategy_values["ingest_lookback_days"], 10)
  longest_validation_window = max([for raw in split(",", local.strategy_values["validation_windows"]) : parseint(trimspace(raw), 10)]...)
  required_trading_days     = ceil(local.longest_validation_window * 2 / 0.8) + 30
  expected_trading_days     = floor(local.ingest_lookback_days * 252 / 365)
  lakehouse_expiration_days = local.ingest_lookback_days + 100
}

resource "terraform_data" "backtest_history_guard" {
  lifecycle {
    precondition {
      condition     = local.expected_trading_days >= local.required_trading_days
      error_message = "ingest_lookback_days is too short for temporal validation: the backtest needs 0.8 * observations >= 2 * the longest validation window (plus a 30-day margin), and the silver lakehouse only accumulates one trading day per run."
    }
    precondition {
      condition     = local.lakehouse_expiration_days >= local.ingest_lookback_days + 30
      error_message = "silver/gold expiration must outlive the ingest lookback window, or the backtest history is deleted before the backtest can read it."
    }
  }

  input = {
    ingest_lookback_days      = local.ingest_lookback_days
    expected_trading_days     = local.expected_trading_days
    required_trading_days     = local.required_trading_days
    lakehouse_expiration_days = local.lakehouse_expiration_days
  }
}

module "data" {
  source = "../../modules/data"

  project = "portfolio-markowitz-aws"
  env     = var.env

  lakehouse_expiration_days = local.lakehouse_expiration_days

  alpaca_api_key_parameter    = var.alpaca_api_key_parameter
  alpaca_secret_key_parameter = var.alpaca_secret_key_parameter
}

module "pipeline" {
  source = "../../modules/pipeline"

  project       = "portfolio-markowitz-aws"
  env           = var.env
  image_digests = var.image_digests
  code_version  = var.code_version

  strategy = local.strategy_values

  alpaca_api_key_parameter    = var.alpaca_api_key_parameter
  alpaca_secret_key_parameter = var.alpaca_secret_key_parameter

  bronze_bucket_name     = module.data.bronze_bucket_name
  bronze_bucket_arn      = module.data.bronze_bucket_arn
  quarantine_bucket_name = module.data.quarantine_bucket_name
  quarantine_bucket_arn  = module.data.quarantine_bucket_arn
  silver_bucket_name     = module.data.silver_bucket_name
  silver_bucket_arn      = module.data.silver_bucket_arn
  gold_bucket_name       = module.data.gold_bucket_name
  gold_bucket_arn        = module.data.gold_bucket_arn

  state_table_name = module.data.portfolio_state_table_name
  state_table_arn  = module.data.portfolio_state_table_arn

  notifications_topic_arn = module.data.notifications_topic_arn
  dlq_arns                = module.data.dlq_arns
}

module "backtest" {
  source = "../../modules/backtest"

  project       = "portfolio-markowitz-aws"
  env           = var.env
  image_digests = var.image_digests
  code_version  = var.code_version

  strategy = local.strategy_values

  silver_bucket_name = module.data.silver_bucket_name
  silver_bucket_arn  = module.data.silver_bucket_arn
  gold_bucket_name   = module.data.gold_bucket_name
  gold_bucket_arn    = module.data.gold_bucket_arn

  dlq_arn = module.data.dlq_arns["backtest"]
}

module "api" {
  source = "../../modules/api"

  project       = "portfolio-markowitz-aws"
  env           = var.env
  image_digests = var.image_digests
  code_version  = var.code_version

  spa_allowed_origin = var.spa_allowed_origin

  looker_connector_callback_urls = var.looker_connector_callback_urls

  bronze_bucket_name     = module.data.bronze_bucket_name
  quarantine_bucket_name = module.data.quarantine_bucket_name
  silver_bucket_name     = module.data.silver_bucket_name
  gold_bucket_name       = module.data.gold_bucket_name
  gold_bucket_arn        = module.data.gold_bucket_arn

  state_table_name = module.data.portfolio_state_table_name
  state_table_arn  = module.data.portfolio_state_table_arn

  alpaca_api_key_parameter    = var.alpaca_api_key_parameter
  alpaca_secret_key_parameter = var.alpaca_secret_key_parameter

  notifications_topic_arn = module.data.notifications_topic_arn
}

module "web" {
  source = "../../modules/web"

  project        = "portfolio-markowitz-aws"
  env            = var.env
  api_endpoint   = module.api.api_endpoint
  cognito_domain = module.api.cognito_hosted_ui_url
  client_id      = module.api.web_client_id
}

variable "env" {
  type        = string
  description = "Deployment environment name."
  default     = "dev"
}

variable "aws_region" {
  type        = string
  description = "AWS region for all dev resources."
  default     = "us-east-1"
}

variable "image_digests" {
  type        = map(string)
  description = "Immutable ECR digest (sha256:<64 hex>) per image, keyed ingest/clean/optimizer/monitor/backtest/reader/executor. Export it as TF_VAR_image_digests before plan/apply: a digest identifies a manifest inside one repository, so no digest can be shared."
  default     = {}

  validation {
    condition = alltrue([
      for digest in values(var.image_digests) : can(regex("^sha256:[0-9a-f]{64}$", digest))
    ])
    error_message = "Every image_digests value must be an immutable sha256 digest (sha256:<64 hex>); mutable tags and 'latest' are forbidden by AGENTS.md 11.2."
  }
}

variable "code_version" {
  type        = string
  description = "Git SHA recorded in logs and artifacts."
  default     = "unknown"
}

variable "alpaca_api_key_parameter" {
  type        = string
  description = "SSM SecureString path holding the Alpaca API key id."
  default     = "/portfolio-markowitz/dev/alpaca/api-key"
}

variable "alpaca_secret_key_parameter" {
  type        = string
  description = "SSM SecureString path holding the Alpaca secret key."
  default     = "/portfolio-markowitz/dev/alpaca/secret-key"
}

variable "spa_allowed_origin" {
  type        = string
  description = "HTTPS origin of the SPA (configures OAuth/CORS); empty disables them."
  default     = ""
}

variable "looker_connector_callback_urls" {
  type        = list(string)
  description = "Apps Script /usercallback URLs of the deployed Looker Studio connector; empty keeps its OAuth flows disabled."
  default     = []
}

output "bronze_bucket_name" {
  value = module.data.bronze_bucket_name
}

output "quarantine_bucket_name" {
  value = module.data.quarantine_bucket_name
}

output "silver_bucket_name" {
  value = module.data.silver_bucket_name
}

output "gold_bucket_name" {
  value = module.data.gold_bucket_name
}

output "portfolio_state_table_name" {
  value = module.data.portfolio_state_table_name
}

output "portfolio_state_table_arn" {
  value = module.data.portfolio_state_table_arn
}

output "notifications_topic_arn" {
  value = module.data.notifications_topic_arn
}

output "dlq_arns" {
  value = module.data.dlq_arns
}

output "alpaca_api_key_parameter_name" {
  value = module.data.alpaca_api_key_parameter_name
}

output "alpaca_secret_key_parameter_name" {
  value = module.data.alpaca_secret_key_parameter_name
}

output "pipeline_state_machine_arn" {
  value = module.pipeline.state_machine_arn
}

output "pipeline_function_arns" {
  value = module.pipeline.function_arns
}

output "pipeline_ecr_repository_uris" {
  value = module.pipeline.ecr_repository_uris
}

output "backtest_function_arn" {
  value = module.backtest.function_arn
}

output "backtest_ecr_repository_uri" {
  value = module.backtest.ecr_repository_uri
}

output "api_endpoint" {
  value = module.api.api_endpoint
}

output "user_pool_id" {
  value = module.api.user_pool_id
}

output "web_client_id" {
  value = module.api.web_client_id
}

output "cognito_hosted_ui_url" {
  value = module.api.cognito_hosted_ui_url
}

output "looker_connector_client_id" {
  value = module.api.looker_connector_client_id
}

output "looker_connector_client_secret" {
  value     = module.api.looker_connector_client_secret
  sensitive = true
}

output "reader_function_name" {
  value = module.api.reader_function_name
}

output "executor_function_name" {
  value = module.api.executor_function_name
}

output "executor_ecr_repository_uri" {
  value = module.api.executor_ecr_repository_uri
}

output "spa_url" {
  value = module.web.spa_url
}

output "spa_bucket_name" {
  value = module.web.spa_bucket_name
}

output "web_distribution_id" {
  value = module.web.distribution_id
}
