# Portfolio Markowitz — Serverless en AWS (demo universitaria)

Proyecto **demostrativo**: muestra el funcionamiento principal de un pipeline
cuantitativo de principio a fin — cómo fluye el dato (Alpaca → S3 → Parquet),
cómo se aplica la matemática (Markowitz robustecido con CVXPY: Ledoit-Wolf,
shrinkage de μ, penalización de rotación L1) y cómo ese dato atraviesa el
pipeline serverless (Step Functions → Lambda → S3/DynamoDB → API → SPA).
El diseño completo está en `Architecture.md`; el manual operativo del
repositorio en `AGENTS.md`.

> **Alcance consciente:** proyecto universitario de demostración, **no
> producción**. Sin alarmas, sin alertas/email/SMS, sin budgets y sin entorno
> prod hasta que exista un ámbito empresarial. Todo el trading es **Alpaca
> paper** (`paper=True` forzado en la configuración): nunca opera dinero real.
> Nada de esto es consejo financiero.

## Estado de la implementación

Implementado y con su checklist de calidad en verde (`make check`):

- **Lazo lento (diario, 16:30 `America/New_York`):** Step Functions con
  ingest (Alpaca con respaldo yfinance y backoff ante 429/5xx) → cuarentena
  de datos inválidos → Silver Parquet por día → optimizador CVXPY → propuesta
  en Gold + DynamoDB + notificación SNS.
- **Lazo rápido (cada 15 min con mercado abierto):** monitor que calcula el
  drift del portafolio real y escribe la instantánea en DynamoDB (sin alertas,
  por decisión de alcance).
- **Lazo semanal:** backtest con validación temporal (holdout de un nivel,
  limitación documentada) y comparación contra 1/N, mínima varianza y SPY.
- **Ejecución:** API `POST /rebalance` con Cognito (JWT + grupo `executors`),
  idempotencia, verificación de mercado abierto, tope de drift con precios
  ajustados re-obtenidos y reconciliación contra las posiciones reales del
  broker. Órdenes primero de venta, luego de compra, con `client_order_id`
  determinista dentro del límite de Alpaca.
- **Servido:** Reader API (HTTP API + JWT) + SPA estática en CloudFront +
  conector comunitario de Looker Studio (Apps Script) sobre la Reader API.

## Requisitos

- Python 3.12 (fijado en `.python-version`)
- [uv](https://docs.astral.sh/uv/)
- Terraform ≥ 1.10 (fixado en `infra/`)
- Docker (imágenes de las Lambdas)
- Cuenta AWS con credenciales locales + rol OIDC de CI (ver `infra/bootstrap/`)
- Claves de Alpaca **paper trading** en SSM Parameter Store

## Comandos

Usa siempre `make` (los comandos son los del `Makefile`; no hay variantes):

| Acción | Comando |
|---|---|
| Instalar entorno | `make install` |
| Todo el chequeo de calidad | `make check` |
| Formato / lint / tipos | `make format` / `make lint` / `make typecheck` |
| Seguridad | `make security` |
| Pruebas unit / property / integration / infra | `make test-unit` / `test-property` / `test-integration` / `test-infra` |
| Cobertura (≥ 80 % global) | `make coverage` |
| Cobertura de los `core/` críticos (≥ 90 % por módulo) | `make coverage-core` |
| Terraform fmt / validate / lint | `make tf-fmt` / `make tf-validate` / `make tf-lint` |
| Terraform plan (solo lectura) | `make tf-plan ENV=dev` |
| Optimizador local | `make run-optimizer-local` |
| Backtest local | `make run-backtest-local` |
| Construir imágenes Docker | `make build-images` (una por módulo: `build-ingest`, `build-clean`, …) |

## Estructura del repositorio

```text
config/          universe.yaml (tickers y tope) + strategy.yaml (parámetros de riesgo)
infra/           Terraform: modules/{data,pipeline,backtest,api,web} + envs/dev + bootstrap/
src/             common/ (modelos Pydantic, almacenamiento, adaptadores, errores)
                 ingest/ clean/ optimizer/ monitor/ reader/ executor/ backtest/
                 (la lógica vive en cada core/, los handlers solo orquestan)
web/             SPA estática (Plotly.js)
tools/           conector comunitario de Looker Studio (Apps Script) + check de cobertura por módulo
tests/           unit/ property/ integration/ infra/ fixtures/
docs/            plan de diseño, bitácoras por fase
```

## Configuración de estrategia

Los parámetros de riesgo viven **solo** en `config/strategy.yaml` (γ, λ_tc, α,
ventana, umbral de rebalanceo 0.10, rf…); Terraform los traduce a variables de
entorno y `StrategyConfig` los valida con reglas cruzadas (coherencia del
lookback con la ventana y la validación). Los tickers y el tope por activo
(0.20) viven en `config/universe.yaml`.

## Despliegue (pasos del humano)

1. `infra/bootstrap` (una vez): bucket de estado + rol OIDC de CI.
2. Crea `infra/envs/dev/backend.hcl` y `infra/envs/registry/backend.hcl` con el
   nombre real del bucket de estado (`bucket = "<state_bucket_name>"`; el
   archivo está en `.gitignore`) y el repositorio con la variable
   `TERRAFORM_BACKEND_BUCKET` para el plan de CI.
3. Claves de Alpaca en SSM (`/portfolio-markowitz/dev/alpaca/...`).
4. Crea los repositorios ECR **antes** de publicar imágenes: el registro vive
   en su propio root porque los repos deben existir antes del push, y las
   Lambdas necesitan las imágenes ya publicadas (ADR 0004):

   ```bash
   make tf-plan-registry               # revisa el plan
   cd infra/envs/registry && terraform apply tfplan
   ```

5. Construir y publicar las 7 imágenes en su repositorio: `make freeze` corre
   solo antes de cada build; autentícate con `aws ecr get-login-password` y
   publica con digest inmutable (nunca `latest`), anotando **un digest por
   imagen** (`ingest`, `clean`, `optimizer`, `monitor`, `backtest`, `reader`,
   `executor`). Un digest solo existe dentro de su repositorio, así que no se
   puede reutilizar el del optimizador. La imagen base
   `public.ecr.aws/lambda/python:3.12` es un tag mutable: obtén su digest con
   `docker pull`/`docker inspect` y pínalo en los 7 Dockerfiles (L-16).
6. Exporta las dos variables obligatorias del entorno (no tienen valor por
   defecto, así que un `plan` sin ellas se detiene nombrando la que falta):

   ```bash
   export TF_VAR_image_digests='{"ingest"="sha256:...","clean"="sha256:...",...}'
   export TF_VAR_cognito_domain_prefix="portfolio-markowitz-dev-<tu-sufijo>"
   ```

   El prefijo de Cognito es único **por región**: si está ocupado, cambia el
   sufijo (QA-027). `infra/envs/dev/terraform.tfvars.example` muestra la forma
   de ambas variables sin valores reales.
7. `make tf-plan ENV=dev` revisado → `terraform apply` con ese plan.
8. Segundo apply para fijar `spa_allowed_origin` (CORS/callbacks de Cognito).
9. Crear tu usuario en Cognito y asignarlo al grupo `executors` (aprueba el
   rebalanceo en la demo vía API).

## Pruebas

`make check` ejecuta formato, lint, tipos, seguridad (bandit + pip-audit),
las tres pirámides de pruebas con mocks (cero llamadas reales a Alpaca/AWS),
cobertura, y los chequeos de Terraform (fmt, validate, tflint/trivy y
`terraform test` por módulo con providers simulados).
