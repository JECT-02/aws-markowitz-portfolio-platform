# Phase 4 plan: Cognito, API Gateway, Reader and SPA

Branch: `feat/fase-4-api-spa`. Base: `main` after the merge of PR #7 and the
removal of the GitHub Actions workflow.
Design reference: `Architecture.md` (sections 9, 14, 15, 16 and 18),
`AGENTS.md` (sections 1, 2, 7, 8, 11, 12 and 17).

## Refined delivery: one branch per PR

The phase is split into three sequential branches; each one is its own PR and
must pass the full verification checklist before the next one starts.

| # | Branch | Content |
|---|---|---|
| 1 | `feat/reader-lambda` | `src/reader/core/` pure logic, response models, `handler.py` with HTTP API v2 routing, unit and moto tests. No CDK changes. |
| 2 | `feat/api-stack` | `infra/stacks/api_stack.py`: Cognito (PKCE, MFA optional), HTTP API with JWT authorizer, restricted CORS, throttling, least-privilege Reader Lambda. `app.py` wiring, infra tests, reviewed `cdk synth`/`cdk diff`. |
| 3 | `feat/web-spa` | `web/` static SPA (Cognito PKCE login, pinned Plotly.js) plus `infra/stacks/web_stack.py` (S3 BLOCK_ALL, CloudFront OAC). Infra tests. |

## Decisions taken at planning time

- `GET /backtest` is implemented now and returns `404` with an explicit message
  until the weekly cloud backtest exists (Phase 6); this keeps a stable API
  contract for the SPA.
- `GET /monitor` returns the reconciled `STATE/CURRENT` weights when available
  (Phase 5 writes them) with the drift of the latest proposal as fallback
  context. No `MONITOR` partition reads until Phase 6.
- The Reader reuses `optimizer.core.models.Proposal` to parse Gold documents:
  the stored JSON is exactly that model's dump; no duplicated schema.
- Cognito MFA is configured as `OPTIONAL` (recommended) for the single-user
  workload, which is reversible; moving to `REQUIRED` is a later decision.

## Objective

Expose the state produced by the Phase 3 pipeline through a private HTTP API and
a static single-page application, so the human owner can inspect proposals,
metrics and monitoring without any code change in the financial strategy, the
risk limits or the execution rules.

## Current starting point (assumed green)

- Phase 3 pipeline exists in `infra/stacks/data_stack.py` and
  `infra/stacks/pipeline_stack.py` (bronze/quarantine/silver/gold buckets,
  `PortfolioState` DynamoDB table, SNS, Step Functions, EventBridge Scheduler,
  optimizer Docker image).
- The optimizer writes `PROPOSAL/{proposal_id}` with `prices_ref`, status,
  `w_star`, drift and TTL.
- `src/reader/` only contains an empty package: the Reader must be built from
  scratch. There is no `web/` directory yet.

## Flow to build

```text
Browser (SPA on CloudFront) -> Cognito Hosted UI (PKCE, MFA)
  -> JWT -> API Gateway HTTP API (JWT authorizer, CORS restricted)
       GET /proposal /metrics /backtest /monitor -> Reader Lambda
            reads Gold S3 (latest.json + run partitions) and DynamoDB
```

Read-only path. Nothing in this phase posts anything or sends orders.

## Scope

In scope:

- `src/reader/core/`: pure logic to load and shape Gold artifacts
  (`latest.json`, `run_date=.../proposal.json`) and DynamoDB items into typed
  Pydantic responses; no I/O in core.
- `src/reader/handler.py`: API Gateway (HTTP API payload v2) Lambda that
  routes `GET /proposal`, `GET /metrics`, `GET /backtest`, `GET /monitor`,
  validates path/query parameters, returns safe 4xx messages and 5xx with
  structured logs (run_id, correlation). Read-only IAM.
- `infra/stacks/api_stack.py`: API Gateway HTTP API with Cognito JWT
  authorizer, restricted CORS (SPA domain only), throttling, explicit
  memory/timeout/retention for the Reader Lambda. The Reader role is scoped to
  the gold bucket objects and read-only table actions on `PortfolioState`.
- `infra/stacks/web_stack.py`: S3 bucket (BLOCK_ALL, encryption, `enforce_ssl`,
  versioning), CloudFront distribution with OAC, HTTPS-only viewer policy,
  static asset delivery.
- Cognito user pool with PKCE and MFA recommended/applicable, app client for
  the SPA, password policy. Parameter Store references only; no secrets in CDK.
- `web/`: static SPA (no framework build step) that performs the Cognito PKCE
  login, calls the four GET endpoints with the JWT and renders weights, drift
  and metrics with Plotly.js loaded from a pinned CDN version.
- New environment variables wired through `infra/config.py` +
  `src/common/config.py` and declared in `.env` even when empty (spa domain,
  Cognito identifiers, allowed origins).
- Tests: unit tests for reader core (shaping, missing artifacts, malformed
  data -> typed errors), integration tests with moto (handler over S3+DDB
  fixtures, 404 vs 422 behavior, no auth bypass), infra tests asserting the JWT
  authorizer exists and is required, CORS allow-origins equal to the SPA
  domain, OAC on CloudFront, BLOCK_ALL and encryption on the web bucket,
  throttling limits, least-privilege Reader IAM (no wildcards).
- `docs/fase_4_bitacora.md` updated with the phase results (log stays Spanish).

Out of scope:

- `cdk deploy`, `cdk destroy` or any real AWS mutation from this branch.
- Executor, idempotency keys, orders, Alpaca calls (Phase 5).
- Monitor fast loop and weekly cloud backtest (Phase 6).
- Athena/Glue (Phase 7).
- Any change to `gamma`, `lambda_tc`, `alpha`, window, `0.20` cap, `0.10`
  threshold or the optimization code.
- Custom CloudWatch metrics, alarms, dashboards or X-Ray.

## Constraints

- The API is read-only: no POST routes in this phase.
- Auth is mandatory: the API Gateway JWT authorizer is the gate; the Reader
  Lambda never trusts query strings for identity.
- CORS restricted to the SPA origin; never `*`.
- All numeric values read from DynamoDB arrive as `Decimal` and are converted
  explicitly; no float comparisons with `==`.
- CloudFront + HTTP API + Cognito stay inside the free/always-low tier for a
  single-user workload; the PR must include the monthly cost estimate.
- Log retention 1-3 days; no hour-billed resources; tags `project`, `env`,
  `owner`, `cost-center` on every resource.

## Verification before merge

- `make format`, `make lint`, `make typecheck`, `make security` clean.
- `make test-unit`, `test-property`, `test-integration`, `test-infra` green.
- `make coverage` >= 80 percent global, >= 90 percent in reader core.
- `make synth` OK and `make diff` reviewed and attached to the PR with the IAM
  and cost explanation.
- No secrets in the diff.
