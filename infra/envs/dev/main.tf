locals {
  # config/strategy.yaml rendered to the lambda environment variables the
  # pipeline module expects (same translation as infra/config.py: scalars
  # become strings, lists join with commas).
  strategy_values = {
    for field, value in yamldecode(file("${path.module}/../../../config/strategy.yaml")) : field => can(tostring(value)) ? tostring(value) : join(",", [for item in value : tostring(item)])
  }
}

module "data" {
  source = "../../modules/data"

  project = "portfolio-markowitz-aws"
  env     = var.env
}

module "pipeline" {
  source = "../../modules/pipeline"

  project      = "portfolio-markowitz-aws"
  env          = var.env
  image_tag    = var.optimizer_image_tag
  code_version = var.code_version

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

module "api" {
  source = "../../modules/api"

  project      = "portfolio-markowitz-aws"
  env          = var.env
  image_tag    = var.optimizer_image_tag
  code_version = var.code_version

  spa_allowed_origin = var.spa_allowed_origin

  bronze_bucket_name     = module.data.bronze_bucket_name
  quarantine_bucket_name = module.data.quarantine_bucket_name
  silver_bucket_name     = module.data.silver_bucket_name
  gold_bucket_name       = module.data.gold_bucket_name
  gold_bucket_arn        = module.data.gold_bucket_arn

  state_table_name = module.data.portfolio_state_table_name
  state_table_arn  = module.data.portfolio_state_table_arn
}

module "web" {
  source = "../../modules/web"

  project        = "portfolio-markowitz-aws"
  env            = var.env
  api_endpoint   = module.api.api_endpoint
  cognito_domain = "https://${module.api.cognito_domain}"
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

variable "optimizer_image_tag" {
  type        = string
  description = "Pinned ECR tag/digest for the ingest/clean/optimizer images (never 'latest')."
  default     = ""
  validation {
    condition     = var.optimizer_image_tag != "" && var.optimizer_image_tag != "latest"
    error_message = "Set OPTIMIZER_IMAGE_TAG (a pushed tag or digest); 'latest' is forbidden by AGENTS.md."
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

output "pipeline_state_machine_arn" {
  value = module.pipeline.state_machine_arn
}

output "pipeline_function_arns" {
  value = module.pipeline.function_arns
}

output "pipeline_ecr_repository_uris" {
  value = module.pipeline.ecr_repository_uris
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

output "cognito_domain" {
  value = module.api.cognito_domain
}

output "reader_function_name" {
  value = module.api.reader_function_name
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
