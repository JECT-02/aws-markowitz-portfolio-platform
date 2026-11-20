# ADR 0004 - Registro ECR en su propio root de Terraform

- **Estado:** aceptado (decisión del humano, 2026-11-19)
- **Registra:** el orden de despliegue entre los repositorios ECR y las Lambdas
  de contenedor, detectado como bloqueador no cubierto por `mistakes.md`
  (el hallazgo original ADR 0001 ya advertía que las imágenes deben existir
  antes de crear las funciones).

## Contexto

Los repositorios ECR vivían dentro de los módulos `pipeline`, `backtest` y
`api`, que también crean las Lambdas. El primer `apply` tenía que crear los
repositorios y, a la vez, las Lambdas que consumen imágenes **ya publicadas**
en ellos: imposible sin `-target` (prohibido por AGENTS.md 11.1 salvo
autorización) o sin un primer apply diseñado para fallar.

## Decisión

Nuevo módulo `infra/modules/registry` compuesto por un root propio
`infra/envs/registry` con su state (`registry/terraform.tfstate`). Orden de
despliegue:

1. `make tf-plan-registry` → `apply` (crea los 7 repositorios).
2. Construir y publicar las 7 imágenes con digest inmutable.
3. `make tf-plan ENV=dev` → `apply` (crea buckets, DynamoDB, Lambdas, API…).
4. Segundo apply de `dev` con `spa_allowed_origin`.

Los módulos de funciones ya no crean repositorios: reciben
`repository_urls` del módulo registry y fallan en plan con un mensaje claro
si falta la URL o el digest (`precondition`).

## Alternativas consideradas

- **-target puntual autorizado:** rechazado; es la excepción permanente que
  AGENTS prohíbe salvo caso excepcional, y este caso es el arranque normal.
- **Primer apply con fallo esperado (estado parcial + reintento):** rechazado;
  un despliegue diseñado para fallar rompe AGENTS.md 16 y complica la
  auditoría del estado.
- **Flag `deploy_functions`:** rechazado; un flag gobierna Lambdas, scheduler,
  state machine y rutas de API, y duplica la lógica de dependencias.

## Consecuencias

- Tres roots: `bootstrap`, `registry` y `dev` (más `prod` a futuro), cada uno
  con su state key; nada se comparte.
- El CI planifica `dev`; el plan de `registry` es un paso manual del humano
  (`make tf-plan-registry`), coherente con el resto de pasos de despliegue.
- Costo: los repositorios ECR son gratuitos con la capa gratuita; el ciclo de
  vida borra imágenes sin tag a los 14 días.
- Los `terraform test` de cada módulo pinnean que ninguna función crea sus
  repositorios y que el registry los cubre todos.
