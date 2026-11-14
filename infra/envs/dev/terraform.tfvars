# Non-secret values for the dev environment. Secrets are NEVER stored here
# (AGENTS.md section 11.1): Alpaca credentials live in SSM SecureStrings
# (placeholders are created by the data module; set their real values once
# via console or `aws ssm put-parameter --overwrite`).
env = "dev"
