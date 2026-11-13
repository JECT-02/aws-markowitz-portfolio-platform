from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, NoReturn, Protocol

from pydantic import BaseModel

from common.config import RuntimeSettings, StorageSettings
from common.env import load_env_file
from common.errors import ConfigurationError, DataValidationError, NotFoundError, StorageError
from common.logging import configure_logging, get_logger
from common.storage import S3Storage
from optimizer.core.models import PortfolioMetrics, Proposal
from reader.core.models import MonitorSnapshot
from reader.core.repository import DynamoCurrentState, S3GoldRepository, StateReader
from reader.core.service import ReaderService

logger = get_logger(__name__)

_CONTENT_TYPE = "application/json"
_PROPOSAL_ROUTE = "GET /proposal"
_METRICS_ROUTE = "GET /metrics"
_MONITOR_ROUTE = "GET /monitor"
_BACKTEST_ROUTE = "GET /backtest"


@dataclass(frozen=True)
class ReaderDependencies:
    service: ReaderRunner


class ReaderRunner(Protocol):
    """Read-only API contract consumed by the handler."""

    def get_proposal(self, run_date: date | None = None) -> Proposal: ...

    def get_metrics(self, run_date: date | None = None) -> dict[str, PortfolioMetrics]: ...

    def get_monitor(self) -> MonitorSnapshot: ...

    def get_backtest(self) -> NoReturn: ...


def handler(event: Mapping[str, Any], _context: object | None = None) -> dict[str, Any]:
    """AWS Lambda entry point behind the read-only HTTP API."""
    load_env_file(Path(".env"))
    configure_logging()
    deps = build_dependencies()
    return run_reader(event, deps)


def run_reader(event: Mapping[str, Any], deps: ReaderDependencies) -> dict[str, Any]:
    """Route one HTTP API v2 event to the read service.

    Precondition: ``event`` follows the API Gateway HTTP API v2 payload.
    Postcondition: returns a Lambda proxy response; client errors map to 400 or
    404 with safe messages and unexpected failures log context and return 500.
    """
    route = event.get("routeKey")
    try:
        run_date = _parse_run_date(event.get("queryStringParameters"))
        if route == _PROPOSAL_ROUTE:
            payload: Any = deps.service.get_proposal(run_date)
        elif route == _METRICS_ROUTE:
            payload = {"metrics": deps.service.get_metrics(run_date)}
        elif route == _MONITOR_ROUTE:
            payload = deps.service.get_monitor()
        elif route == _BACKTEST_ROUTE:
            deps.service.get_backtest()
        else:
            raise NotFoundError(f"no route for {route}")
    except NotFoundError as exc:
        return _response(404, {"message": str(exc)})
    except DataValidationError as exc:
        return _response(400, {"message": str(exc)})
    except (StorageError, ConfigurationError):
        logger.exception("reader request failed", extra={"route": route})
        return _response(500, {"message": "internal error"})
    except Exception:
        logger.exception("unexpected reader failure", extra={"route": route})
        return _response(500, {"message": "internal error"})
    logger.info(
        "reader request served",
        extra={"route": route, "run_date": run_date.isoformat() if run_date else None},
    )
    return _response(200, _jsonable(payload))


def build_dependencies() -> ReaderDependencies:
    runtime = RuntimeSettings.from_environment()
    storage_settings = StorageSettings.from_environment()
    storage = S3Storage(region_name=runtime.aws_region)
    service = ReaderService(
        gold=S3GoldRepository(storage, storage_settings.gold_bucket),
        state=_build_state(runtime),
    )
    return ReaderDependencies(service=service)


def _build_state(runtime: RuntimeSettings) -> StateReader | None:
    table_name = os.environ.get("STATE_TABLE")
    if table_name is None or not table_name.strip():
        logger.warning("STATE_TABLE not set, monitor will fall back to proposal weights")
        return None
    return DynamoCurrentState(table_name.strip(), region_name=runtime.aws_region)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _parse_run_date(parameters: Mapping[str, Any] | None) -> date | None:
    if parameters is None:
        return None
    raw = parameters.get("run_date")
    if raw is None or not str(raw).strip():
        return None
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError as exc:
        raise DataValidationError("run_date must be an ISO date (YYYY-MM-DD)") from exc


def _response(status_code: int, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": _CONTENT_TYPE},
        "body": json.dumps(dict(payload), default=str),
    }
