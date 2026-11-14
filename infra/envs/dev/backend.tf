# Remote state backend (AGENTS.md section 11.1). Fill in `bucket` with the
# `state_bucket_name` output of infra/bootstrap after the human applies it
# once. `use_lockfile` requires Terraform >= 1.10 (AGENTS.md section 11.1).

terraform {
  backend "s3" {
    bucket       = "portfolio-markowitz-tfstate-REPLACE_WITH_ACCOUNT_ID"
    key          = "dev/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
