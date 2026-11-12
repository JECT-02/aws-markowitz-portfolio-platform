from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from alpaca.data.enums import DataFeed
from alpaca.trading.client import TradingClient

from common.clock import Clock, SystemClock
from common.config import (
    StorageSettings,
    StrategyConfig,
    UniverseConfig,
    load_strategy_config,
    load_universe_config,
)
from common.errors import ConfigurationError
from common.logging import configure_logging, get_logger
from common.models import DataSource, RunMetadata
from common.storage import S3Storage
from ingest.adapters.alpaca import AlpacaMarketDataSource
from ingest.adapters.yfinance import YFinanceMarketDataSource
from ingest.calendar import TradingCalendar
from ingest.core.service import IngestService

_EASTERN = ZoneInfo("America/New_York")
logger = get_logger(__name__)


@dataclass(frozen=True)
class IngestDependencies:
    service: IngestService
    calendar: TradingCalendar
    clock: Clock
    universe: UniverseConfig
    strategy: StrategyConfig
    code_version: str


def run_ingestion(event: Mapping[str, object], deps: IngestDependencies) -> dict[str, object]:
    """Run the daily ingestion for the event run date."""
    run_date = _resolve_run_date(event, deps.clock)
    if not deps.calendar.is_trading_day(run_date):
        logger.info("market closed", extra={"run_date": run_date.isoformat()})
        return {"status": "SKIPPED", "run_date": run_date.isoformat(), "reason": "market_closed"}
    metadata = RunMetadata(
        run_id=_resolve_run_id(event),
        run_date=run_date,
        code_version=deps.code_version,
        source=DataSource.ALPACA,
        fetched_at=deps.clock.now(),
    )
    result = deps.service.run(
        metadata,
        deps.universe.tickers,
        run_date - timedelta(days=deps.strategy.ingest_lookback_days),
        run_date - timedelta(days=1),
    )
    logger.info(
        "ingestion completed",
        extra={
            "run_id": metadata.run_id,
            "run_date": run_date.isoformat(),
            "accepted": len(result.accepted),
            "quarantined": len(result.quarantined),
        },
    )
    return {
        "status": "COMPLETED",
        "run_id": metadata.run_id,
        "run_date": run_date.isoformat(),
        "source": result.metadata.source.value,
        "accepted": dict(result.accepted),
        "quarantined": dict(result.quarantined),
    }


def handler(event: Mapping[str, object], _context: object | None = None) -> dict[str, object]:
    """AWS Lambda entry point for the ingest stage."""
    configure_logging()
    return run_ingestion(event, _build_dependencies())


def _build_dependencies() -> IngestDependencies:
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise ConfigurationError("ALPACA_API_KEY and ALPACA_SECRET_KEY are required")
    storage_settings = StorageSettings.from_environment()
    config_dir = Path(os.environ.get("CONFIG_DIR", "config"))
    universe = load_universe_config(config_dir / "universe.yaml")
    strategy = load_strategy_config(config_dir / "strategy.yaml")
    clock = SystemClock()
    storage = S3Storage()
    service = IngestService(
        primary=AlpacaMarketDataSource(api_key, secret_key, feed=DataFeed.IEX),
        fallback=YFinanceMarketDataSource(),
        storage=storage,
        bronze_bucket=storage_settings.bronze_bucket,
        quarantine_bucket=storage_settings.quarantine_bucket,
    )
    calendar = TradingCalendar(
        TradingClient(api_key=api_key, secret_key=secret_key, paper=True)
    )
    return IngestDependencies(
        service=service,
        calendar=calendar,
        clock=clock,
        universe=universe,
        strategy=strategy,
        code_version=os.environ.get("CODE_VERSION", "unknown"),
    )


def _resolve_run_date(event: Mapping[str, object], clock: Clock) -> date:
    value = event.get("run_date")
    if value is None:
        return clock.now().astimezone(_EASTERN).date()
    if not isinstance(value, str):
        raise ConfigurationError("run_date must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigurationError("run_date must be an ISO date string") from exc


def _resolve_run_id(event: Mapping[str, object]) -> str:
    value = event.get("run_id")
    if value is None:
        return uuid4().hex
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("run_id must be a non-empty string")
    return value.strip()
