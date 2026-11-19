ENV ?= dev

.PHONY: install format lint typecheck security test-unit test-property test-integration test-infra coverage coverage-core check tf-fmt tf-validate tf-lint tf-test tf-plan freeze run-optimizer-local run-backtest-local build-ingest build-clean build-images build-optimizer build-monitor build-backtest

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
	cd infra/modules/backtest && terraform test
	cd infra/modules/api && terraform test
	cd infra/modules/web && terraform test

coverage:
	uv run pytest --cov=src --cov-report=term-missing --cov-fail-under=80

# AGENTS.md §8.3 asks for 90% on the core/ of the modules that decide money or
# data. coverage.py has a single global fail_under, so the threshold is applied
# per module by tools/check_core_coverage.py over one run's JSON report;
# adding a module here also adds it to that file's --cov scope.
COVERAGE_CORE_MODULES := src/optimizer/core src/executor/core src/ingest/core src/backtest/core
COVERAGE_CORE_REPORT := build/coverage-core.json

coverage-core:
	mkdir -p build
	uv run pytest $(addprefix --cov=,$(COVERAGE_CORE_MODULES)) --cov-report=term-missing --cov-report=json:$(COVERAGE_CORE_REPORT)
	uv run python tools/check_core_coverage.py $(COVERAGE_CORE_REPORT)

check: format lint typecheck security test-unit test-property test-integration test-infra coverage coverage-core tf-fmt tf-validate tf-lint

tf-fmt:
	terraform fmt -check -recursive infra

tf-validate:
	cd infra/bootstrap && terraform init -backend=false && terraform validate
	cd infra/envs/dev && terraform init -backend=false && terraform validate

tf-lint:
	tflint --chdir infra --recursive
	trivy config --exit-code 1 --severity HIGH,CRITICAL infra

tf-test:
	cd infra/modules/data && terraform test
	cd infra/modules/pipeline && terraform test
	cd infra/modules/backtest && terraform test
	cd infra/modules/api && terraform test
	cd infra/modules/web && terraform test

tf-plan:
	cd infra/envs/$(ENV) && terraform init -backend-config=backend.hcl && terraform plan -out=tfplan

run-optimizer-local:
	uv run python -m optimizer.local

run-backtest-local:
	uv run python -m backtest.local

# L-04: the images install a fully pinned universe generated from uv.lock
# (direct + transitive), so two builds of the same revision are identical.
freeze:
	uv export --frozen --no-hashes --no-dev --no-emit-project -o build/requirements.lock.txt

build-ingest: freeze
	docker build -f src/ingest/Dockerfile -t portfolio-markowitz-ingest --platform linux/amd64 .

build-clean: freeze
	docker build -f src/clean/Dockerfile -t portfolio-markowitz-clean --platform linux/amd64 .

build-optimizer: freeze
	docker build -f src/optimizer/Dockerfile -t portfolio-markowitz-optimizer --platform linux/amd64 .

build-executor: freeze
	docker build -f src/executor/Dockerfile -t portfolio-markowitz-executor --platform linux/amd64 .

build-images: build-ingest build-clean build-optimizer build-reader build-executor build-monitor build-backtest

build-backtest: freeze
	docker build -f src/backtest/Dockerfile -t portfolio-markowitz-backtest --platform linux/amd64 .

build-monitor: freeze
	docker build -f src/monitor/Dockerfile -t portfolio-markowitz-monitor --platform linux/amd64 .

build-reader: freeze
	docker build -f src/reader/Dockerfile -t portfolio-markowitz-reader --platform linux/amd64 .
