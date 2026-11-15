"""Fast monitoring loop: one drift observation every 15 minutes with an open
market.

The handler never reoptimizes and never submits orders; it reads the latest
gold proposal, samples the real paper-account positions and equity through
the shared Alpaca adapter, computes the L1 drift and stores one ``MONITOR``
record (AGENTS.md sections 1 and 7.2).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from common.adapters.alpaca import AlpacaPaperBroker
from common.adapters.secrets import load_alpaca_credentials
from common.clock import Clock, SystemClock
from common.config import RuntimeSettings, StorageSettings
from common.env import load_env_file
from common.errors import ConfigurationError, PortfolioError
from common.logging import configure_logging, get_logger
from common.models import BrokerPosition
from common.storage import S3Storage
from monitor.core.drift import drift_snapshot
from monitor.core.proposal import GoldProposalReader, S3LatestProposal
from monitor.core.state import DynamoMonitorState, MonitorState

logger = get_logger(__name__)

_ALPACA_PAPER_ENV = "ALPACA_PAPER"
_ALPACA_API_KEY_PARAMETER_ENV = "ALPACA_API_KEY_PARAMETER"
# B105 false positive: the constant holds the *name* of an environment
# variable (an SSM parameter path), never the secret itself.
_ALPACA_SECRET_KEY_PARAMETER_ENV = "ALPACA_SECRET_KEY_PARAMETER"  # nosec B105
_REBALANCE_THRESHOLD_ENV = "REBALANCE_L1_THRESHOLD"


class MarketSampler(Protocol):
    """Read-only broker contract the fast loop depends on."""

    def is_market_open(self) -> bool: ...

    def get_positions(self) -> list[BrokerPosition]: ...

    def get_account_equity(self) -> float: ...


def handler(_event: object | None = None, _context: object | None = None) -> dict[str, object]:
    """EventBridge Scheduler entry point for the fast loop."""
    load_env_file(Path(".env"))
    configure_logging()
    deps = build_dependencies()
    return run_monitor(deps)


@dataclass(frozen=True)
class MonitorDependencies:
    """Collaborators injected into one fast-loop run."""

    broker: MarketSampler
    proposal_reader: GoldProposalReader
    state: MonitorState
    rebalance_threshold: float
    clock: Clock


def run_monitor(deps: MonitorDependencies) -> dict[str, object]:
    """Run one fast-loop observation.

    Preconditions: the dependencies are fully built from the environment.
    Postconditions: with an open market and an available proposal the run
    stores exactly one ``MONITOR`` snapshot and returns its validated payload;
    a closed market returns a skip marker without touching DynamoDB. Every
    error is logged with context and re-raised so the asynchronous invocation
    lands in the monitor DLQ instead of failing silently.
    """
    try:
        if not deps.broker.is_market_open():
            logger.info("market closed, monitor run skipped")
            return {"skipped": "market_closed"}
        proposal = deps.proposal_reader.read_latest()
        positions = deps.broker.get_positions()
        equity = deps.broker.get_account_equity()
        snapshot = drift_snapshot(
            proposal=proposal,
            positions=positions,
            account_equity=equity,
            rebalance_threshold=deps.rebalance_threshold,
            computed_at=deps.clock.now(),
        )
        deps.state.write_snapshot(snapshot, deps.clock.now())
    except PortfolioError:
        logger.exception("monitor run failed")
        raise
    except Exception:
        logger.exception("unexpected monitor failure")
        raise
    logger.info(
        "monitor run completed",
        extra={
            "drift_l1": snapshot.drift_l1,
            "rebalance_required": snapshot.rebalance_required,
            "proposal_id": snapshot.proposal_id,
        },
    )
    payload: dict[str, object] = snapshot.model_dump(mode="json")
    return payload


def build_dependencies() -> MonitorDependencies:
    """Build the fast-loop collaborators from the environment.

    Precondition: Terraform (or a local test harness) exported the storage,
    state, SSM and strategy environment variables. Postcondition: the
    threshold is strictly positive and the broker is the fail-closed paper
    adapter.
    """
    runtime = RuntimeSettings.from_environment()
    storage_settings = StorageSettings.from_environment()
    return MonitorDependencies(
        broker=_build_broker(runtime),
        proposal_reader=S3LatestProposal(
            storage=S3Storage(region_name=runtime.aws_region),
            bucket=storage_settings.gold_bucket,
        ),
        state=_build_state(runtime),
        rebalance_threshold=_threshold(),
        clock=SystemClock(),
    )


def _build_state(runtime: RuntimeSettings) -> MonitorState:
    table_name = os.environ.get("STATE_TABLE")
    if table_name is None or not table_name.strip():
        raise ConfigurationError("missing environment variable: STATE_TABLE")
    return DynamoMonitorState(
        table_name.strip(),
        region_name=runtime.aws_region,
    )


def _build_broker(runtime: RuntimeSettings) -> AlpacaPaperBroker:
    """Build the fail-closed Alpaca paper adapter (mirror of the executor)."""
    paper = os.environ.get(_ALPACA_PAPER_ENV, "")
    if paper.strip().lower() != "true":
        raise ConfigurationError(f"{_ALPACA_PAPER_ENV} must be 'true'; live trading is forbidden")
    api_key_parameter = os.environ.get(_ALPACA_API_KEY_PARAMETER_ENV, "")
    secret_key_parameter = os.environ.get(_ALPACA_SECRET_KEY_PARAMETER_ENV, "")
    if not api_key_parameter.strip() or not secret_key_parameter.strip():
        raise ConfigurationError(
            f"missing environment variables: {_ALPACA_API_KEY_PARAMETER_ENV}, "
            f"{_ALPACA_SECRET_KEY_PARAMETER_ENV}"
        )
    credentials = load_alpaca_credentials(
        api_key_parameter=api_key_parameter.strip(),
        secret_key_parameter=secret_key_parameter.strip(),
        region_name=runtime.aws_region,
    )
    return AlpacaPaperBroker(
        api_key=credentials.api_key,
        secret_key=credentials.secret_key,
        paper=True,
    )


def _threshold() -> float:
    raw = os.environ.get(_REBALANCE_THRESHOLD_ENV, "")
    if not raw.strip():
        raise ConfigurationError(f"missing environment variable: {_REBALANCE_THRESHOLD_ENV}")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(
            f"invalid value for environment variable {_REBALANCE_THRESHOLD_ENV}"
        ) from exc
    if value <= 0.0:
        raise ConfigurationError(f"{_REBALANCE_THRESHOLD_ENV} must be strictly positive")
    return value
