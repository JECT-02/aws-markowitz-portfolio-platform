# Phase 3 plan: AWS pipeline

Branch: `feat/fase-3-pipeline-aws`. Base: `main` after the merge of PR #3.
Design reference: `Architecture.md`, sections 4, 5, 10, 11, 12, 13 and 17.

## Objective

Deploy the daily slow loop with infrastructure as code, without changing the financial
strategy, the risk limits or the execution rules.

Flow to build:

```text
EventBridge Scheduler (16:30 America/New_York, MON-FRI)
  -> Step Functions Standard
       Ingest -> Clean -> Optimizer -> SNS notify
```

The pipeline must be reproducible with `cdk synth` and `cdk diff`, must never deploy from this
branch without explicit human confirmation, and must never contain secret values.

## Scope

In scope:

- `infra/` CDK app in Python with `DataStack`, `PipelineStack` and `ObservabilityStack`.
- S3 buckets: bronze, quarantine, silver and gold.
- DynamoDB `PortfolioState` with provisioned capacity, PITR and on-demand writes disabled.
- SNS topic for proposals and failures, plus SQS dead-letter queues.
- Lambda packaging for ingest and clean, and a Docker image for the optimizer.
- Step Functions state machine with retries, catches, DLQs and terminal failure notification.
- EventBridge Scheduler with the New York time zone and separate rules for slow and fast loops.
- DynamoDB state adapter so the optimizer reads `STATE/CURRENT` and writes the proposal with a
  `PENDING` or `NO_REBALANCE` status, and `FAILED` on solver errors.
- CloudWatch log retention, alarms and an AWS Budget with warnings.
- `tests/infra` assertions for encryption, public access, capacity, IAM, DLQ and schedule.
- GitHub Actions workflow for `ruff`, `mypy`, tests, `cdk synth` and `cdk diff` with OIDC.
- Phase 3 log in `docs/fase_3_bitacora.md`.

Out of scope:

- `cdk deploy`, `cdk destroy` or any real AWS mutation.
- Cognito, API Gateway, Reader and SPA (Phase 4).
- Executor, idempotency keys and broker orders (Phase 5).
- Monitor loop implementation and weekly cloud backtest (Phase 6).
- Athena and Glue (Phase 7).
- Changing `gamma`, `lambda_tc`, `alpha`, window, `0.20` cap or the `0.10` threshold.

## Constraints

- Runtime values still come only from the environment. CDK sets them per function; it never
  embeds secrets.
- Alpaca credentials stay in SSM Parameter Store `SecureString`. The CDK references parameter
  names, not values. The parameters must already exist in the target account.
- `paper=True` remains hard-coded in the broker client. No trading endpoint is touched in this
  phase.
- No NAT Gateway, no VPC, no EC2, no RDS and no hourly resources.
- Every Lambda has explicit memory, timeout, log retention and DLQ.
- One IAM role per function with specific actions and resource ARNs.
- Data buckets use `BLOCK_ALL`, encryption, TLS enforcement, `RETAIN` and a lifecycle policy.
- DynamoDB uses `Decimal`, conditional writes and `RETAIN`.
- All resources carry `project`, `env`, `owner` and `cost-center` tags.

## Proposed repository changes

```text
infra/
  app.py
  cdk.json
  stacks/
    data_stack.py
    pipeline_stack.py
    observability_stack.py
  constructs/
    tagged_function.py
    bucket_policy.py
src/
  common/
    secrets.py
    state.py
  ingest/
    requirements.txt
  clean/
    requirements.txt
  optimizer/
    core/
      state_adapter.py
    Dockerfile
    requirements.txt
tests/
  infra/
    test_data_stack.py
    test_pipeline_stack.py
    test_iac_invariants.py
    test_requirements.py
    fixtures/
.github/workflows/
  ci.yml
```

## Implementation order

1. Documentation only: this plan.
2. Dependencies and scaffolding: add `aws-cdk-lib` and `constructs`, create `infra/cdk.json`,
   `infra/app.py`, environment context resolution and the tag helper. No stacks yet.
3. `DataStack`: buckets, table, topic, DLQs. Infra tests for encryption, public access,
   provisioned capacity, PITR, retention and tags.
4. Secrets from SSM: provider protocol, adapter, settings and tests.
5. State adapter: DynamoDB reads and writes with `Decimal`, conditional proposal transitions,
   failure proposal on `SolverError` or `InfeasibleError`, unit tests with `moto`.
6. Lambda packaging: per-function requirements files, Docker bundling asset and a test that
   pins match `uv.lock`.
7. Optimizer image: `Dockerfile`, pinned runtime requirements, `DockerImageFunction`, health
   check through a local test. Verify `arm64` wheels before choosing the architecture; if any
   dependency lacks them, use `x86_64` and record the reason.
8. `PipelineStack`: ingest and clean zips, optimizer image, state machine, scheduler and SNS
   wiring. Infra tests for retries, catches, time zone, DLQs, IAM and function settings.
9. `ObservabilityStack`: alarms for Lambda errors, state machine failures, DLQ depth and missing
   daily run; budget with 50, 80 and 100 percent notifications.
10. CI workflow: OIDC role assumption, no static keys, `cdk synth` and `cdk diff` on pull
    requests, manual approval gate for any deploy job.
11. Phase 3 log with real command results and open limitations.

Each step is one atomic commit, with English messages and no secrets.

## Detailed execution design

### Packaging and build

- Ingest and clean are zip Lambdas built with Docker bundling from `python:3.12-slim`. The
  bundling command copies `src` and `config` into the asset and installs the function
  requirements into the same root, so imports resolve as `common`, `ingest`, `clean` and so on.
  Docker bundling avoids polluting the Windows host and produces manylinux wheels.
- Each zip function has its own exact-pinned requirements file: `src/ingest/requirements.txt`
  and `src/clean/requirements.txt`. A test asserts that every pin matches `uv.lock`, so the
  lockfile stays the single source of truth.
- boto3 is not bundled. The Lambda runtime provides it, and bundling it risks version drift.
- The optimizer is a single-stage image from `python:3.12-slim` with pinned requirements, a
  non-root user, no build tools in the final layer and a `python -c "import cvxpy"` health
  check. Default platform is `x86_64`; `arm64` is used only after verifying wheels for every
  dependency.
- `CONFIG_DIR` is `/var/task/config` for zip functions and `/var/task/config` for the container.
  The `config` directory is copied into the asset, never read from the developer machine at
  runtime.

### Secrets

The current ingest handler reads `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` from the environment.
That conflicts with the design, which requires SSM `SecureString`.

- Add `src/common/secrets.py` with a `SecretProvider` protocol and an SSM implementation that
  calls `GetParameter` with `WithDecryption=True` and caches values in memory for the life of
  the Lambda.
- Add `SecretsSettings.from_environment` with `ALPACA_API_KEY_PARAM` and
  `ALPACA_SECRET_KEY_PARAM`.
- The ingest handler receives the provider through its dependencies. Tests use a fake provider;
  `moto` covers the SSM adapter.
- Local runners that need the broker use the same provider and therefore need AWS credentials.
  There is no silent fallback to plain environment variables.

### IAM matrix

| Function | S3 | DynamoDB | SSM | Logs and DLQ |
|---|---|---|---|---|
| Ingest | List and Get on bronze, Put on bronze and quarantine prefixes | none | GetParameter on the two Alpaca parameters | PutLogEvents, SendMessage to its DLQ |
| Clean | List and Get on bronze, Put on silver | none | none | PutLogEvents, SendMessage to its DLQ |
| Optimizer | List and Get on silver, Put on gold | GetItem and conditional PutItem on the state table | none | PutLogEvents, SendMessage to its DLQ |
| State machine | none | none | none | SNS Publish, SQS SendMessage to its DLQ |

No policy uses `Action: "*"` or `Resource: "*"`. Prefix conditions use the bucket ARN plus
`/*` only for the allowed key prefixes.

### State machine

```text
Ingest (Lambda, retry on service and throttling errors)
  Catch -> NotifyFailure -> Fail
Choice on $.status
  SKIPPED -> Succeed
  default -> Clean -> Optimizer -> NotifySuccess -> Succeed
Clean or Optimizer failure
  Catch -> NotifyFailure -> Fail
```

- The state machine is Standard, with a 15 minute timeout and an SQS DLQ.
- Retries: three attempts, exponential backoff with jitter, for transient Lambda and network
  errors. Business failures such as infeasible or solver errors are not retried.
- Only metadata is passed between states. Price series stay in S3.
- `NotifyFailure` publishes to SNS, then the execution fails so the alarm fires.

### DynamoDB access patterns

The table keeps the `pk` and `sk` design from `Architecture.md` section 10.

- Read `pk=STATE, sk=CURRENT` to obtain actual weights. Missing item means equal weights as the
  prior, with a warning and no invented state.
- Write `pk=PROPOSAL, sk={proposal_id}` with `attribute_not_exists` to avoid overwriting a
  proposal with the same id.
- Failure records use status `FAILED` and add an optional `error` attribute. This is an additive
  schema change and requires approval before implementation.
- All numbers are `Decimal`, and NaN or infinity is rejected before writing.

### Function environment contract

| Variable | Ingest | Clean | Optimizer |
|---|---|---|---|
| `AWS_REGION` | runtime | runtime | runtime |
| `CODE_VERSION` | context | context | context |
| `CONFIG_DIR` | `/var/task/config` | `/var/task/config` | `/var/task/config` |
| `BRONZE_BUCKET` | yes | yes | no |
| `QUARANTINE_BUCKET` | yes | no | no |
| `SILVER_BUCKET` | no | yes | yes |
| `GOLD_BUCKET` | no | no | yes |
| `STATE_TABLE` | no | no | yes |
| `ALPACA_API_KEY_PARAM` | yes | no | no |
| `ALPACA_SECRET_KEY_PARAM` | yes | no | no |
| strategy values | yes | yes | yes |

### CI workflow

- Pull requests: `ruff format --check`, `ruff check`, `mypy --strict`, `pytest`, `cdk synth` and
  `cdk diff` against a read-only role.
- The deploy job is manual (`workflow_dispatch`), gated by a GitHub environment with required
  reviewers, and is the only job with write permissions. It is not part of this branch.
- OIDC only. The role ARN comes from repository variables, never from a committed file.
- Third-party actions are pinned by commit SHA.

## Contracts to preserve

- Ingest event: optional `run_date`, `run_id` and `source`; response includes `status`,
  `run_id`, `run_date`, `accepted` and `quarantined`.
- Clean event: same metadata; response includes `status`, `run_id`, `run_date`, `used`,
  `missing` and `excluded`.
- Optimizer event: optional `actual_weights`; when absent, the adapter reads `STATE/CURRENT`.
- Optimizer response: `status`, `proposal_id`, `run_id`, `run_date`, `weights`, `drift_l1`,
  `rebalance_required` and `excluded`.
- A market-closed day ends the state machine successfully with `SKIPPED`, not as a failure.
- A solver failure writes a `FAILED` proposal and does not touch `STATE/CURRENT`.

New schema fields require approval. The plan assumes no change to existing proposal fields and
only adds the DynamoDB records described in `Architecture.md` section 10.

## Testing plan

- Unit: state adapter with `moto`, `Decimal` conversion, conditional conflicts and failure
  proposals.
- Infra: `assertions.Template` for bucket encryption and public access, table capacity and PITR,
  Lambda memory, timeout, DLQ and log retention, IAM without wildcards, SNS subscriptions,
  scheduler time zone and state machine retries.
- Integration: state machine definition rendered by `cdk synth` contains the expected order and
  error paths.
- Security: no secret values in synthesized templates, no wildcard IAM actions or resources.
- Cost: estimate each new resource in the pull request; expected range remains around one to five
  dollars per month.

## Acceptance criteria

- `make format`, `make lint`, `make typecheck`, `make security` clean.
- `make test-unit`, `make test-property`, `make test-integration` and `make test-infra` green.
- `make coverage` above 80 percent; `make coverage-core` above 90 percent.
- `make synth` succeeds with no real AWS calls.
- `cdk diff` reviewed; IAM, data, network and cost changes explained.
- No secrets, account identifiers or emails committed.
- No deploy performed.

## Open decisions requiring approval

1. Add `aws-cdk-lib` and `constructs` to the dev dependency group. Exact pins will be resolved
   with `uv lock`, and versions published less than 14 days ago will be avoided.
2. Confirm the SSM parameter names for the Alpaca key and secret, or approve creating them
   manually in the target account. The CDK will only reference them.
3. Provide the target AWS account and region for `dev` through environment or CDK context. They
   are not committed.
4. Decide whether the SNS email subscription is created by CDK from a context value or managed
   manually. The email address is not committed.
5. Confirm the Lambda architecture after verifying `arm64` wheels for `cvxpy`, `clarabel`,
   `scikit-learn`, `numpy`, `scipy` and `pyarrow`.
6. Approve the additive `error` attribute on `FAILED` proposal records. No existing field
   changes. If rejected, failures stay only in logs and SNS.

## Risks

- Phase 2 has no real metrics yet, so the financial go/no-go remains open. Building the pipeline
  does not validate the strategy.
- Packaging `cvxpy` inside a container image can exceed the size or cold-start budget; the image
  must be measured locally before deployment.
- Step Functions payload limits are not a concern at the current sizes, but the state machine
  must pass only metadata and references, not full price series.
- DynamoDB writes for proposals introduce a new failure mode. Conditional writes and a failure
  proposal are required before wiring SNS.
- `AGENTS.md` references `docs/plan_markowitz_aws_serverless.md`, which does not exist in the
  repository. The real design document is `Architecture.md`. This inconsistency must be resolved
  before Phase 4 documentation work.

## Definition of done for this branch

All code, tests and documentation for the scope above are committed atomically. Nothing is
deployed. The branch is ready for human review, and the Phase 2 financial decision remains
explicitly pending.
