# Persistent storage, notifications and dead-letter queues (module `data`).
# Mirrors the CDK DataStack: four lakehouse buckets, the PortfolioState
# DynamoDB table, the SNS topic and one DLQ per Lambda + scheduler.

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
  bucket_configs = {
    bronze = {
      versioned       = true
      expiration_days = null
      description     = "raw JSON responses (immutable source of truth)"
    }
    quarantine = {
      versioned       = false
      expiration_days = 90
      description     = "rejected batches with a reason"
    }
    silver = {
      versioned       = false
      expiration_days = 400
      description     = "clean log returns (Parquet+Snappy)"
    }
    gold = {
      versioned       = false
      expiration_days = 400
      description     = "served artifacts (w*, metrics, latest.json)"
    }
  }

  dlq_names = ["ingest", "clean", "optimizer", "schedule"]
}

variable "project" {
  type        = string
  description = "Project identifier used for resource names and tags."
}

variable "env" {
  type        = string
  description = "Deployment environment (dev or prod)."
}

variable "notification_email" {
  type        = string
  description = "Email subscribed to the SNS notifications topic; empty disables the subscription."
  default     = ""
}

variable "sqs_retention_days" {
  type        = number
  description = "Message retention of the dead-letter queues."
  default     = 4
}

variable "dynamodb_read_capacity" {
  type        = number
  description = "Provisioned read capacity for PortfolioState (never on-demand, AGENTS.md section 11.2)."
  default     = 1
}

variable "dynamodb_write_capacity" {
  type        = number
  description = "Provisioned write capacity for PortfolioState."
  default     = 1
}

variable "alpaca_api_key_parameter" {
  type        = string
  description = "SSM SecureString path holding the Alpaca API key id (placeholder created here, value set by the human)."
}

variable "alpaca_secret_key_parameter" {
  type        = string
  description = "SSM SecureString path holding the Alpaca secret key (placeholder created here, value set by the human)."
}

# --- Lakehouse buckets ------------------------------------------------------

resource "aws_s3_bucket" "lakehouse" {
  for_each = local.bucket_configs

  bucket = "${var.project}-${var.env}-lakehouse-${each.key}"

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    project     = var.project
    env         = var.env
    owner       = "JECT"
    cost-center = "personal-portfolio"
    layer       = each.key
    description = each.value.description
  }
}

resource "aws_s3_bucket_versioning" "lakehouse" {
  for_each = { for name, cfg in local.bucket_configs : name => cfg if cfg.versioned }

  bucket = aws_s3_bucket.lakehouse[each.key].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lakehouse" {
  for_each = aws_s3_bucket.lakehouse

  # trivy:ignore:AVD-AWS-0132 SSE-S3 provides at-rest encryption; a
  # customer-managed KMS key costs ~$1/month per key against the $1-$5
  # monthly budget of AGENTS.md with no benefit for these private buckets.
  bucket = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "lakehouse" {
  for_each = aws_s3_bucket.lakehouse

  bucket = each.value.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "enforce_tls" {
  for_each = aws_s3_bucket.lakehouse

  bucket = each.value.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          each.value.arn,
          "${each.value.arn}/*",
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
    ]
  })
}

resource "aws_s3_bucket_lifecycle_configuration" "lakehouse" {
  for_each = { for name, cfg in local.bucket_configs : name => cfg if cfg.expiration_days != null }

  bucket = aws_s3_bucket.lakehouse[each.key].id

  rule {
    id     = "expire-old-objects"
    status = "Enabled"

    filter {}

    expiration {
      days = each.value.expiration_days
    }
  }

  depends_on = [aws_s3_bucket_versioning.lakehouse]
}

resource "aws_s3_bucket_lifecycle_configuration" "bronze_versions" {
  bucket = aws_s3_bucket.lakehouse["bronze"].id

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }

  depends_on = [aws_s3_bucket_versioning.lakehouse]
}

# --- DynamoDB PortfolioState --------------------------------------------------

resource "aws_dynamodb_table" "portfolio_state" {
  name           = "${var.project}-${var.env}-portfolio-state"
  billing_mode   = "PROVISIONED"
  read_capacity  = var.dynamodb_read_capacity
  write_capacity = var.dynamodb_write_capacity
  hash_key       = "pk"
  range_key      = "sk"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    project = var.project
    env     = var.env
    owner   = "JECT"
  }

  lifecycle {
    prevent_destroy = true
  }
}

# --- Notifications -----------------------------------------------------------

resource "aws_sns_topic" "notifications" {
  # AWS-managed KMS key: encrypted at rest at no extra cost (a customer
  # managed key would add ~$1/month against the AGENTS.md budget).
  name              = "${var.project}-${var.env}-notifications"
  kms_master_key_id = "alias/aws/sns"
  tags              = { project = var.project, env = var.env, owner = "JECT" }
}

resource "aws_sns_topic_policy" "enforce_tls" {
  arn = aws_sns_topic.notifications.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "sns:*"
        Resource  = aws_sns_topic.notifications.arn
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
    ]
  })
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.notification_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.notifications.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

# --- Dead-letter queues ------------------------------------------------------

resource "aws_sqs_queue" "dlq" {
  for_each = toset(local.dlq_names)

  name                      = "${var.project}-${var.env}-${each.key}-dlq"
  message_retention_seconds = var.sqs_retention_days * 86400
  sqs_managed_sse_enabled   = true

  tags = {
    project = var.project
    env     = var.env
    owner   = "JECT"
  }
}

resource "aws_sqs_queue_policy" "enforce_tls" {
  for_each = aws_sqs_queue.dlq

  queue_url = each.value.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "sqs:*"
        Resource  = each.value.arn
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
    ]
  })
}

# --- Outputs ------------------------------------------------------------------

output "bronze_bucket_name" {
  description = "Bronze bucket (raw JSON)."
  value       = aws_s3_bucket.lakehouse["bronze"].id
}

output "bronze_bucket_arn" {
  description = "Bronze bucket ARN."
  value       = aws_s3_bucket.lakehouse["bronze"].arn
}

output "quarantine_bucket_name" {
  description = "Quarantine bucket (rejected batches)."
  value       = aws_s3_bucket.lakehouse["quarantine"].id
}

output "quarantine_bucket_arn" {
  description = "Quarantine bucket ARN."
  value       = aws_s3_bucket.lakehouse["quarantine"].arn
}

output "silver_bucket_name" {
  description = "Silver bucket (clean returns Parquet)."
  value       = aws_s3_bucket.lakehouse["silver"].id
}

output "silver_bucket_arn" {
  description = "Silver bucket ARN."
  value       = aws_s3_bucket.lakehouse["silver"].arn
}

output "gold_bucket_name" {
  description = "Gold bucket (served artifacts)."
  value       = aws_s3_bucket.lakehouse["gold"].id
}

output "gold_bucket_arn" {
  description = "Gold bucket ARN."
  value       = aws_s3_bucket.lakehouse["gold"].arn
}

output "portfolio_state_table_name" {
  description = "PortfolioState DynamoDB table."
  value       = aws_dynamodb_table.portfolio_state.name
}

output "portfolio_state_table_arn" {
  description = "PortfolioState DynamoDB table ARN."
  value       = aws_dynamodb_table.portfolio_state.arn
}

output "notifications_topic_arn" {
  description = "SNS notifications topic ARN."
  value       = aws_sns_topic.notifications.arn
}

output "dlq_arns" {
  description = "Dead-letter queue ARNs keyed by lambda name."
  value       = { for name, queue in aws_sqs_queue.dlq : name => queue.arn }
}

output "alpaca_api_key_parameter_name" {
  description = "SSM SecureString holding the Alpaca API key id."
  value       = aws_ssm_parameter.alpaca_api_key.name
}

output "alpaca_secret_key_parameter_name" {
  description = "SSM SecureString holding the Alpaca secret key."
  value       = aws_ssm_parameter.alpaca_secret_key.name
}
