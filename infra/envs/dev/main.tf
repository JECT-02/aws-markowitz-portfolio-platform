module "data" {
  source = "../../modules/data"

  project = "portfolio-markowitz-aws"
  env     = var.env
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
