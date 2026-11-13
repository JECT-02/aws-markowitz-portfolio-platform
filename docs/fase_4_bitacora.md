# Fase 4 — Bitácora

## Rama `feat/api-stack`: Cognito + HTTP API + Reader (2026-11-13)

Cuatro commits atómicos:

1. **Imagen del reader**: `src/reader/Dockerfile` (base lambda python 3.12, usuario
   no root, `CMD ["reader.handler.handler"]`) y `requirements.txt` con solo las
   dependencias alcanzables (`pyarrow`, `pydantic`, `PyYAML`, versiones fijadas a
   `uv.lock`). `tests/infra/test_requirements.py` ampliado al reader.
2. **DLQ opcional**: `container_function(dlq=None)`; la Lambda Reader es síncrona
   (API Gateway) y la DLQ solo aplica a invocaciones asíncronas.
3. **Cognito**: `UserPool` (sign-in email, auto-registro deshabilitado, MFA
   opcional con TOTP, password policy 12+), `UserPoolClient` público sin secret,
   solo SRP/refresh, OAuth deshabilitado hasta la rama `feat/web-spa` (CDK por
   defecto habilitaría implicit+code con callback example.com; se apagó
   explícitamente).
4. **HTTP API**: `HttpApi` con autorizador JWT de Cognito
   (`HttpUserPoolAuthorizer`, identity source `Authorization`), 4 rutas GET
   (`/proposal`, `/metrics`, `/monitor`, `/backtest`) protegidas, throttling
   rate 10/s burst 5, IAM mínimo (s3:GetObject sobre gold/*, dynamodb:GetItem
   sobre la tabla), memoria 256 MB, timeout 10 s, logs 3 días, sin DLQ.
   Nueva variable `SPA_ALLOWED_ORIGIN` (vacía): al definirla se configura CORS
   restringido a ese origen; sin ella no hay CORS (no hay SPA todavía).

### Decisiones técnicas

- En esta versión de `aws-cdk-lib`, `HttpAuthorizer` no implementa `bind`; el
  autorizador correcto para rutas es `HttpUserPoolAuthorizer` del módulo
  `aws_apigatewayv2_authorizers`.
- El throttling se aplica mutando el `CfnStage` del stage `$default`
  (`DefaultRouteSettings`); crear un `HttpStage` explícito duplica el stage.
- El reader requiere `CONFIG_DIR` y los 4 nombres de bucket por los contratos
  existentes de `RuntimeSettings`/`StorageSettings` aunque no los usa; se
  inyectan por entorno sin refactor.
- La imagen del reader no se pudo construir localmente: el daemon de Docker no
  estaba corriendo. La verificación de dependencias fue estática (imports
  alcanzables) y los pins contra `uv.lock`; pendiente verificar el build cuando
  Docker esté activo.

### Costo estimado mensual

- Cognito 1 MAU: $0 (capa gratuita).
- HTTP API + Lambda 256 MB/10 s para unas decenas de llamadas diarias: $0
  dentro del free tier.
- ECR reader repo: ~$0 (una imagen pequeña).


## Punto de partida: correcciones teóricas (`fix/invariantes-teoricos`)

Rama creada desde `main` (`d81ff11`) tras una revisión completa del código contra `AGENTS.md`.
Todo el trabajo es teórico: no se ejecutó nada contra AWS ni contra Alpaca.

### Problemas encontrados y corregidos

1. **Clean ignoraba la cuarentena de Ingest (crítico, §5.1/§7.3/§16).**
   Ingest escribía a Bronze antes de validar y Clean leía Bronze sin saber qué
   símbolos fueron cuarentenados, sin re-validar. Corregido en tres capas:
   - `CleanService` re-ejecuta `validate_bar_series` al cargar cada payload de
     Bronze; los símbolos inválidos quedan excluidos con motivo `invalid_data`.
   - `run_clean` filtra el universo por `accepted` cuando el evento lo trae; si
     llega vacío falla con `DataValidationError`.
   - El payload de Clean en Step Functions ahora incluye `accepted.$: $.accepted`.
2. **La validación de hiperparámetros no era anidada (§5.6).**
   Sustituido el split único 60/20/20 por folds expanding-window dentro del
   periodo de validación: cada combinación se puntúa con el Sharpe medio sobre
   `VALIDATION_FOLDS` folds (nueva variable de entorno, llamada aunque esté
   vacía en `.env`). El periodo de test permanece intacto.
3. **Retorno de portafolio calculado como media ponderada de retornos log
   (sesgo de Jensen).**
   Nueva función `portfolio_log_returns`: agregación en espacio simple
   (Σwᵢ(e^{rᵢ}−1)) y conversión exacta a log. Usada por el optimizador y por el
   backtest. Sharpe/Sortino ahora restan la tasa libre de riesgo como log diario
   (`ln(1+rf)/252`), eliminando la mezcla de convenciones.
4. **Menores.**
   - Curva SPY del backtest arranca en la misma fecha mensual que los demás modos.
   - `Proposal.prices_ref` añadido y persistido en DynamoDB (§7.2).
   - Gold escribe `proposal.json`/`latest.json` de forma condicional
     (gana el primer run), coherente con la escritura condicional de DynamoDB.
   - `ignore_dpp=True` silencia el aviso conocido de CVXPY (el problema usa
     γ/λ como parámetros, por lo que no es DPP).
   - El log de `optimal_inaccurate` usa `INACCURATE_TOLERANCE` en vez de un número.
   - `optimizer/handler` usa un único `SystemClock`.

### Lo que NO se corrigió (queda documentado)

- Cuarentena a nivel de serie (un día con |r|>0.40 cuarentena el símbolo
  completo): interpretación fail-closed deliberada, pendiente de ADR si se quiere
  cuarentena por día.
- `STATE/CURRENT` sin `reconciled_at`/`account_value` y sin particiones
  MONITOR/IDEMPOTENCY: pertenecen a las fases 5–6 (Executor/Monitoreo).
- `CheckMarketCalendar` sigue implementado dentro de Ingest (SKIPPED→Choice):
  equivalente funcional del diagrama, no se consideró defecto.
- `cp: Any = cvxpy` se mantiene: los stubs de CVXPY están incompletos para
  `mypy --strict`.

### Verificación ejecutada

- `pytest tests/unit tests/property tests/integration tests/infra`: en verde.
- `ruff check`, `mypy --strict src`: sin errores.
- Cobertura ≥ 80 %.
- No se ejecutó: `bandit`/`pip-audit` (pendiente de `make check` final),
  `cdk synth`/`diff`, ni ninguna llamada real de red.
