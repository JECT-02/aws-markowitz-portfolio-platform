# Remote state backend (AGENTS.md section 11.1). The bucket is created by
# infra/bootstrap and applied by the human; the name is injected per
# environment with `-backend-config` (local override or CI repository
# variable), so no placeholder lives in the repository (N-17).
# `use_lockfile` requires Terraform >= 1.10 (AGENTS.md section 11.1).

terraform {
  backend "s3" {
    key          = "dev/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
