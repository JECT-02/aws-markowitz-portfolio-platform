.PHONY: install format lint typecheck security test-unit test-property test-integration test-infra coverage check synth diff run-optimizer-local run-backtest-local build-optimizer

install:
	uv sync

format:
	uv run ruff format .

lint:
	uv run ruff check .

typecheck:
	uv run mypy --strict src

security:
	uv run bandit -r src
	uv run pip-audit

test-unit:
	uv run pytest tests/unit

test-property:
	uv run pytest tests/property

test-integration:
	uv run pytest tests/integration

test-infra:
	uv run pytest tests/infra

coverage:
	uv run pytest --cov=src --cov-report=term-missing --cov-fail-under=80

check: format lint typecheck security test-unit test-property test-integration test-infra coverage

synth:
	uv run cdk synth

diff:
	uv run cdk diff

run-optimizer-local:
	uv run python -m optimizer.local

run-backtest-local:
	uv run python -m backtest.local

build-optimizer:
	docker build -t portfolio-markowitz-optimizer src/optimizer
