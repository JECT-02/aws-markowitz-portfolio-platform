# Runbook — Portafolio Markowitz Serverless (demo)

Operación **manual y vigilada por el humano** (ADR 0001-recorte: sin alarmas,
sin budgets, sin SMS/email operativos). Este runbook cubre qué mirar y qué
hacer cuando algo falla. Todo el trading es Alpaca **paper**; nunca hay dinero
real en juego.

---

## 1. Dónde mirar

| Qué | Dónde |
|---|---|
| Logs de cada Lambda | CloudWatch `/aws/lambda/portfolio-markowitz-aws-<stage>-<env>` (retención 14 días) |
| Ejecuciones del pipeline | Step Functions → ejecuciones de la state machine diaria/monitor/backtest |
| Propuestas y estado | DynamoDB `PortfolioState`: `PROPOSAL/{id}`, `STATE/CURRENT`, `IDEMPOTENCY/{key}`, `MONITOR/{ts}` |
| Artefactos de datos | S3: `<project>-<env>-lakehouse-bronze` (crudo, inmutable), `…-quarantine` (rechazados), `…-silver` (Parquet), `…-gold` (`latest.json`, `run_date=…/proposal.json`) |
| Notificaciones | SNS `…-notifications` (resultado del pipeline y del rebalanceo; los rechazos también avisan) |
| Órdenes | Dashboard de Alpaca **paper**; buscar por `client_order_id` |

Correlación: cada log lleva `run_id`, `proposal_id`, `idempotency_key` y
`code_version` (SHA de git).

---

## 2. Fallos y qué hacer

| Situación | Señal | Acción |
|---|---|---|
| Datos inválidos | filas en `lakehouse-quarantine`, log `quarantined` | Revisar el motivo en el JSON de cuarentena; **no** se corrigen en silencio: si es un defecto del código, rama + test |
| API de datos caída / 429 | logs `ingest` con reintentos | El backoff con jitter y el respaldo yfinance actúan solos; si persiste, el día queda sin corrida: reintentar al día siguiente o re-invocar ingest con `run_date` del día |
| Solver falla / infactible | propuesta `FAILED` en DynamoDB, SNS `FAILED` | `STATE/CURRENT` (w_actual) no cambia. Revisar el log del solver (estado, activos válidos). Corregir la causa y dejar que la corrida siguiente proponga de nuevo. **Nunca** relajar restricciones a mano |
| Mercado cerrado | 409 `MarketClosed` | La propuesta sigue `PENDING`; reintentar `POST /rebalance` en horario de mercado |
| Drift de precios | 409 `PriceDriftExceeded` | Pedir una propuesta nueva (esperar la corrida diaria); no forzar la ejecución |
| Propuesta expirada | 409 `ProposalExpired` (24 h) | Esperar la propuesta del día siguiente |
| Orden abierta al vencer el deadline | resultado `UNRESOLVED` + SNS | Consultar la orden en Alpaca por `client_order_id` (`{hash12}-{symbol}-{side}`). **Nunca reenviar a ciegas.** Reintentar `POST /rebalance` con la **misma** `Idempotency-Key`: reanuda, sondea lo abierto y solo envía lo que falta |
| Lambda muerta a mitad de ejecución | claim `EXECUTING` sin resultado, reintento con 409 | Esperar `EXECUTOR_CLAIM_STALENESS_SECONDS` (90 s) y reintentar con la misma key: el takeover verifica cada orden en el broker antes de enviar nada |
| Fill parcial | `PARTIAL` + SNS | El `w_actual` ya quedó reconciliado desde las posiciones reales; decidir si se completa manualmente o se espera la propuesta siguiente |
| Orden rechazada | `FAILED` + SNS, detalle en log | Revisar el mensaje del broker en CloudWatch; causas típicas: fondos insuficientes en paper, símbolo no negociable |
| Secreto expuesto | — | Detener, rotar la credencial comprometida (Alpaca en SSM o AWS), avisar; no reescribir historial sin permiso |
| Despliegue fallido | error de `apply` | Leer el error completo. El orden correcto es: `infra/envs/registry` → publicar imágenes → `infra/envs/dev` (ADR 0004). No improvisar targets ni reintentos a ciegas |

---

## 3. Reglas fijas de operación

- **Nunca** enviar una orden sin idempotencia, mercado abierto y propuesta
  `PENDING` vigente: el executor lo valida, pero no se bypasea llamando al
  broker a mano.
- **Nunca** reintentar una orden con resultado desconocido sin consultar antes
  su estado por `client_order_id` (AGENTS.md §6.15).
- **Backfills: no hacer.** La ventana diaria incluye el retorno del propio día
  (ADR 0002); un rerun de una fecha pasada leería particiones más nuevas
  (look-ahead). Re-invocar el optimizador solo con la fecha del día.
- Los umbrales de riesgo (`0.20`, `0.10`, `gamma`, `lambda_tc`, `alpha`) viven
  solo en `config/`; cambiarlos requiere decisión humana explícita.
- Los reports de backtest se regeneran tras cualquier cambio de metodología
  (los benchmarks comparten ahora el umbral L1 del optimizador, QA-012).

---

## 4. Verificación manual diaria (demo)

1. Step Functions del día terminó `SUCCEEDED` antes del cierre.
2. `lakehouse-gold/latest.json` tiene la propuesta del día y coincide con
   `run_date=…/proposal.json`.
3. SNS llegó (o el pipeline falló con motivo claro).
4. Si se aprobó un rebalanceo: `STATE/CURRENT.reconciled_at` es posterior a la
   ejecución y las órdenes en Alpaca paper coinciden con `w_actual`.

---

## 5. Estado de la remediación

El registro vivo de los hallazgos de `mistakes.md` está en
`docs/qa_status.md`; los ADR de las desviaciones de diseño están en
`docs/adr/`.
