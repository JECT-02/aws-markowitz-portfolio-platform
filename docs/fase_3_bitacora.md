# Phase 3 log - AWS pipeline in CDK

Branch: `feat/fase-3-pipeline-cdk`. Date: 2026-11-12. No AWS resource was created, modified or
destroyed. No real broker call was made. No secret or account identifier is committed.

## Delivered

- `DataStack`: bronze, quarantine, silver and gold buckets (`BLOCK_ALL`, SSE, TLS-only, `RETAIN`,
  lifecycle), `PortfolioState` with provisioned 1/1 capacity, TTL and no PITR, SNS notifications,
  four function DLQs and one scheduler DLQ.
- `PipelineStack`: three container Lambdas (ingest, clean, optimizer), one ECR repository each,
  minimal IAM per function, SSM parameter policy limited to the two Alpaca parameter ARNs.
- Step Functions state machine: `Ingest -> CheckMarketCalendar -> Clean -> Optimize ->
  NotifySuccess`, market-closed branch to `MarketClosed`, retries only for transient Lambda errors
  and `Catch` to `NotifyFailure` with a terminal `Fail` state.
- EventBridge Scheduler: `cron(30 16 ? * MON-FRI *)` in `America/New_York`, flexible window off,
  retry policy and scheduler DLQ.
- SSM secret provider (`src/common/secrets.py`) with in-memory cache, and ingest now reads the
  Alpaca credentials through it instead of plain environment variables.
- DynamoDB state adapter (`src/optimizer/core/state_adapter.py`): reads `STATE/CURRENT`, writes
  proposals with `attribute_not_exists`, writes `FAILED` proposals on solver/infeasible errors and
  converts numbers to `Decimal`.
- Optimizer handler: event weights win over stored weights; stored weights are used when the event
  has none; failed proposals are recorded before the error is re-raised.
- Container Dockerfiles for ingest, clean and optimizer, pinned per-function requirements and
  tests that verify every pin against `uv.lock`.
- CI workflow with OIDC variables, no static keys, pinned action SHAs, tests, `cdk synth`, image
  builds and an optional `cdk diff` when `AWS_DEPLOY_ROLE_ARN` is configured.

## Decisions and deviations

- Container images replaced zip packages for ingest and clean. See
  `docs/adr/0001-container-lambda-images.md`. Human approval was given during this conversation.
- Step Functions has no state-machine DLQ property in CloudFormation. Failures are surfaced through
  the `NotifyFailure` SNS publish and the terminal `Fail` state, and the execution history keeps
  the error. The unused `StateMachineDlq` was removed instead of leaving an orphan queue.
- Only the slow loop is scheduled. The fast monitor loop is Phase 6, so no schedule points at a
  function that does not exist yet.
- `cdk.json` lives at the repository root (not `infra/`), so `cdk synth` resolves `uv run python -m
  infra.app` from the project root.
- Container images are built and pushed outside CDK; `cdk synth` never runs Docker. Image tag comes
  from `OPTIMIZER_IMAGE_TAG` (default `latest`).
- `AWS_REGION` is not set by CDK because the Lambda runtime reserves it.

## Verification executed

| Command | Result |
|---|---|
| `make format` | 102 files unchanged |
| `make lint` | clean |
| `make typecheck` | clean, 50 source files |
| `make security` | bandit 0 high, 0 medium; pip-audit no known vulnerabilities |
| `make test-unit` | 208 passed |
| `make test-property` | 10 passed |
| `make test-integration` | 3 passed |
| `make test-infra` | 27 passed |
| `make coverage` | 89.07 % (threshold 80 %) |
| `make coverage-core` | 94.16 % (threshold 90 %) |
| `make synth` | success, two stacks synthesized |

`cdk diff` was not executed because it requires AWS credentials and a deployed baseline.

## Not verified

- Container builds and imports inside the images: the Docker daemon was not running on this
  machine (`failed to connect to the docker API`). The Dockerfiles and the CI steps are written but
  not executed here.
- Whether the Lambda base image already ships `boto3` for the runtime. It is expected from the
  official Python base image, but it must be confirmed with the first local image build:
  `docker run --rm --entrypoint python portfolio-markowitz-ingest -c "import boto3"`.
- `cdk diff`, `cdk deploy`, SSM parameters, ECR pushes and any real Alpaca call.
- The GitHub Actions workflow has not run in CI yet.
- Phase 2 financial go/no-go remains open; it is not resolved by this phase.

## Operational notes before deploying

1. Create the two SSM `SecureString` parameters with the names from `ALPACA_API_KEY_PARAM` and
   `ALPACA_SECRET_KEY_PARAM`, and confirm the Alpaca client still uses `paper=True`.
2. Build and push all three images to their ECR repositories with the tag that `OPTIMIZER_IMAGE_TAG`
   will use. The `make build-images` target builds them locally; the push is manual.
3. Run `make synth` and `cdk diff` with real credentials, and review IAM, data and cost changes.
4. Configure `NOTIFICATION_EMAIL` to receive SNS email, and keep `DEPLOY_ENV=dev` until production
   promotion is approved.

Estimated monthly cost remains within the project budget: Lambda on-demand for one daily run,
Step Functions standard executions, provisioned DynamoDB 1/1, S3 small objects, SNS email, SQS
DLQs and three small ECR repositories.
