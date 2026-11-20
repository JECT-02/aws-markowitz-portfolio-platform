# Estado de `mistakes.md` (checklist de remediación)

Registro vivo de los 31 hallazgos de `mistakes.md`. Se actualiza en la misma rama que cierra cada hallazgo,
de modo que el archivo refleje lo mergeado y no lo prometido.

**Progreso:** 31 de 31 (QA-014 en esta rama, pendiente de merge).

| Leyenda | Significado |
|---|---|
| ✅ main | Corregido y mergeado en `main`. |
| 🔀 sin mergear | Corrección escrita y pusheada, falta el merge. |
| 🚧 esta rama | Corrección implementada en la rama actual, aún sin mergear. |
| ⏳ pendiente | Sin corregir; tiene rama planificada. |
| 🧑 decisión humana | Requiere aprobación explícita antes de tocar el esquema de DynamoDB o el flujo de órdenes. |

| # | Severidad | Hallazgo | Estado | Dónde |
|---|---|---|---|---|
| QA-001 | CRITICAL | La imagen `clean` no incluye el paquete `ingest` | ✅ main | PR #31 |
| QA-002 | CRITICAL | URI de imagen Lambda con sintaxis de digest inválida (`:` en vez de `@`) | ✅ main | PR #32 |
| QA-003 | CRITICAL | Un único digest reutilizado para siete repositorios ECR | ✅ main | PR #32 |
| QA-004 | CRITICAL | `get_adjusted_close` incompatible con `alpaca-py 0.45.0` | ✅ main | PR #31 |
| QA-005 | CRITICAL | `optimizer_image_tag` con `default = ""` rompía `plan/apply` | ✅ main | PR #33 |
| QA-006 | CRITICAL | URL del dominio alojado de Cognito incompleta | ✅ main | PR #31 |
| QA-007 | HIGH | El conector de Looker usaba APIs inexistentes de `CardService` | ✅ main | PR #31 |
| QA-008 | HIGH | El esquema `monitor` del conector referenciaba una variable indefinida | ✅ main | PR #31 |
| QA-009 | HIGH | La SPA lanzaba `TypeError` con métricas `null` | ✅ main | PR #31 |
| QA-010 | HIGH | Polling de órdenes a 10 Hz podía agotar la cuota de Alpaca | ✅ main | PR #31 |
| QA-011 | HIGH | Los fallos por excepción del executor no notificaban por SNS | ✅ main | PR #31 |
| QA-012 | MEDIUM | Los benchmarks del backtest no aplicaban el umbral de rebalanceo | ✅ main | PR #31 |
| QA-013 | MEDIUM | La carga de Silver ignoraba `run_date` (reruns con datos futuros) | ⏳ pendiente | rama `fix/qa-013-run-date-window` |
| QA-014 | LOW | Contrato de precios de la propuesta muerto y sin fallback | 🚧 esta rama | el optimizador persiste los precios en el ítem de DynamoDB (`prices`) y el executor los usa como fallback del drift; aprobado por el humano |
| QA-015 | MEDIUM | La reconciliación usaba precios anteriores a la ejecución | ✅ main | PR #31 |
| QA-016 | MEDIUM | Un timeout con estado desconocido se cacheaba como `FAILED` | ✅ main | PR #31 |
| QA-017 | MEDIUM | Timeout de 25 s de la Lambda del executor era ajustado | ✅ main | PR #34 |
| QA-018 | MEDIUM | `_fail` del conector usaba `.throw()` en vez de `.throwException()` | ✅ main | PR #31 |
| QA-019 | MEDIUM | El estado `PENDING_REVIEW` de Alpaca no estaba mapeado | ✅ main | PR #31 |
| QA-020 | MEDIUM | La escritura de `w_actual` era incondicional | ✅ main | PR #31 |
| QA-021 | MEDIUM | Un 404 de `/proposal` se renderizaba como propuesta | ✅ main | PR #31 |
| QA-022 | LOW | `make check` no aplicaba el umbral de 90 % en `core/` | 🚧 esta rama | rama `fix/qa-022-coverage-core` |
| QA-023 | LOW | `client_order_id` no seguía el formato literal de `AGENTS.md §6.9` | ⏳ pendiente | rama `docs/qa-023-client-order-id` |
| QA-024 | LOW | Código muerto en el repositorio del optimizador | ✅ main | PR #31 |
| QA-025 | LOW | Permiso `ssm:GetParameterHistory` innecesario en el executor | ✅ main | PR #31 |
| QA-026 | LOW | La imagen base de las Lambdas es un tag mutable | ⏳ pendiente | rama `fix/qa-026-base-image-digests` (necesita los digests reales) |
| QA-027 | LOW | El prefijo de Cognito era global con valor por defecto | ✅ main | PR #33 |
| QA-028 | LOW | Los handlers aceptaban `mixed` como fuente de evento | ✅ main | PR #31 |
| QA-029 | LOW | La SPA no mostraba los resultados del backtest | ✅ main | PR #31 |
| QA-030 | LOW | Un rerun del mismo día dejaba el artefacto Gold inconsistente | 🔀 sin mergear | rama `fix/qa-030-rerun-gold-artifact` (PR pendiente) |
| QA-031 | LOW | El flujo OAuth de la SPA no usaba el parámetro `state` | ✅ main | PR #31 |

## Rama en curso

`fix/qa-022-coverage-core` — QA-022: `coverage-core` entra en `make check` y en el workflow de CI, cubre los
cuatro `core/` críticos (optimizer, executor, ingest y backtest) y aplica el 90 % **por módulo** con
`tools/check_core_coverage.py`, porque `coverage.py` solo admite un umbral global y el promedio de los cuatro
dejaría pasar la regresión de uno pequeño. Estado medido al escribir esto: optimizer 92.19 %, executor 94.99 %,
ingest 98.44 %, backtest 95.50 %.

## Pendientes, en orden

1. `fix/qa-013-run-date-window` — QA-013.
2. `fix/qa-026-base-image-digests` — QA-026 (los digests de la imagen base los aporta el humano).
3. `docs/qa-023-client-order-id` — QA-023 (solo documentación y una prueba del formato).
4. `fix/qa-014-proposal-prices` — cerrada en esta rama (opción a del audit: precios en DynamoDB + fallback del drift).

## Pasos del humano

1. Mergear `fix/qa-030-rerun-gold-artifact`, que sigue sin PR.
2. Publicar las 7 imágenes en ECR y anotar el digest de cada una.
3. Resolver y fijar los digests de la imagen base (QA-026).
