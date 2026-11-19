# Plan de ramas QA — remediación de `mistakes.md`

- **Base:** `fix/qa-iam-least-privilege` (contiene 20 correcciones ya escritas, sin mergear a `main`).
- **Referencia:** `mistakes.md` (31 hallazgos `QA-001`…`QA-031`), auditado sobre `main` = `0e3bb76`.
- **Modo de trabajo:** solo teoría. No se despliega, no se ejecuta `terraform plan/apply`, no se construyen imágenes
  (bajarían la base de la red) y no se hace ninguna llamada a AWS ni a Alpaca. Todo se valida con `terraform validate`,
  `terraform test` (solo `plan`), `pytest` con `moto`/mocks, `ruff` y `mypy`.

## Regla transversal: "listo pero apagado"

Ninguna rama introduce valores reales de despliegue. Todo dato de infraestructura (digests de imagen, prefijo de
Cognito, origen de la SPA) pasa a ser una **entrada obligatoria o validada** del módulo, de modo que:

- `terraform validate` y `make check` pasan en verde **sin** variables de entorno.
- `terraform plan/apply` falla con un mensaje explícito hasta que el humano exporte los `TF_VAR_*`.
- No queda ningún valor por defecto que pueda apuntar a un artefacto inexistente o mutable.

## Estado de partida

| Estado | Hallazgos |
|---|---|
| Corregidos, **sin mergear** | QA-001, QA-004, QA-006, QA-007, QA-008, QA-009, QA-010, QA-011, QA-012, QA-015, QA-016, QA-018, QA-019, QA-020, QA-021, QA-024, QA-025, QA-028, QA-029, QA-031 |
| Corregido, **sin commitear** | QA-030 |
| Abiertos | QA-002, QA-003, QA-005, QA-013, QA-014, QA-017, QA-022, QA-023, QA-026, QA-027 |

## Orden de las ramas

| # | Rama | Hallazgo(s) | Severidad |
|---|---|---|---|
| 1 | `fix/qa-030-rerun-gold-artifact` | QA-030 | baja (código ya escrito) |
| 2 | `fix/qa-002-003-image-digests` | QA-002, QA-003 | crítica (bloquea el despliegue) |
| 3 | `fix/qa-005-027-deploy-variables` | QA-005, QA-027 | crítica / baja |
| 4 | `fix/qa-017-executor-timeout` | QA-017 | media |
| 5 | `fix/qa-022-coverage-core` | QA-022 | baja |
| 6 | `fix/qa-013-run-date-window` | QA-013 (reruns) | media |
| 7 | `fix/qa-026-base-image-digests` | QA-026 | baja (requiere paso humano) |
| 8 | `docs/qa-023-client-order-id` | QA-023 | baja |
| — | `fix/qa-014-proposal-prices` (**no creada**) | QA-014 | baja; requiere aprobación humana antes de tocar DynamoDB/executor |

---

## 1. `fix/qa-030-rerun-gold-artifact`

**Problema:** un rerun del mismo día genera un `proposal_id` nuevo y el artefacto
`gold/run_date=YYYY-MM-DD/proposal.json` quedaba con la propuesta vieja (primera escritura gana), de modo que
`GET /proposal` servía una propuesta desactualizada.

**Cambio:**

- `OptimizerService` acepta un `ProposalStatusReader` opcional (`read_proposal_status`).
- `_write_correctable` reemplaza el artefacto fechado **solo** si la propuesta anterior sigue en estado no final
  (`PENDING`/`NO_REBALANCE`) en la tabla de estado; si el estado es final o desconocido, no lo toca (fail closed).
- `DynamoPortfolioState.read_proposal_status` lee el ítem `PROPOSAL/{id}` y valida el tipo.
- `build_dependencies` comparte una sola instancia de estado entre el servicio y el handler.
- Pruebas: rerun con propuesta previa no ejecutada ⇒ se reemplaza; estados finales y estado desconocido ⇒ se conserva.

**Pruebas:** `pytest tests/unit/optimizer/test_service.py tests/unit/optimizer/test_handler.py`, `ruff`, `mypy --strict`.

**Aceptación:** el artefacto fechado refleja la última propuesta no ejecutada; ninguna propuesta ya ejecutada se
sobrescribe.

## 2. `fix/qa-002-003-image-digests` (crítica)

**Problema:** las URIs se arman como `${repository_url}:${digest}` (formato inválido: debe ser `@sha256:…`) y un
**único** digest se aplica a los 7 repositorios ECR, que son distintos.

**Cambio:**

- `infra/envs/dev`: `optimizer_image_tag` → `image_digests` (`map(string)`), una clave por repositorio
  (`ingest`, `clean`, `optimizer`, `monitor`, `backtest`, `reader`, `executor`).
- Módulos `pipeline`, `backtest`, `api`: reciben **su** digest (`image_digest`) y arman
  `${aws_ecr_repository.X.repository_url}@${var.image_digest}`.
- Validación `^sha256:[0-9a-f]{64}$` en cada módulo; `precondition` que enumere los repositorios faltantes del mapa.
- README: cómo obtener cada digest por repositorio y exportarlos como `TF_VAR_image_digests`.
- `terraform test`: la URI contiene `@sha256:` y la imagen de cada Lambda apunta a su propio repositorio.

**Pruebas:** `terraform fmt -check`, `terraform validate` (sin backend), `terraform test` en `pipeline`, `backtest`, `api`.

**Aceptación:** ninguna URI queda con `:` antes del digest; ningún módulo comparte el digest de otro repositorio.

## 3. `fix/qa-005-027-deploy-variables`

**Problema:** la variable de imagen tiene `default = ""` con validación de digest, así que un `plan` sin variables
falla con un error de validación confuso (o con `default` inválido), y el README sigue documentando
`OPTIMIZER_IMAGE_TAG`. Además el prefijo del dominio Cognito usa un valor genérico que puede estar ocupado.

**Cambio:**

- Variable de digests **sin** `default` (obligatoria) y `precondition` con mensaje accionable, de modo que
  `validate` pase siempre y `plan` explique exactamente qué exportar.
- `infra/envs/dev/terraform.tfvars.example` con la forma de `TF_VAR_image_digests` (sin valores reales).
- README: sección de despliegue coherente con las variables reales.
- `cognito_domain_prefix` obligatorio en el entorno (o derivado de `project-env` con validación de longitud/unicidad),
  documentando que es global por región.
- `terraform test` de que la validación rechaza `latest`, tag mutable y digest corto.

**Pruebas:** `terraform validate` sin variables, `terraform test` de `api`.

**Aceptación:** `validate` verde sin entorno; el mensaje de error de `plan` nombra la variable a exportar.

## 4. `fix/qa-017-executor-timeout`

**Problema:** la Lambda del executor tiene `timeout = 25` s pero el flujo espera fills con backoff y consulta precios
secuencialmente; puede agotar el tiempo y dejar la propuesta en estado intermedio.

**Cambio:** timeout de 60 s (y validación de `executor_execution_timeout_seconds` coherente con él), ajuste del
`terraform test` y del README si documenta el valor.

**Pruebas:** `terraform test` de `api`, `pytest tests/integration/test_executor_flow.py`.

**Aceptación:** el timeout configurado es mayor que el peor caso del polling; no cambia el flujo de órdenes
(sección 6 de `AGENTS.md` intacta).

## 5. `fix/qa-022-coverage-core`

**Problema:** el umbral de 90 % en `core/` existe como target `coverage-core` pero no se ejecuta en `make check` ni
en CI.

**Cambio:** medir primero la cobertura real; si cumple 90 %, añadir `coverage-core` a `check` y al workflow. Si no
cumple, añadir pruebas hasta cumplir (nunca bajar el umbral).

**Pruebas:** `make coverage-core`.

**Aceptación:** `make check` incluye el umbral y pasa.

## 6. `fix/qa-013-run-date-window`

**Problema:** ADR 0002 acepta que la ventana diaria incluya el día de ejecución, pero deja abierto que un **rerun o
backfill** de una fecha pasada lea particiones posteriores (look-ahead).

**Cambio:** `SilverReturnsRepository.load(..., as_of=None)` que excluye particiones posteriores a `run_date`; el
servicio pasa la fecha de la corrida. Nuevo **ADR 0003** que cierra el caso de reruns. Prueba: particiones futuras
no alteran la matriz de una corrida pasada.

**Pruebas:** `pytest tests/unit/optimizer/test_repository.py tests/unit/optimizer/test_service.py`,
`pytest tests/property`.

**Aceptación:** el resultado de un rerun depende solo de datos hasta su `run_date`; el régimen diario no cambia.

## 7. `fix/qa-026-base-image-digests`

**Problema:** los 7 Dockerfiles usan `FROM public.ecr.aws/lambda/python:3.12`, un tag mutable: la imagen base puede
cambiar entre builds y despliegues.

**Cambio:** fijar `FROM public.ecr.aws/lambda/python:3.12@sha256:<64 hex>` en los 7 Dockerfiles. Los digests los
aporta el humano (requiere red y `docker manifest inspect`); el agente deja la estructura y un script de verificación
en CI que falla si aparece `FROM` sin digest.

**Pruebas:** `pytest tests/infra` + un test que valide el patrón `FROM …@sha256:` en todos los Dockerfiles.

**Aceptación:** ningún Dockerfile con tag mutable; los digests quedan documentados junto a la fecha de verificación.

## 8. `docs/qa-023-client-order-id`

**Problema:** `AGENTS.md §6.9` documenta `client_order_id = {proposal_id}-{ticker}-{side}`, pero el código usa un
formato con hash y truncado al límite de Alpaca.

**Cambio:** ADR corto con el formato real (determinista, longitud máxima, colisiones) y actualización de `AGENTS.md`
§6.9. Sin cambios de código.

**Aceptación:** la documentación coincide con `src/executor/core/execution.py` y existe una prueba que fija el formato.

## 9. `fix/qa-014-proposal-prices` — NO creada (requiere aprobación)

**Problema:** se guarda `prices_ref` (un rango de fechas) pero el executor escribe `prices={}`, así que el contrato
de precios de referencia no sirve ni para auditoría ni para el control de drift.

**Propuesta:** persistir `{ticker: precio}` en el ítem de la propuesta y usarlo en el executor para el chequeo de
drift y la auditoría; renombrar el campo muerto.

**Bloqueo:** cambia el esquema de DynamoDB y el flujo del executor → `AGENTS.md §14.2` exige confirmación humana
explícita antes de implementarlo.

## Pasos humanos (fuera de las ramas)

1. Publicar las 7 imágenes en ECR y anotar el digest de cada una.
2. Resolver los digests de la imagen base (QA-026).
3. Mergear `fix/qa-iam-least-privilege` antes (o junto) con estas ramas, para que cada PR muestre solo su hallazgo.
4. Actualizar `AGENTS.md §17`, que hoy declara cerrado el ciclo QA mientras estos hallazgos siguen abiertos.
