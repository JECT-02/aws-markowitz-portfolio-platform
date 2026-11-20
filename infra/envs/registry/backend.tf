# Registry root backend (AGENTS.md section 11.1): one state key per
# environment; the bucket name comes from infra/bootstrap via backend.hcl.
# `use_lockfile` requires Terraform >= 1.10.

terraform {
  backend "s3" {
    key          = "registry/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
