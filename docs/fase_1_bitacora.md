# Bitácora Fase 1 — Ingesta y capa Silver

Rama: `feat/fase-1-ingest`. Base: `main` (`867efdb`). Python 3.12, `uv`.

## Commits

1. `chore: add phase 1 runtime and typing dependencies` — `yfinance`, stubs de `boto3`, `pandas`, `PyYAML`; overrides de mypy para paquetes sin tipos.
2. `chore: add uv lockfile` — versiones exactas reproducibles.
3. `feat: add error hierarchy and json logging` — errores de dominio y logging JSON con `run_id`, `code_version`, etc.
4. `feat: add clock abstraction and config loaders` — reloj inyectable, carga validada de `universe.yaml` y `strategy.yaml`, buckets desde entorno.
5. `feat: add market data models and bar validation` — modelos Pydantic de barras, calendario, metadatos y cuarentena; validación OHLC, precios positivos, volumen y salto diario máximo (0.40 log).
6. `feat: add idempotent s3 storage adapter` — JSON y Parquet Snappy; escritura condicional (write-once) para Bronze.
7. `feat: add alpaca daily bar adapter` — barras diarias ajustadas (`adjustment=all`, feed IEX) y mapeo a payload canónico.
8. `refactor: expose canonical raw payloads from market data adapters` — los adapters entregan payload crudo canónico; el parseo a `BarSeries` ocurre en `ingest/core/parsing.py` para poder cuarentenar.
9. `feat: add yfinance fallback adapter` — descarga con `auto_adjust=True`, timestamps anclados a fecha de mercado (`America/New_York`).
10. `feat: add ingest service with fallback and quarantine` — Bronze write-once por símbolo/día, fallback por corrida completa, cuarentena con motivo (`no_data`, `invalid_data`) y metadatos de fuente.
11. `feat: add trading calendar client` — días hábiles desde Alpaca; mercado cerrado devuelve `SKIPPED`.
12. `feat: add ingest lambda handler with market calendar check` — orquestación, rango de ingesta `[run_date - lookback, run_date - 1]`, resumen de ejecución.
13. `feat: add aligned log return computation` — alineación por fecha de mercado, forward-fill acotado, exclusión con registro de símbolos con historia insuficiente, retornos logarítmicos.
14. `feat: add clean service and lambda handler` — lee Bronze, escribe Silver mensual en Parquet con metadatos de esquema (`run_id`, `run_date`, `code_version`, `source`); reporta ausentes y excluidos.
15. `test: add return computation property tests` — cálculo manual, finitud y ausencia de look-ahead (cambiar precios futuros no altera retornos pasados).
16. `test: add ingest and clean integration pipeline` — moto S3, ingesta y limpieza de extremo a extremo.
17. `style: apply ruff formatting`.

## Verificaciones ejecutadas

- `make format`: aplicado.
- `make lint`: sin errores.
- `make typecheck`: `mypy --strict src` sin errores (35 archivos).
- `make security`: `bandit` sin hallazgos; `pip-audit` sin vulnerabilidades conocidas.
- `make test-unit`: 83 pruebas en verde.
- `make test-property`: 3 pruebas en verde.
- `make test-integration`: 1 prueba en verde.
- `make coverage`: 87 pruebas, 89.87 % global; `ingest/core` entre 95 % y 100 %.
- `make test-infra`: no ejecutado con éxito; `tests/infra` está vacío porque no hay stacks CDK hasta la Fase 3 (`pytest` sale con código 5).
- `make synth` / `make diff`: no ejecutados; no existe app CDK todavía.

## No probado y límites

- No se hicieron llamadas reales a Alpaca ni a yfinance. Los adapters se probaron con clientes falsos y DataFrames sintéticos; S3 se probó con `moto`.
- No se creó ni desplegó infraestructura AWS.
- `max_forward_fill_days` sigue en `null` en `config/strategy.yaml`; el handler de limpieza falla explícitamente con `ConfigurationError` hasta que se defina. No se inventó un valor.
- Bronze guarda un payload canónico con timestamps ISO, no el JSON crudo del proveedor. Se registran `source`, `code_version`, `run_id` y `run_date`.
- El fallback de yfinance se aplica a la corrida completa si Alpaca falla; no se mezclan fuentes por símbolo. Los símbolos sin datos van a cuarentena.
- Forward-fill de precios implica retorno 0 en días rellenados; el límite se controla con `max_forward_fill_days`.
- Silver se reescribe por partición mensual; Bronze nunca se sobrescribe.

## Riesgos y pendientes

- `ingest_lookback_days` (420 por defecto) debe cubrir 252 días hábiles más festivos; revisar al conectar datos reales.
- Faltan CDK, Step Functions, SNS, DLQ, alarmas y pruebas de infraestructura (Fase 3).
- El optimizador, backtest, monitor y executor no forman parte de esta rama.
- La validación de drift de precios y la ejecución de órdenes quedan para fases posteriores.

## Invariantes respetadas

- `paper=True` forzado en el cliente de trading; esta rama no envía órdenes.
- Sin secretos en código, logs ni commits; credenciales solo desde entorno.
- Sin look-ahead: la ingesta termina en `run_date - 1` y los retornos usan únicamente datos pasados.
- Cuarentena explícita con motivo; no se corrigen datos en silencio.
- Topes de riesgo y parámetros de estrategia sin cambios.
