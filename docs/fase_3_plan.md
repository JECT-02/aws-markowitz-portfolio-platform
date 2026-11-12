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
    state.py
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
4. Optimizer image: `Dockerfile`, pinned runtime requirements, `DockerImageFunction`, health
   check through a local test. Verify `arm64` wheels before choosing the architecture; if any
   dependency lacks them, use `x86_64` and record the reason.
5. State adapter: DynamoDB reads and writes with `Decimal`, conditional proposal transitions,
   failure proposal on `SolverError` or `InfeasibleError`, unit tests with `moto`.
6. `PipelineStack`: ingest and clean zips, optimizer image, state machine, scheduler and SNS
   wiring. Infra tests for retries, catches, time zone, DLQs, IAM and function settings.
7. `ObservabilityStack`: alarms for Lambda errors, state machine failures, DLQ depth and missing
   daily run; budget with 50, 80 and 100 percent notifications.
8. CI workflow: OIDC role assumption, no static keys, `cdk synth` and `cdk diff` on pull
   requests, manual approval gate for any deploy job.
9. Phase 3 log with real command results and open limitations.

Each step is one atomic commit, with English messages and no secrets.

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
