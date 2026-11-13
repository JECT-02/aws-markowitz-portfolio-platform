# Fase 4 — Bitácora

## Rama `feat/web-spa`: SPA estática + CloudFront + PKCE (2026-11-13)

Cuatro commits atómicos:

1. **Activos SPA** (`web/`): `index.html` (secciones proposal/metrics/monitor/
   backtest, botones login/logout), `styles.css` (tema oscuro, clases de
   estado), `app.js` (login PKCE S256 con `crypto.subtle`, intercambio de code
   en `oauth2/token`, tokens solo en `sessionStorage`, `apiGet` con manejo
   401/404, gráfico Plotly de pesos propuestos vs actuales) y `config.js` con
   placeholders `{{ API_ENDPOINT }}`/`{{ COGNITO_DOMAIN }}`/`{{ CLIENT_ID }}`.
   Plotly fijado por CDN a la versión 2.35.2. Sintaxis verificada con
   `node --check`; no hay pytest para JS estático (documentado como riesgo).
2. **Dominio Cognito + PKCE condicional**: `UserPoolDomain` con prefijo
   determinista `{project}-{env}` en `ApiStack`. El OAuth del cliente web pasa
   a ser condicional: si `SPA_ALLOWED_ORIGIN` está definida se habilita solo
   authorization code grant (scopes openid/email/profile) con callback y
   logout en ese origen; sin ella sigue deshabilitado (tests de ambas ramas).
3. **WebStack**: bucket S3 privado versionado (`secure_bucket`), distribución
   CloudFront con OAC, `REDIRECT_TO_HTTPS`, error responses 403/404 →
   `index.html` (SPA routing), `BucketDeployment` de los activos (excluyendo
   `config.js`) con invalidación `/*`, y `DeployTimeSubstitutedFile` que
   sustituye los placeholders de `config.js` en deploy-time con los outputs de
   `ApiStack` (endpoint, dominio Cognito, client id). `config.js` se sirve con
   `CACHING_DISABLED` para que siempre refleje el último deploy. Outputs:
   `SpaUrl` y `CognitoDomain`.
4. **Bitácora** (este documento).

### Corrección adicional (pre-existente)

- `test_large_transaction_cost_keeps_actual_weights` fallaba en `main` con un
  ejemplo de hypothesis (5 activos, seed 746): Ledoit-Wolf produce una
  covarianza degenerada (identidad escalada) y λ=1e7 hace el QP
  numéricamente intractable para CLARABEL (~10 órdenes de magnitud entre el
  término L1 y μ). El invariante λ→∞ se verifica con λ=1e4, que ya fija
  w=w_actual (domina μ por ~1e7). La lógica del optimizador no cambió; el
  fallo de solver en producción sigue siendo fail-closed (propuesta FAILED).

### Decisiones técnicas

- `DeployTimeSubstitutedFile` usa placeholders `{{ KEY }}` (doble llave con
  espacios), no `@@KEY@@`; su `source` es una ruta plana, no un
  `IS3DeploymentSource`. Ambos despliegues emiten recursos
  `Custom::CDKBucketDeployment`.
- Orden de commits: primero el dominio Cognito (el WebStack referencia
  `user_pool_domain`), para que cada commit sintetice en verde.
- Activación de la SPA en dos pasos (documentada en el plan): deploy con
  `SPA_ALLOWED_ORIGIN` vacía → tomar `SpaUrl` del output → definir
  `SPA_ALLOWED_ORIGIN=https://<distribution>` → redeploy (activa CORS y
  callbacks PKCE). El `.env` local ya trae la variable vacía.

### Costo estimado mensual

- CloudFront: ~$0 (free tier, tráfico mínimo).
- S3: ~$0 (unos KB, storage gateway no aplica).
- Invalidaciones: incluidas en el free tier (1000/mes).

### Verificación ejecutada

- `make check` EXIT=0: 308 pruebas en verde, cobertura 89.25 %.
- `make synth`: 4 stacks sintetizados incluido `portfolio-markowitz-web-dev`.
- `node --check web/app.js`: sin errores de sintaxis.
- No se ejecutó: `cdk diff` (requiere credenciales AWS), build Docker,
  despliegue, ni ninguna llamada real de red.

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


## Rama `feat/executor-core`: fase 5A - flujo de ejecución con idempotencia (2026-11-13)

Tres commits atomicos sobre `main` (`e81f11c`):

1. **Errores y configuracion** (`7df97ef`): `BrokerError`, `ProposalExpired`,
   `ProposalNotPending`, `MarketClosed`, `PriceDriftExceeded` en
   `common/errors`; `ExecutorConfig` (env `EXECUTOR_MIN_ORDER_NOTIONAL`,
   `EXECUTOR_MAX_PRICE_DRIFT_PCT`, `EXECUTOR_EXECUTION_TIMEOUT_SECONDS`,
   en `.env` vacias) con helpers de parsing refactorizados
   (`_parse_number/_parse_integer(value, env_name)`).
2. **Nucleo puro** (`aa38fcb`): `models.py` (OrderSnapshot, PlannedOrder,
   FilledOrder, Reconciliation, ExecutionResult, ProposalRecord,
   IdempotencyClaim), `broker.py` (protocol BrokerPort: submit_order y
   get_order_status devuelven snapshots), `simulated.py` (SimulatedBroker
   determinista con partial_fill_ratio/reject_symbols y
   client_order_id deduplicado), `planning.py` (ventas antes que compras,
   polvo >= min_order_notional, client_order_id determinista
   {proposal_id}-{SYMBOL}-{side}) y `drift.py`.
3. **Estado e idempotencia** (`8aac8fe`): `DynamoExecutionState` con
   escrituras condicionales (`attribute_not_exists` para claims,
   `status = PENDING` para la transicion a EXECUTING, `attribute_exists(pk)`
   para resultados) y nombres de expresion para keywords reservadas.

### Decisiones tecnicas

- **prices.json en Gold**: el optimizador ahora escribe
  `run_date=X/prices.json` (closes de referencia de Bronze, via nuevo
  `PricesRepository` protocol) y valida cobertura de los simbolos del
  `OptimizerInput`. El executor lee ese artefacto con
  `GoldPricesRepository` (falta -> NotFoundError, fail-closed) para el
  chequeo de drift con precios de referencia.
- **Claim antes que todo**: el claim de idempotencia se hace primero; un
  claim pre-existente sin resultado => `IdempotencyConflict` (en vuelo, se
  propaga SIN escribir failure en el claim del dueno). Los umbrales
  aprobados: $50 min_order_notional, 1% max_price_drift_pct, 60 s timeout
  de polling con waiter inyectable.
- **SimulatedBroker solo para tests/local**: `build_dependencies` del
  handler lanza `ConfigurationError` si no hay broker real; el adaptador
  Alpaca paper llega en la parte B (`feat/executor-broker`). La ruta
  `POST /rebalance` no es deployable todavia: el registro de la ruta y el
  adaptador son parte B.
- **Claims zombis**: si el proceso muere entre claim y resultado, el TTL
  (24 h) limpia el claim y un reintento posterior puede ejecutar. La
  propuesta quedaría EXECUTING; se reconsiderará con la reconciliación de
  la parte B si hace falta un camino de escape manual.

4. **Handler y servicio** (`54a2bb0`): `RebalanceService` (orden crítica:
   claim → replay → propuesta → expirada → pending → mercado abierto →
   drift → transición condicional a EXECUTING → plan → submit con polling
   hasta deadline → clasificar EXECUTED/PARTIAL/FAILED → reconciliar desde
   posiciones reales → persistir STATE/CURRENT + resultado) y `run_executor`
   (ruta `POST /rebalance`, header `idempotency-key` case-insensitive,
   mapeo de errores: NotFoundError→404, DataValidationError→400,
   rejections→409, BrokerError→502, Storage/Configuration→500).
   Los tests de flujo usan el SimulatedBroker con moto (sin AWS real).

### Verificacion ejecutada

- `pytest tests/unit tests/property tests/integration`: 327 passed.
- `ruff check` + `ruff format`: limpio. `mypy --strict src`: sin errores.
- Pendiente para el cierre de la parte A: `make check` completo
  (incluye cobertura >= 90 % en executor/core), `cdk synth`, push y PR.
