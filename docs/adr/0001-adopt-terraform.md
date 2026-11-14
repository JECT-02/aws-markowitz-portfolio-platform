# ADR-0001: Migrar la infraestructura de CDK a Terraform

## Estado

Aceptado (2026-11-13).

## Contexto

Las fases 1–5A del proyecto (datos, optimizador, pipeline, API/SPA, núcleo del
executor) se implementaron con **AWS CDK en Python** (`infra/stacks/*.py`,
`app.py`, aserciones CDK en `tests/infra`). Ese código nunca se desplegó: la
cuenta AWS aún no tiene credenciales configuradas, así que no existe estado,
ni recursos creados, ni dependencia operativa de CDK.

En noviembre de 2026 el humano actualizó `AGENTS.md` y añadió `PLAN.md`
(v2 del diseño), que fijan **Terraform como la única IaC del proyecto**
(requisito externo del proyecto, no preferencia técnica): §2 de AGENTS.md
("Terraform, único; NO CDK") y §13 de PLAN.md (estado remoto en S3 con
`use_lockfile`, módulos `infra/modules/*`, entornos `infra/envs/{dev,prod}`,
bootstrap manual).

## Decisión

Migrar toda la infraestructura de CDK a Terraform antes del primer despliegue,
siguiendo la estructura de AGENTS.md §3/§11 y PLAN.md §13:

- `infra/versions.tf`: pin de Terraform (≥ 1.10, por `use_lockfile`) y del
  provider `hashicorp/aws` (`~> 5`), con `.terraform.lock.hcl` commiteado.
- `infra/bootstrap/`: bucket de estado remoto (versionado, cifrado, acceso
  público bloqueado) y rol OIDC para CI; se aplica una sola vez y solo por el
  humano.
- Módulos en `infra/modules/` (data, pipeline, backtest, api, web,
  observability) y entorno dev en `infra/envs/dev/`; un solo estado por
  entorno, nunca compartido.
- Tests de infraestructura reescritos a `terraform test` + `tflint` +
  `checkov` (AGENTS.md §8.1/§11.3); el Makefile gana los targets
  `tf-fmt/tf-validate/tf-lint/tf-plan`.
- CDK se elimina por completo (stacks, `app.py`, `cdk.json`, dependencias y
  tests) en una rama final de limpieza; mientras coexisten, `make check`
  debe seguir en verde.

## Alternativas consideradas

1. **Mantener CDK y desplegar primero.** Descartada: viola el requisito
   externo del proyecto (AGENTS.md/PLAN.md) y dejaría la IaC elegida sin uso.
2. **Mantener ambas IaC indefinidamente.** Descartada: duplicación de
   código, estados divergentes y costo de mantenimiento doble.
3. **CloudFormation manual.** Descartada por AGENTS.md §2.

## Consecuencias

- **Positivas:** cumplimiento del requisito; planes revisables antes de
  aplicar; módulos reutilizables entre dev/prod; estado remoto con bloqueo
  nativo.
- **Negativas / costos:** ~1.5–2 semanas de trabajo hasta la paridad
  verificable; la paridad real de IAM/ARNs solo se valida con credenciales
  AWS; las aserciones CDK existentes se reescriben (no se portan 1:1).
- **Neutras:** el código Python de `src/` y `tests/` no cambia; solo la capa
  de infraestructura y sus pruebas.

## Nota de riesgo

Mientras CDK y Terraform coexisten (ramas intermedias de la migración),
Ningún comando `cdk deploy` ni `terraform apply` se ejecuta: el despliegue es
responsabilidad exclusiva del humano con credenciales reales
(AGENTS.md §14.2–14.3).
