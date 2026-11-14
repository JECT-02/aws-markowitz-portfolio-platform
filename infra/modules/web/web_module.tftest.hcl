mock_provider "aws" {}

variables {
  project        = "portfolio-markowitz-aws"
  env            = "dev"
  api_endpoint   = "https://api.example.amazonaws.com"
  cognito_domain = "https://portfolio-markowitz-dev.auth.us-east-1.amazoncognito.com"
  client_id      = "client123"
}

run "spa_bucket_is_private_versioned_and_encrypted" {
  command = plan

  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.spa.block_public_acls,
      aws_s3_bucket_public_access_block.spa.block_public_policy,
      aws_s3_bucket_public_access_block.spa.ignore_public_acls,
      aws_s3_bucket_public_access_block.spa.restrict_public_buckets,
    ])
    error_message = "The SPA bucket must block all public access."
  }

  assert {
    condition     = aws_s3_bucket_versioning.spa.versioning_configuration[0].status == "Enabled"
    error_message = "The SPA bucket must be versioned."
  }
}

run "cloudfront_redirects_to_https" {
  command = plan

  assert {
    condition = alltrue([
      aws_cloudfront_distribution.spa.default_cache_behavior[0].viewer_protocol_policy == "redirect-to-https",
      aws_cloudfront_distribution.spa.ordered_cache_behavior[0].viewer_protocol_policy == "redirect-to-https",
    ])
    error_message = "HTTPS is mandatory (redirect-to-https on every behavior)."
  }
}

run "config_js_is_never_cached" {
  command = plan

  assert {
    condition     = aws_cloudfront_distribution.spa.ordered_cache_behavior[0].path_pattern == "config.js"
    error_message = "config.js must have its own no-cache behavior."
  }

  assert {
    condition     = data.aws_cloudfront_cache_policy.disabled.name == "Managed-CachingDisabled"
    error_message = "config.js uses the managed CachingDisabled policy."
  }

  assert {
    condition     = aws_s3_object.spa_config.cache_control == "no-store"
    error_message = "The substituted config.js must be served no-store."
  }
}

run "spa_routing_errors_fall_back_to_index" {
  command = plan

  assert {
    condition = alltrue([
      for response in aws_cloudfront_distribution.spa.custom_error_response :
      response.response_page_path == "/index.html" && response.response_code == 200
    ])
    error_message = "403/404 must fall back to index.html (SPA routing)."
  }
}

run "config_substitution_renders_all_placeholders" {
  command = plan

  assert {
    condition     = !strcontains(aws_s3_object.spa_config.content, "{{ API_ENDPOINT }}")
    error_message = "The API endpoint placeholder must be substituted at deploy time."
  }

  assert {
    condition     = !strcontains(aws_s3_object.spa_config.content, "{{ COGNITO_DOMAIN }}")
    error_message = "The Cognito domain placeholder must be substituted at deploy time."
  }

  assert {
    condition     = !strcontains(aws_s3_object.spa_config.content, "{{ CLIENT_ID }}")
    error_message = "The client id placeholder must be substituted at deploy time."
  }
}

run "every_non_config_asset_is_uploaded" {
  command = plan

  assert {
    condition = alltrue([
      for key in keys(aws_s3_object.spa_assets) : key != "config.js"
    ])
    error_message = "The raw config.js must never be uploaded (only the substituted one)."
  }
}
