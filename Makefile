ENV ?= dev

.PHONY: install format lint typecheck security test-unit test-property test-integration test-infra coverage coverage-core check tf-fmt tf-validate tf-lint tf-test tf-plan run-optimizer-local run-backtest-local build-ingest build-clean build-images build-optimizer

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
	cd infra/modules/data && terraform test
	cd infra/modules/pipeline && terraform test
	cd infra/modules/api && terraform test
	cd infra/modules/web && terraform test

coverage:
	uv run pytest --cov=src --cov-report=term-missing --cov-fail-under=80

coverage-core:
	uv run pytest --cov=src/optimizer/core --cov=src/backtest/core --cov-report=term-missing --cov-fail-under=90

check: format lint typecheck security test-unit test-property test-integration test-infra coverage

tf-fmt:
	terraform fmt -check -recursive infra

tf-validate:
	cd infra/bootstrap && terraform init -backend=false && terraform validate
	cd infra/envs/dev && terraform init -backend=false && terraform validate

tf-lint:
	tflint --chdir infra --recursive
	trivy config --exit-code 1 --severity HIGH,CRITICAL --ignorefile infra/.trivyignore infra

tf-test:
	cd infra/modules/data && terraform test
	cd infra/modules/pipeline && terraform test
	cd infra/modules/api && terraform test
	cd infra/modules/web && terraform test

tf-plan:
	cd infra/envs/$(ENV) && terraform init && terraform plan -out=tfplan

run-optimizer-local:
	uv run python -m optimizer.local

run-backtest-local:
	uv run python -m backtest.local

build-ingest:
	docker build -f src/ingest/Dockerfile -t portfolio-markowitz-ingest .

build-clean:
	docker build -f src/clean/Dockerfile -t portfolio-markowitz-clean .

build-optimizer:
	docker build -f src/optimizer/Dockerfile -t portfolio-markowitz-optimizer .

build-images: build-ingest build-clean build-optimizer
