plugin "terraform" {
  enabled = true
  version = "0.15.0"
  source  = "github.com/terraform-linters/tflint-ruleset-terraform"
}

rule "terraform_deprecated_index" {
  enabled = true
}

rule "terraform_comment_syntax" {
  enabled = true
}

rule "terraform_documented_variables" {
  enabled = true
}

rule "terraform_typed_variables" {
  enabled = true
}

# infra/ holds only the canonical pins (versions.tf); the real roots are
# infra/bootstrap and infra/envs/*, which follow the standard module layout.
rule "terraform_standard_module_structure" {
  enabled = false
}

# infra/versions.tf is the canonical pin record; every env root mirrors the
# block, so the "unused provider" warning here is expected.
rule "terraform_unused_required_providers" {
  enabled = false
}
