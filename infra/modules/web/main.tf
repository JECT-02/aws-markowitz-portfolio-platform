# Static SPA hosting (module `web`): private S3 bucket served through
# CloudFront with Origin Access Control. Mirrors the CDK WebStack without
# custom resources: assets upload with aws_s3_object, config.js is
# substituted at deploy time with string interpolation, and the CloudFront
# invalidation is keyed on the assets hash.

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
  web_dir     = "${path.module}/../../../web"
  config_key  = "config.js"
  asset_files = [for path in fileset(local.web_dir, "*") : path if path != local.config_key]

  # Deploy-time substitution of the three placeholders in web/config.js
  # (same effect as the CDK DeployTimeSubstitutedFile, natively in Terraform).
  config_content = replace(
    replace(
      replace(
        file("${local.web_dir}/${local.config_key}"),
        "{{ API_ENDPOINT }}",
        var.api_endpoint,
      ),
      "{{ COGNITO_DOMAIN }}",
      var.cognito_domain,
    ),
    "{{ CLIENT_ID }}",
    var.client_id,
  )

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

variable "api_endpoint" {
  type        = string
  description = "Base URL of the reader HTTP API."
}

variable "cognito_domain" {
  type        = string
  description = "HTTPS URL of the Cognito hosted UI domain."
}

variable "client_id" {
  type        = string
  description = "Cognito app client id for the SPA."
}

# --- SPA bucket ---------------------------------------------------------------

resource "aws_s3_bucket" "spa" {
  bucket = "${var.project}-${var.env}-spa"

  lifecycle {
    prevent_destroy = true
  }

  tags = local.tags
}

resource "aws_s3_bucket_versioning" "spa" {
  bucket = aws_s3_bucket.spa.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "spa" {
  # trivy:ignore:AVD-AWS-0132 SSE-S3 provides at-rest encryption; a
  # customer-managed KMS key costs ~$1/month against the $1-$5 monthly
  # budget of AGENTS.md with no benefit for these static assets.
  bucket = aws_s3_bucket.spa.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "spa" {
  bucket = aws_s3_bucket.spa.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "spa" {
  bucket = aws_s3_bucket.spa.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.spa.arn,
          "${aws_s3_bucket.spa.arn}/*",
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
      {
        Sid       = "AllowCloudFrontRead"
        Effect    = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.spa.arn}/*"
        Condition = {
          ArnLike = { "aws:SourceArn" = aws_cloudfront_distribution.spa.arn }
        }
      },
    ]
  })
}

# --- CloudFront ----------------------------------------------------------------

resource "aws_cloudfront_origin_access_control" "spa" {
  name                              = "${var.project}-${var.env}-spa-oac"
  description                       = "OAC for the SPA bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "spa" {
  comment             = "SPA for ${var.project} ${var.env}"
  enabled             = true
  default_root_object = "index.html"

  origin {
    domain_name              = aws_s3_bucket.spa.bucket_regional_domain_name
    origin_id                = "spa-bucket"
    origin_access_control_id = aws_cloudfront_origin_access_control.spa.id
  }

  default_cache_behavior {
    target_origin_id       = "spa-bucket"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.optimized.id
  }

  # config.js is substituted at deploy time and must never be cached.
  ordered_cache_behavior {
    path_pattern           = local.config_key
    target_origin_id       = "spa-bucket"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.disabled.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  custom_error_response {
    error_code            = 403
    response_page_path    = "/index.html"
    response_code         = 200
    error_caching_min_ttl = 0
  }

  custom_error_response {
    error_code            = 404
    response_page_path    = "/index.html"
    response_code         = 200
    error_caching_min_ttl = 0
  }

  tags = local.tags
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

# --- Asset upload ---------------------------------------------------------------

resource "aws_s3_object" "spa_assets" {
  for_each = toset(local.asset_files)

  bucket        = aws_s3_bucket.spa.id
  key           = each.key
  source        = "${local.web_dir}/${each.key}"
  etag          = filemd5("${local.web_dir}/${each.key}")
  content_type  = each.key == "index.html" ? "text/html" : (endswith(each.key, ".css") ? "text/css" : "text/javascript")
  cache_control = "public, max-age=300"
}

resource "aws_s3_object" "spa_config" {
  bucket        = aws_s3_bucket.spa.id
  key           = local.config_key
  content       = local.config_content
  etag          = md5(local.config_content)
  content_type  = "text/javascript"
  cache_control = "no-store"

  depends_on = [aws_s3_bucket.spa]
}

# CloudFront invalidation: the AWS provider does not manage invalidations as
# a resource, and AGENTS.md forbids local-exec/null_resource tricks. Assets
# carry short TTLs (5 minutes) and config.js is served no-store, so stale
# content clears by itself; the human may also run a manual invalidation
# (aws cloudfront create-invalidation --paths "/*") right after applying.

# --- Outputs -----------------------------------------------------------------------

output "spa_url" {
  description = "HTTPS URL of the CloudFront distribution."
  value       = "https://${aws_cloudfront_distribution.spa.domain_name}"
}

output "spa_bucket_name" {
  description = "SPA bucket name."
  value       = aws_s3_bucket.spa.id
}

output "distribution_id" {
  description = "CloudFront distribution id."
  value       = aws_cloudfront_distribution.spa.id
}
