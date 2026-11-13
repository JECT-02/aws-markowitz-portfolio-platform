from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import numpy as np
import pytest

from common.config import StrategyConfig, UniverseConfig
from common.errors import DataValidationError, InfeasibleError
from common.models import DataSource, RunMetadata
from optimizer.core.repository import ReturnsMatrix
from optimizer.core.service import OptimizerService

SYMBOLS = ["SPY", "QQQ", "IWM", "EFA", "TLT", "GLD"]


class _Clock:
    def now(self) -> datetime:
        return datetime(2025, 1, 2, 21, 30, tzinfo=UTC)

    def today(self) -> date:
        return self.now().date()


class _Repository:
    def __init__(self, matrix: ReturnsMatrix) -> None:
        self._matrix = matrix
        self.calls: list[tuple[list[str], int]] = []

    def load(self, symbols: list[str], window_days: int) -> ReturnsMatrix:
        self.calls.append((symbols, window_days))
        return self._matrix

    def raise_load_error(self, message: str) -> None:
        raise DataValidationError(message)


class _Storage:
    def __init__(self) -> None:
        self.json_objects: dict[tuple[str, str], dict[str, Any]] = {}

    def exists(self, bucket: str, key: str) -> bool:
        return (bucket, key) in self.json_objects

    def write_json_if_absent(self, bucket: str, key: str, payload: dict[str, Any]) -> bool:
        if self.exists(bucket, key):
            return False
        self.write_json(bucket, key, payload)
        return True

    def write_json(self, bucket: str, key: str, payload: dict[str, Any]) -> None:
        self.json_objects[(bucket, key)] = payload

    def read_json(self, bucket: str, key: str) -> dict[str, Any]:
        return self.json_objects[(bucket, key)]

    def write_parquet(self, bucket: str, key: str, obj: Any) -> None:
        raise NotImplementedError

    def read_parquet(self, bucket: str, key: str) -> Any:
        raise NotImplementedError

    def list_keys(self, bucket: str, prefix: str) -> list[str]:
        return sorted(key for bucket_name, key in self.json_objects if bucket_name == bucket)


def _matrix(symbols: list[str] = SYMBOLS) -> ReturnsMatrix:
    generator = np.random.default_rng(5)
    observations = 80
    values = generator.normal(0.0005, 0.008, size=(observations, len(symbols)))
    values[10, :] = -0.02
    values[30, :] = -0.015
    values[50, :] = -0.01
    dates = [date(2024, 9, 1) for _ in range(observations)]
    return ReturnsMatrix(
        symbols=list(symbols),
        dates=dates,
        values=values,
        excluded={},
    )


def _service(
    matrix: ReturnsMatrix | None = None,
    symbols: list[str] = SYMBOLS,
) -> tuple[OptimizerService, _Repository, _Storage, StrategyConfig]:
    repository = _Repository(matrix if matrix is not None else _matrix(symbols))
    storage = _Storage()
    strategy = StrategyConfig.model_validate(
        {
            "risk_aversion": 2.0,
            "transaction_cost_lambda": 0.001,
            "return_shrinkage_alpha": 0.5,
            "risk_free_rate_annual": 0.04,
            "max_forward_fill_days": 2,
            "window_days": 60,
            "ingest_lookback_days": 420,
            "rebalance_l1_threshold": 0.10,
            "validation_gammas": [1.0, 2.0],
            "validation_lambdas": [0.0, 0.001],
            "validation_alphas": [0.0, 0.5],
            "validation_windows": [126, 252],
            "validation_folds": 3,
        }
    )
    service = OptimizerService(
        repository=repository,
        storage=storage,
        gold_bucket="gold",
        universe=UniverseConfig(tickers=symbols, max_weight=0.20),
        strategy=strategy,
        clock=_Clock(),
    )
    return service, repository, storage, strategy


def _metadata() -> RunMetadata:
    return RunMetadata(
        run_id="run-1",
        run_date=date(2025, 1, 2),
        code_version="test",
        source=DataSource.ALPACA,
        fetched_at=datetime(2025, 1, 2, 21, 0, tzinfo=UTC),
    )


def test_service_writes_proposal_and_latest() -> None:
    service, repository, storage, strategy = _service()
    proposal = service.run(_metadata())
    assert repository.calls == [(SYMBOLS, strategy.window_days)]
    assert proposal.proposal_id == "2025-01-02-run-1"
    assert proposal.status in {"PENDING", "NO_REBALANCE"}
    assert sum(proposal.weights.values()) == pytest.approx(1.0, abs=1e-6)
    assert set(proposal.metrics) == {"optimizer", "equal_weight", "minimum_variance", "spy"}
    assert proposal.drift_l1 == pytest.approx(
        sum(abs(proposal.weights[s] - proposal.actual_weights[s]) for s in SYMBOLS)
    )
    assert ("gold", "run_date=2025-01-02/proposal.json") in storage.json_objects
    assert ("gold", "latest.json") in storage.json_objects
    assert (
        storage.json_objects[("gold", "latest.json")]
        == storage.json_objects[("gold", "run_date=2025-01-02/proposal.json")]
    )


def test_service_uses_provided_actual_weights() -> None:
    service, _, _, _ = _service()
    actual = {symbol: 1.0 / len(SYMBOLS) for symbol in SYMBOLS}
    proposal = service.run(_metadata(), actual)
    assert proposal.actual_weights == pytest.approx(actual)
    assert proposal.rebalance_required == (proposal.drift_l1 > 0.10)


def test_service_requires_five_assets() -> None:
    symbols = ["SPY", "QQQ", "IWM", "EFA"]
    service, _, _, _ = _service(_matrix(symbols), symbols)
    with pytest.raises(InfeasibleError):
        service.run(_metadata())


def test_service_rejects_actual_weights_that_do_not_sum_to_one() -> None:
    service, _, _, _ = _service()
    with pytest.raises(DataValidationError):
        service.run(_metadata(), {"SPY": 0.5})


def test_service_rejects_negative_actual_weights() -> None:
    service, _, _, _ = _service()
    actual = {symbol: 1.0 / len(SYMBOLS) for symbol in SYMBOLS}
    actual["SPY"] = -0.1
    actual["QQQ"] += 0.1
    with pytest.raises(DataValidationError):
        service.run(_metadata(), actual)


def test_service_requires_spy_benchmark() -> None:
    symbols = ["QQQ", "IWM", "EFA", "TLT", "GLD", "VNQ"]
    service, _, _, _ = _service(_matrix(symbols), symbols)
    with pytest.raises(DataValidationError):
        service.run(_metadata())
