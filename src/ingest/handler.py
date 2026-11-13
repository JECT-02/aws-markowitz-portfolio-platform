from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from alpaca.data.enums import DataFeed
from alpaca.trading.client import TradingClient

from common.clock import Clock, SystemClock
from common.config import (
    RuntimeSettings,
    StorageSettings,
    StrategyConfig,
    UniverseConfig,
    load_universe_config,
)
from common.env import load_env_file
from common.events import resolve_run_date, resolve_run_id
from common.logging import configure_logging, get_logger
from common.models import DataSource, RunMetadata
from common.secrets import SecretsSettings, SsmSecretProvider
from common.storage import S3Storage
from ingest.adapters.alpaca import AlpacaMarketDataSource
from ingest.adapters.yfinance import YFinanceMarketDataSource
from ingest.calendar import TradingCalendar
from ingest.core.service import IngestService

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
    run_date = resolve_run_date(event, deps.clock)
    if not deps.calendar.is_trading_day(run_date):
        logger.info("market closed", extra={"run_date": run_date.isoformat()})
        return {"status": "SKIPPED", "run_date": run_date.isoformat(), "reason": "market_closed"}
    metadata = RunMetadata(
        run_id=resolve_run_id(event),
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
    load_env_file(Path(".env"))
    configure_logging()
    return run_ingestion(event, _build_dependencies())


def _build_dependencies() -> IngestDependencies:
    runtime = RuntimeSettings.from_environment()
    secrets = SecretsSettings.from_environment()
    provider = SsmSecretProvider(region_name=runtime.aws_region)
    api_key = provider.get_secret(secrets.api_key_parameter)
    secret_key = provider.get_secret(secrets.secret_key_parameter)
    storage_settings = StorageSettings.from_environment()
    universe = load_universe_config(runtime.config_dir / "universe.yaml")
    strategy = StrategyConfig.from_environment()
    clock = SystemClock()
    storage = S3Storage(region_name=runtime.aws_region)
    service = IngestService(
        primary=AlpacaMarketDataSource(api_key, secret_key, feed=DataFeed.IEX),
        fallback=YFinanceMarketDataSource(),
        storage=storage,
        bronze_bucket=storage_settings.bronze_bucket,
        quarantine_bucket=storage_settings.quarantine_bucket,
    )
    calendar = TradingCalendar(TradingClient(api_key=api_key, secret_key=secret_key, paper=True))
    return IngestDependencies(
        service=service,
        calendar=calendar,
        clock=clock,
        universe=universe,
        strategy=strategy,
        code_version=runtime.code_version,
    )
