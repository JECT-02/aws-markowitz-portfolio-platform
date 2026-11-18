# ADR 0001 — Recorte de alcance del proyecto demo y desviaciones del diseño original

- **Estado:** aceptado
- **Fecha:** 2026-11-18
- **Decisión de:** el humano (propietario del proyecto)
- **Registra:** las desviaciones conscientes entre el diseño original
  (`Architecture.md` v2 y `docs/plan_markowitz_aws_serverless.md`) y el
  sistema realmente construido.

## Contexto

El proyecto es una **demostración universitaria** cuyo objetivo es mostrar el
funcionamiento principal: el flujo del dato (Alpaca → S3 → Parquet), la
matemática (Markowitz robustecido con CVXPY) y el pipeline serverless.
No hay ámbito empresarial ni producción. El presupuesto objetivo es $0/mes.

El diseño original contemplaba elementos operativos empresariales que no
aportan al objetivo demostrativo y que tienen costo o complejidad adicional.

## Decisión

1. **Sin observabilidad operativa:** no hay alarmas CloudWatch, alertas SNS
   del monitor, budgets, SMS, email ni entorno `prod`. El monitor escribe su
   instantánea en DynamoDB y nada más. El SNS se conserva únicamente para la
   notificación del resultado del pipeline (ya implementado). Este recorte es
   **permanente hasta que exista ámbito empresarial**.
2. **IaC = Terraform**, no AWS CDK: estado remoto con lockfile, `terraform
   test` por módulo y análisis estático (tflint/trivy). Único motor de IaC.
3. **Backtest con validación temporal de un nivel** (holdout con folds de
   ventana expansiva), no anidada de dos niveles: el presupuesto de cómputo
   de la Lambda semanal (900 s) lo descarta; la limitación está documentada
   en el código y en el reporte.
4. **BI = conector comunitario de Looker Studio** (Apps Script) sobre la
   Reader API, en lugar de RDS/Redshift/Athena (vetados por costo; Looker
   Studio no tiene conector nativo a S3/DynamoDB).
5. **Aprobación del rebalanceo vía API** (`POST /rebalance` con JWT de
   Cognito + grupo `executors`): el botón en la SPA queda deferido; en la
   demo la aprobación es manual.
6. **Benchmarks comparados:** 1/N, mínima varianza y SPY (risk parity del
   diseño no se implementó).

## Alternativas consideradas

- Mantener alarmas/budgets: rechazado — costo (~$5-6/mes solo el WAF y las
  alarmas) y complejidad sin valor demostrativo.
- Validación anidada completa: rechazada — cientos de optimizaciones por
  corrida sin checkpoint; riesgo de timeout con el mismo resultado honesto.
- RDS/Redshift para BI: rechazado — prohibidos por costo en AGENTS §11.2.

## Consecuencias

- La operación de la demo es manual y vigilada por el humano: sin redes de
  seguridad automáticas. Aceptado explícitamente.
- Cualquier endurecimiento futuro (alarmas, prod, multiusuario) requiere una
  decisión humana y probablemente un ADR nuevo.
- El resto de decisiones técnicas sigue regido por `AGENTS.md`.
