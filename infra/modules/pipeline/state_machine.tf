# Step Functions state machine and the daily EventBridge Scheduler.

locals {
  definition = jsonencode({
    Comment        = "Daily Markowitz pipeline: ingest, clean, optimize, notify"
    TimeoutSeconds = 900
    StartAt        = "Ingest"
    States = {
      Ingest = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.pipeline["ingest"].arn
          Payload = {
            source = "alpaca"
          }
        }
        Retry = [
          {
            ErrorEquals     = local.transient_lambda_errors
            IntervalSeconds = 2
            MaxAttempts     = 3
            BackoffRate     = 2.0
          }
        ]
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            Next        = "NotifyFailure"
            ResultPath  = "$.error"
          }
        ]
        Next = "CheckMarketCalendar"
      }
      CheckMarketCalendar = {
        Type    = "Choice"
        Default = "Clean"
        Choices = [
          {
            Variable     = "$.status"
            StringEquals = "SKIPPED"
            Next         = "MarketClosed"
          }
        ]
      }
      MarketClosed = {
        Type = "Succeed"
      }
      Clean = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.pipeline["clean"].arn
          Payload = {
            "run_id.$"   = "$.run_id"
            "run_date.$" = "$.run_date"
            "source.$"   = "$.source"
            "accepted.$" = "$.accepted"
          }
        }
        Retry = [
          {
            ErrorEquals     = local.transient_lambda_errors
            IntervalSeconds = 2
            MaxAttempts     = 3
            BackoffRate     = 2.0
          }
        ]
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            Next        = "NotifyFailure"
            ResultPath  = "$.error"
          }
        ]
        Next = "Optimize"
      }
      Optimize = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.pipeline["optimizer"].arn
          Payload = {
            "run_id.$"   = "$.run_id"
            "run_date.$" = "$.run_date"
          }
        }
        Retry = [
          {
            ErrorEquals     = local.transient_lambda_errors
            IntervalSeconds = 2
            MaxAttempts     = 3
            BackoffRate     = 2.0
          }
        ]
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            Next        = "NotifyFailure"
            ResultPath  = "$.error"
          }
        ]
        Next = "NotifySuccess"
      }
      NotifySuccess = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::sns:publish"
        Parameters = {
          TopicArn    = var.notifications_topic_arn
          "Message.$" = "$"
          Subject     = "Portfolio proposal ready"
        }
        Next = "PipelineSucceeded"
      }
      PipelineSucceeded = {
        Type = "Succeed"
      }
      NotifyFailure = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::sns:publish"
        Parameters = {
          TopicArn    = var.notifications_topic_arn
          "Message.$" = "$"
          Subject     = "Portfolio pipeline failure"
        }
        Next = "PipelineFailed"
      }
      PipelineFailed = {
        Type  = "Fail"
        Error = "PipelineFailure"
        Cause = "A pipeline stage failed"
      }
    }
  })
}

data "aws_partition" "current" {}

# Step Functions execution role.
resource "aws_iam_role" "sfn" {
  name = "${var.project}-${var.env}-pipeline-sfn"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "sts:AssumeRole"
        Principal = {
          Service = "states.amazonaws.com"
        }
      },
    ]
  })

  tags = local.tags
}

data "aws_iam_policy_document" "sfn" {
  statement {
    sid       = "InvokePipelineLambdas"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [for _, fn in aws_lambda_function.pipeline : fn.arn]
  }

  statement {
    sid       = "PublishNotifications"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [var.notifications_topic_arn]
  }
}

resource "aws_iam_role_policy" "sfn" {
  name   = "pipeline"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}

resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/states/${var.project}-daily-${var.env}"
  retention_in_days = local.log_retention_days

  tags = local.tags
}

resource "aws_sfn_state_machine" "daily_pipeline" {
  name       = "${var.project}-daily-${var.env}"
  role_arn   = aws_iam_role.sfn.arn
  type       = "STANDARD"
  definition = local.definition

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }

  tags = local.tags
}

# --- Daily EventBridge Scheduler ------------------------------------------------

resource "aws_iam_role" "scheduler" {
  name = "${var.project}-${var.env}-pipeline-scheduler"
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
    sid       = "StartPipelineExecution"
    effect    = "Allow"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.daily_pipeline.arn]
  }

  statement {
    sid       = "SendScheduleDeadLetters"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [var.dlq_arns["schedule"]]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "schedule"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

resource "aws_scheduler_schedule" "daily" {
  name        = "${var.project}-daily-${var.env}"
  description = "Daily optimization at the New York close"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = local.schedule_expression
  schedule_expression_timezone = local.schedule_timezone

  target {
    arn      = aws_sfn_state_machine.daily_pipeline.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ source = "alpaca" })

    dead_letter_config {
      arn = var.dlq_arns["schedule"]
    }

    retry_policy {
      maximum_retry_attempts       = 3
      maximum_event_age_in_seconds = 3600
    }
  }
}

# --- Outputs --------------------------------------------------------------------

output "state_machine_arn" {
  description = "Daily pipeline state machine ARN."
  value       = aws_sfn_state_machine.daily_pipeline.arn
}

output "state_machine_name" {
  description = "Daily pipeline state machine name."
  value       = aws_sfn_state_machine.daily_pipeline.name
}

output "function_arns" {
  description = "Pipeline lambda ARNs keyed by stage."
  value       = { for name, fn in aws_lambda_function.pipeline : name => fn.arn }
}

output "function_names" {
  description = "Pipeline lambda names keyed by stage."
  value       = { for name, fn in aws_lambda_function.pipeline : name => fn.function_name }
}

output "ecr_repository_uris" {
  description = "ECR repository URIs keyed by stage."
  value       = { for name, repo in aws_ecr_repository.functions : name => repo.repository_url }
}
