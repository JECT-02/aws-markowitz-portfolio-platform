# Phase 2 log: optimizer, backtest and temporal validation

Branch: `feat/fase-2-optimizer`. Base: `feat/fase-1-ingest` (merged into `main`). Python 3.12, `uv`.

No CDK, no deployment, no executor and no real calls to Alpaca or AWS.

## Implemented scope

1. Mandatory environment configuration (`src/common/config.py`, `src/common/env.py`).
   - `RuntimeSettings.from_environment`: `AWS_REGION`, `CODE_VERSION` and `CONFIG_DIR` are required.
   - `StorageSettings.from_environment`: `BRONZE_BUCKET`, `QUARANTINE_BUCKET`, `SILVER_BUCKET`,
     `GOLD_BUCKET`.
   - `StrategyConfig.from_environment`: `RISK_AVERSION`, `TRANSACTION_COST_LAMBDA`,
     `RETURN_SHRINKAGE_ALPHA`, `RISK_FREE_RATE_ANNUAL`, `MAX_FORWARD_FILL_DAYS`, `WINDOW_DAYS`,
     `INGEST_LOOKBACK_DAYS`, `REBALANCE_L1_THRESHOLD`, `VALIDATION_GAMMAS`,
     `VALIDATION_LAMBDAS`, `VALIDATION_ALPHAS`, `VALIDATION_WINDOWS`.
   - `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` are required by the ingest handler.
   - A missing variable raises `ConfigurationError` naming the exact variable. There are no
     default values or placeholders in the runtime path.
   - `config/universe.yaml` holds the universe and the position cap. `config/strategy.yaml` is a
     key schema only and is not read at runtime; the environment is the only source of values.
2. Estimators (`src/optimizer/core/estimation.py`): Ledoit-Wolf covariance with symmetrization
   and PSD validation; expected returns with shrinkage toward the global mean.
3. CVXPY optimizer (`src/optimizer/core/problem.py`): compiled parameters, budget constraint,
   long-only bounds, L1 turnover penalty, at least five valid assets and post-solve validation.
   - `optimal` is accepted with sum tolerance 1e-6 and bound tolerance 1e-8.
   - `optimal_inaccurate` is accepted with explicit tolerance 1e-4 and a service warning.
   - `infeasible` raises `InfeasibleError`; any other status raises `SolverError`. Constraints are
     never relaxed and actual weights are never updated on failure.
4. Benchmarks (`src/optimizer/core/benchmarks.py`): equal weight, minimum variance with the same
   caps, and SPY.
5. Metrics (`src/optimizer/core/metrics.py`): annualized return and volatility, Sharpe with an
   explicit risk-free rate, Sortino, maximum drawdown, Calmar, HHI and L1/one-way turnover.
6. Service and handler (`src/optimizer/core/service.py`, `src/optimizer/handler.py`): read Silver
   from S3, compute the proposal and write `run_date=YYYY-MM-DD/proposal.json` plus `latest.json`
   to Gold.
7. Walk-forward backtest (`src/backtest/core/engine.py`): moving window, monthly rebalancing,
   decision with data up to `t-1`, execution with returns at `t`, costs
   `lambda_tc * L1(w_star - w_actual)`, threshold 0.10 and passive weight drift between
   rebalances.
8. Temporal validation and sensitivity (`src/backtest/core/validation.py`): 60 percent training,
   80 percent validation and 20 percent test split, selection by validation Sharpe, separate
   in-sample and out-of-sample metrics, and best result per window size.
9. Local runners: `src/optimizer/local.py` and `src/backtest/local.py`. The backtest runner prints
   JSON with `selected`, `comparison`, `sensitivity`, period metrics and candidates.
10. Tests: unit, property-based with `hypothesis`, and integration with `moto`.
11. This document.

## Robustness and scalability review

- The Silver repository now reads partitions from newest to oldest and stops once it has the
  required number of dates. It no longer loads the full historical lakehouse for every run.
- Window validation now uses the loaded tail, while insufficient-history detection uses the
  loaded history, preserving explicit exclusion reasons.
- The optimizer no longer disables DPP, so compiled parameters are reused across solves.
- The backtest compiles the minimum-variance problem once and reuses it for every monthly
  rebalance instead of rebuilding it each month.
- Monthly rebalance membership uses a set instead of repeated list scans.
- When actual weights are not provided, the service emits an explicit warning before using equal
  weights as the prior, so the assumption is visible in logs.

## Defect found and fixed

The property-based test found that a very high transaction cost makes the solver return
`optimal_inaccurate`, which the code treated as an unexpected status. The behavior now follows
AGENTS section 5.4: accept the status with an explicit tolerance and emit a warning.

Pytest collection also failed because of duplicate test module names across directories. It was
fixed with `--import-mode=importlib` in `pyproject.toml`.

## Executed checks

| Command | Result |
|---|---|
| `make format` | Applied, no pending changes. |
| `make lint` | No errors. |
| `make typecheck` | `mypy --strict src` clean, 48 files. |
| `make security` | `bandit` 0 findings; `pip-audit` no known vulnerabilities. |
| `make test-unit` | 189 passed. |
| `make test-property` | 10 passed. |
| `make test-integration` | 3 passed, including optimizer with `moto`. |
| `make coverage` | 202 tests, 89.22 percent global against an 80 percent threshold. |
| `make coverage-core` | 94.88 percent for `optimizer/core` and `backtest/core` against 90 percent. |
| `make test-infra` | Not run successfully: `tests/infra` is empty until Phase 3, pytest exits with code 5. |
| `make synth` / `make diff` | Not run: there is no CDK app and `cdk` is not installed. |
| `git diff` | Reviewed, no secrets found. |

## Not tested and limitations

- No run against real S3 or real market data. The local backtest runner needs Silver in S3 and
  AWS credentials, so it was not executed.
- The Phase 2 decision criterion cannot be closed yet. The net-of-cost comparison against equal
  weight and minimum variance requires real data. This log does not claim that the model beats or
  loses to the benchmarks.
- The backtest rebalances benchmarks monthly without the production threshold and only pays costs
  when weights actually change.
- Temporal validation selects hyperparameters on the validation block and reports the test block
  without touching it. It does not run multiple nested folds.
- The local runners have no unit coverage because they are command-line entry points.
- No infrastructure was created or deployed, and no orders or actual weights were modified.

## Risks and pending work

- `INGEST_LOOKBACK_DAYS` must cover the longest validation window, for example 504 trading days,
  otherwise the backtest lacks history. This must be checked with the real calendar.
- Silver must contain aligned series per symbol. Symbols with gaps are excluded and recorded, not
  filled.
- Phase 3 (CDK, Step Functions, Scheduler, Gold, DynamoDB, SNS) and Phase 5 (executor) remain out
  of scope.

## Preserved invariants

- No short positions, no leverage, `0 <= w_i <= 0.20` and `sum(w) = 1` validated after solving.
- No look-ahead: decisions at `t` use returns up to `t-1`; a test confirms that altering future
  data does not change past decisions.
- No secrets in code, logs or commits; credentials come from the environment only.
- Paper mode remains untouched; this branch sends no orders and does not call the broker.
- Errors are logged and propagated; exceptions are not silenced and constraints are not relaxed.
