# Optimización de Portafolios de Markowitz Casi en Tiempo Real mediante Programación Cuadrática Convexa: Arquitectura Serverless en AWS con IaC

> **Versión 2 (noviembre 2026).** Rediseño completo a partir de la versión 1. Incluye correcciones técnicas, decisiones de herramientas justificadas con datos verificados y un plan de implementación.

**Título sugerido (más honesto y defendible):**
*Near-Real-Time Monitoring and Daily Markowitz Rebalancing via Convex Quadratic Programming: An AWS Serverless IaC Architecture*

---

## Índice

1. [Resumen ejecutivo](#1-resumen-ejecutivo)
2. [Qué cambió respecto a la versión 1 y por qué](#2-qué-cambió-respecto-a-la-versión-1-y-por-qué)
3. [Estrategia de inversión y de operación (dos velocidades)](#3-estrategia-de-inversión-y-de-operación-dos-velocidades)
4. [Arquitectura general](#4-arquitectura-general)
5. [Flujo de datos nodo por nodo](#5-flujo-de-datos-nodo-por-nodo)
6. [Modelo matemático](#6-modelo-matemático)
7. [Backtesting walk-forward](#7-backtesting-walk-forward)
8. [Capa de servido y dashboard](#8-capa-de-servido-y-dashboard)
9. [Ejecución de órdenes segura (human-in-the-loop)](#9-ejecución-de-órdenes-segura-human-in-the-loop)
10. [Modelo de datos](#10-modelo-de-datos)
11. [Seguridad](#11-seguridad)
12. [Observabilidad y operación](#12-observabilidad-y-operación)
13. [Infraestructura como código y CI/CD](#13-infraestructura-como-código-y-cicd)
14. [Matriz de selección tecnológica](#14-matriz-de-selección-tecnológica)
15. [Costos reales estimados](#15-costos-reales-estimados)
16. [Registro de riesgos](#16-registro-de-riesgos)
17. [Plan de implementación por fases](#17-plan-de-implementación-por-fases)
18. [Estructura sugerida del repositorio](#18-estructura-sugerida-del-repositorio)
19. [Criterios de éxito y evaluación](#19-criterios-de-éxito-y-evaluación)
20. [Limitaciones y trabajo futuro](#20-limitaciones-y-trabajo-futuro)
21. [Fuentes consultadas](#21-fuentes-consultadas)

---

## 1. Resumen ejecutivo

Se construye un sistema **serverless** en AWS que:

1. Descarga precios de un universo de 15–30 ETFs líquidos desde **Alpaca Market Data**.
2. Los organiza en un lakehouse **Bronze / Silver / Gold** sobre S3 (Parquet).
3. Resuelve cada día, tras el cierre de mercado, un problema de **programación cuadrática convexa** (Markowitz robustecido con covarianza Ledoit-Wolf, retornos esperados con shrinkage y penalización de rotación L1) usando **CVXPY**.
4. Publica una **propuesta de rebalanceo** y la muestra en un dashboard propio (SPA estática).
5. Ante la **aprobación explícita del usuario**, un ejecutor envía las órdenes a **Alpaca (paper trading)** de forma idempotente y reconcilia el estado real del portafolio.
6. Entre cierres, un **lazo de monitoreo** cada 15 minutos (solo con mercado abierto) calcula el desvío del portafolio real respecto al objetivo, sin reoptimizar.

Toda la infraestructura se define con **Terraform** (requisito del proyecto) y se despliega con GitHub Actions.

**Decisión central de diseño:** el "tiempo real" se refiere al **monitoreo**, no a la decisión de inversión. Reoptimizar Markowitz cada 15 minutos con retornos diarios solo añade ruido y rotación.

---

## 2. Qué cambió respecto a la versión 1 y por qué

| # | Versión 1 | Versión 2 | Motivo |
|---|---|---|---|
| 1 | Looker / Looker Studio sobre Athena | **SPA estática (Plotly.js) en S3 + CloudFront** | Looker Studio no tiene conector nativo con Athena y no ejecuta acciones; Looker enterprise es caro. QuickSight conecta con Athena, pero cuesta ~$24/mes por Author y ~$3 por Reader, y tampoco puede disparar un POST de aprobación. |
| 2 | Athena + Glue en el camino crítico | **Athena + Glue opcionales** (capa analítica) | El Gold son pocos KB por corrida; Athena es innecesaria para servir el dashboard. Se conserva como demostración del lakehouse y para SQL ad hoc. |
| 3 | Botón "Rebalancear" dentro de Looker | **Botón en la SPA con Cognito + API Gateway** | Las herramientas BI no ejecutan acciones sobre APIs externas de forma simple. |
| 4 | Yahoo Finance (`yfinance`) | **Alpaca Market Data** (principal); yfinance solo como respaldo | yfinance es un scraper no oficial con errores 429 y roturas frecuentes. Alpaca ya se usa para ejecutar: una sola credencial y precios ajustados. |
| 5 | Cron cada 15 min para optimizar | **Optimización diaria + monitoreo cada 15 min** | Markowitz con retornos diarios no justifica reoptimización intradía. |
| 6 | Backtest dentro de la Lambda del optimizador | **Backtest separado** (Step Functions Map / Fargate Spot), semanal | Lambda tiene tope de 15 min; el walk-forward con muchas reoptimizaciones puede excederlo. |
| 7 | Lambdas encadenadas por eventos | **AWS Step Functions** | Reintentos, manejo de errores y trazabilidad. |
| 8 | Secrets Manager implícito | **SSM Parameter Store (SecureString)** | Secrets Manager cobra por secreto al mes; SSM estándar no. |
| 9 | Cron en UTC fijo (`21:00 UTC`) | **EventBridge Scheduler con zona `America/New_York`** | El cierre del mercado es 16:00 ET; en UTC cambia con el horario de verano. |
| 10 | μ = media muestral | **μ con shrinkage** + benchmarks (mínima varianza, risk parity, 1/N) | La media muestral es la parte más ruidosa de Markowitz. |
| 11 | Costos "$0.00" | **$1–$5/mes realistas** | El free tier cambió para cuentas nuevas (créditos, no 12 meses). |
| 12 | Ejecución sin salvaguardas | **Idempotencia, reloj de mercado, validez de propuesta, reconciliación real** | Evitar doble orden y desfases entre pesos teóricos y reales. |
| 13 | Silver escrita por Ingestion y/o Optimizer (ambiguo) | **Lambda `Clean` dedicada** | Responsabilidad única por función. |
| 14 | IaC "CDK / Terraform" (ambiguo) | **Terraform** (requisito del proyecto) | Cumple el requisito; estado remoto en S3, módulos reutilizables y planes revisables. |
| 15 | Sin modo seguro | **`paper=True` obligatorio** hasta validar | Reducir riesgo financiero durante el desarrollo. |

---

## 3. Estrategia de inversión y de operación (dos velocidades)

### 3.1 Lazo lento: decisión (diario, tras el cierre)

- **Disparo:** EventBridge Scheduler, lunes a viernes, 16:30 `America/New_York`.
- **Calendario:** el primer paso consulta el calendario de Alpaca; si el día no es de mercado (feriado), el flujo termina sin error.
- **Cálculo:** ingesta → limpieza → optimización → propuesta.
- **Regla de rebalanceo:** solo se **propone** rebalancear si $\lVert w^* - w_{actual}\rVert_1 > 0.10$ (equivale a 5% de rotación unilateral). Debajo del umbral, la propuesta queda como "mantener".
- **Decisión final:** siempre humana (human-in-the-loop).

### 3.2 Lazo rápido: monitoreo (cada 15 min, solo con mercado abierto)

- **Disparo:** EventBridge Scheduler cada 15 minutos entre 09:30 y 16:00 ET, lunes a viernes.
- **Qué hace:** lee posiciones reales y precios; calcula pesos actuales y drift frente a $w^*$; guarda una instantánea en DynamoDB; alerta por SNS si el drift supera un umbral configurable.
- **Qué NO hace:** no reoptimiza ni envía órdenes.
- **Nota sobre datos:** en el plan gratuito de Alpaca, las cotizaciones intradía provienen de IEX (una fracción del volumen consolidado) y el histórico tiene 15 minutos de retraso. Es suficiente para monitoreo, no para trading de alta frecuencia.

### 3.3 Lazo semanal: validación (backtest)

- Job independiente que recalcula el walk-forward completo y escribe la curva de equity y métricas en Gold.
- Sirve para detectar degradación del modelo y alimentar el dashboard.

### 3.4 Universo de inversión

- 15–30 ETFs líquidos de renta variable, bonos, oro y bienes raíces (por ejemplo, SPY, QQQ, IWM, EFA, EEM, AGG, TLT, LQD, GLD, VNQ, entre otros).
- Con tope $w_i \le 0.20$ se necesitan **al menos 5 activos** para que el problema sea factible.
- Los ETFs evitan el sesgo de supervivencia propio de acciones individuales.
- La lista vive en un archivo de configuración versionado en el repositorio.

---

## 4. Arquitectura general

```text
┌────────────────────────────────────────────────────────────────────────────────────────────┐
│ IaC: Terraform + GitHub Actions (OIDC)                                                     │
└────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌─ LAZO LENTO (16:30 ET, L-V) ──────────────────────────────────────────────────────────────┐
 │ EventBridge Scheduler ─► Step Functions (Standard)                                        │
 │   0. CheckMarketCalendar λ (Alpaca clock/calendar)                                        │
 │   1. Ingest λ ─► Alpaca Data API (fallback: yfinance)                                     │
 │         │  Pydantic + chequeos de calidad ──fallo──► S3 quarantine                        │
 │         ▼                                                                                 │
 │      S3 BRONZE (JSON crudo; year=YYYY/month=MM/day=DD/)                                   │
 │   2. Clean λ ─► S3 SILVER (Parquet+Snappy: retornos logarítmicos)                         │
 │   3. Optimizer λ (imagen Docker: cvxpy, scikit-learn, numpy, scipy, pyarrow)              │
 │         lee w_actual ◄── DynamoDB PortfolioState                                          │
 │         Ledoit-Wolf + μ con shrinkage + QP con penalización L1                            │
 │         ▼                                                                                 │
 │      S3 GOLD (Parquet + latest.json: w*, métricas, propuesta)                             │
 │   4. Notify (SNS ─► email: "hay propuesta de rebalanceo")                                 │
 └───────────────────────────────────────────────────────────────────────────────────────────┘

 ┌─ LAZO RÁPIDO (cada 15 min, mercado abierto) ──────────────────────────────────────────────┐
 │ EventBridge Scheduler ─► Monitor λ ─► posiciones reales + precios (Alpaca)                │
 │                       ─► drift ─► DynamoDB (instantánea) ─► SNS si supera umbral          │
 └───────────────────────────────────────────────────────────────────────────────────────────┘

 ┌─ BACKTEST (semanal, separado) ────────────────────────────────────────────────────────────┐
 │ Step Functions (Map por fecha de rebalanceo) o Fargate Spot ─► S3 GOLD (equity curve)     │
 └───────────────────────────────────────────────────────────────────────────────────────────┘

 ┌─ SERVIDO Y DECISIÓN ──────────────────────────────────────────────────────────────────────┐
 │ Usuario ─► CloudFront + S3 (SPA Plotly.js) ─► Cognito (login, JWT)                        │
 │         ─► API Gateway HTTP API                                                           │
 │              GET  /proposal, /metrics, /backtest, /monitor ─► Reader λ ─► S3 Gold/DynamoDB│
 │              POST /rebalance (Idempotency-Key) ─► Executor λ ──► Alpaca (PAPER)           │
 │                                                       │ reconcilia posiciones reales      │
 │                                                       └──► DynamoDB PortfolioState        │
 └───────────────────────────────────────────────────────────────────────────────────────────┘

 Opcional: AWS Glue Data Catalog + Amazon Athena sobre Silver/Gold (SQL ad hoc, no crítico).
 Transversal: IAM de mínimo privilegio · CloudWatch Alarms · DLQ · AWS Budgets · SSM Parameter Store.
```

---

## 5. Flujo de datos nodo por nodo

### Nodo 1: Programadores de eventos
- **Herramienta:** Amazon EventBridge Scheduler.
- **Reglas:**
  - `cron(30 16 ? * MON-FRI *)` con zona `America/New_York` → lazo lento.
  - `rate(15 minutes)` restringido a la ventana de mercado → lazo rápido.
  - Programación semanal → backtest.
- **Función:** dispara la ejecución de las máquinas de estado sin servidores activos.

### Nodo 2: Verificación de calendario
- **Herramienta:** Lambda `CheckMarketCalendar`.
- **Función:** consulta el reloj y el calendario de Alpaca. Si no es día de mercado, la máquina de estado termina en éxito sin ejecutar el resto.

### Nodo 3: Ingesta y validación
- **Herramienta:** Lambda `Ingest`.
- **Función:**
  1. Obtiene barras diarias ajustadas (splits y dividendos) de cada ticker desde Alpaca Market Data. Si falla, usa yfinance como respaldo y lo registra.
  2. Respeta el límite de 200 llamadas por minuto del plan gratuito (lotes y backoff exponencial).
  3. Valida el esquema con **Pydantic**: nulos (`NaN`), fechas vacías o duplicadas, precios no positivos, saltos anómalos (por ejemplo, retorno diario fuera de ±40%), huecos de calendario.
  4. Si hay datos corruptos, aísla el lote en `s3://lakehouse-quarantine/` y emite una métrica y alerta, sin detener el resto del pipeline cuando sea recuperable.
  5. Es **idempotente**: reescribe siempre la misma partición por fecha.

### Nodo 4: Capa Bronze (datos crudos)
- **Herramienta:** S3, bucket `lakehouse-bronze`.
- **Función:** guarda la respuesta cruda original (JSON), particionada `year=YYYY/month=MM/day=DD/`, con versionado habilitado y reglas de ciclo de vida.

### Nodo 5: Capa Silver (series limpias)
- **Herramienta:** Lambda `Clean` + S3, bucket `lakehouse-silver`.
- **Función:** construye la matriz de precios alineada por fecha y calcula retornos logarítmicos:

$$r_{i,t} = \ln\left(\frac{P_{i,t}}{P_{i,t-1}}\right)$$

- Tratamiento de faltantes documentado (por ejemplo, forward-fill acotado y exclusión de activos con historia insuficiente).
- Guarda en **Apache Parquet** comprimido con Snappy.

### Nodo 6: Motor de optimización cuadrática
- **Herramienta:** Lambda `Optimizer`, desplegada como **imagen de contenedor Docker** (por el peso de `cvxpy`, `scikit-learn`, `numpy`, `scipy`, `pyarrow`). Memoria sugerida: 1–2 GB.
- **Pasos:**
  1. Lee la ventana móvil de retornos desde Silver.
  2. Lee $w_{actual}$ desde DynamoDB (provisioned, lectura de milisegundos).
  3. Estima $\Sigma_{LW}$ y $\mu_s$ (ver sección 6).
  4. Resuelve el QP con `CLARABEL` u `OSQP`.
  5. Calcula métricas esperadas (retorno, volatilidad, Sharpe, contribución al riesgo).
  6. Evalúa el umbral de rebalanceo y genera la propuesta con `proposal_id` y expiración.
  7. Escribe resultados en Gold.
- **Manejo de infactibilidad:** si el solver no converge o el problema es infactible, la propuesta queda en estado `FAILED`, se alerta y **no** se modifica nada.

### Nodo 7: Capa Gold y persistencia de estado
- **Herramienta A:** S3, bucket `lakehouse-gold`. Guarda Parquet con $w^*$, retornos esperados, riesgo esperado, Sharpe estimado, métricas de riesgo, frontera eficiente y curva de equity del backtest. Además publica `latest.json` (pocos KB) para la SPA.
- **Herramienta B:** DynamoDB, tabla `PortfolioState`. Guarda **únicamente** el estado vigente: $w_{actual}$, última reconciliación, y estados de propuestas e idempotencia (ver sección 10).

### Nodo 8: Notificación
- **Herramienta:** Amazon SNS.
- **Función:** correo al usuario cuando hay una propuesta nueva, cuando falla un paso, o cuando el monitoreo detecta drift alto.

### Nodo 9: Backtest semanal
- **Herramienta:** Step Functions con estado `Map` (una iteración por fecha de rebalanceo) o tarea en AWS Fargate Spot.
- **Función:** ejecuta el walk-forward (sección 7) y escribe la curva de equity y métricas en Gold.

### Nodo 10: Capa de lectura (API)
- **Herramientas:** Amazon API Gateway (HTTP API) con autorizador JWT de Cognito + Lambda `Reader`.
- **Endpoints de lectura:** `GET /proposal`, `GET /metrics`, `GET /backtest`, `GET /monitor`.

### Nodo 11: Dashboard financiero
- **Herramienta:** SPA estática (React o JavaScript simple + **Plotly.js**) servida desde S3 + CloudFront, con login por **Amazon Cognito**.
- **Visualizaciones:**
  - Composición: pesos actuales $w_{actual}$ vs. óptimos $w^*$.
  - Frontera eficiente (barrido de γ) con el portafolio tangente marcado.
  - Métricas: Sharpe, volatilidad esperada, máximo drawdown, rotación.
  - Backtest: curva de equity de Markowitz vs. benchmarks (SPY, 1/N, mínima varianza).
  - Indicador de rebalanceo cuando $\lVert w^*-w_{actual}\rVert_1 > 0.10$.
  - Panel de monitoreo intradía (drift en las últimas horas).
  - Botón **Aprobar rebalanceo**, con resumen de las órdenes que se enviarán.

### Nodo 12: Entrada de decisión
- **Herramienta:** `POST /api/v1/rebalance` en API Gateway.
- **Requisitos:** JWT de Cognito válido, `proposal_id` y cabecera `Idempotency-Key`.

### Nodo 13: Motor de ejecución de órdenes
- **Herramienta:** Lambda `Executor`.
- **Función:** ver sección 9. Convierte diferencias de peso en órdenes y las envía al broker.

### Nodo 14: Broker
- **Herramienta:** Alpaca Trading API en **paper trading** (`paper=True`).
- **Función:** ejecuta las órdenes y devuelve estados de ejecución (fills).

### Nodo 15: Cierre de circuito
- **Herramienta:** Lambda `Executor` → DynamoDB.
- **Función:** tras los fills, lee las **posiciones reales** del broker, calcula los pesos reales y sobrescribe $w_{actual}$. La siguiente corrida diaria parte de ese estado.

---

## 6. Modelo matemático

### 6.1 Retornos y ventana
- Retornos logarítmicos diarios en una ventana móvil de $T$ periodos (por ejemplo, $T = 252$).
- Solo se usan datos hasta $t-1$ para decidir en $t$ (sin look-ahead).

### 6.2 Covarianza robusta (Ledoit-Wolf)

$$\Sigma_{LW} = \delta F + (1-\delta) S$$

donde $S$ es la covarianza muestral, $F$ la matriz objetivo y $\delta \in [0,1]$ la intensidad de encogimiento.

> **Aclaración técnica:** `sklearn.covariance.LedoitWolf` encoge hacia una **identidad escalada**, no hacia una matriz de correlación constante. Si quieres el objetivo de correlación constante, hay que implementarlo aparte. `OAS` es una alternativa en `sklearn`. Documenta cuál usas en la tesis.

### 6.3 Retornos esperados con shrinkage

La media muestral $\bar r$ es muy ruidosa. Se encoge hacia la media global:

$$\mu_s = (1-\alpha)\,\bar r + \alpha\,\bar r_{global}\,\mathbf{1}, \qquad \alpha \in [0,1]$$

- $\alpha$ se elige por validación temporal anidada (sección 7).
- Se comparan además estrategias que **no** requieren μ: mínima varianza y risk parity.

### 6.4 Problema de optimización (QP convexo)

$$\min_{w}\ \ \frac{\gamma}{2}\,w^\top \Sigma_{LW}\, w \;-\; \mu_s^\top w \;+\; \lambda_{tc}\,\lVert w - w_{actual}\rVert_1$$

sujeto a:

1. **Inversión total:** $\sum_{i=1}^{N} w_i = 1$.
2. **Sin ventas en corto y tope de exposición:** $0 \le w_i \le 0.20\ \ \forall i$ (requiere $N \ge 5$).
3. **Rotación:** el término $\lambda_{tc}\lVert w - w_{actual}\rVert_1$ penaliza movimientos pequeños que consumirían costos. Opcionalmente, una restricción dura $\lVert w - w_{actual}\rVert_1 \le \tau_{max}$.

Notas:
- $\gamma$ es la aversión al riesgo (sobre la varianza). Se barre $\gamma$ para trazar la frontera eficiente.
- $\lambda_{tc}$ representa spread, slippage e impuestos (Alpaca no cobra comisión). Calibración inicial: 5–10 pb.
- CVXPY convierte la norma L1 en un QP estándar. Solvers: `CLARABEL` (por defecto en CVXPY moderno) u `OSQP`.
- Se usa `cp.Parameter` para $\Sigma$, $\mu$, $w_{actual}$, $\gamma$ y $\lambda$: el problema se compila una vez y se resuelve rápido.

### 6.5 Portafolio tangente y Sharpe

- **Sharpe anualizado:**

$$SR = \frac{\mathbb{E}[r_p] - r_f}{\sigma_p}\sqrt{252}\ \ \text{(con retornos diarios)}$$

- La tasa libre de riesgo $r_f$ se define explícitamente (por ejemplo, rendimiento de T-Bills a 3 meses, o una constante documentada).
- **Portafolio tangente con restricciones de caja:** se resuelve con la reformulación convexa estándar (variable $y = \kappa w$): minimizar $y^\top \Sigma y$ sujeto a $(\mu - r_f\mathbf{1})^\top y = 1$, $y \ge 0$, $y \le 0.20\,\kappa$, $\kappa \ge 0$; luego $w = y/\sum y$.
- **Frontera eficiente:** barrido de $\gamma$ (o de retorno objetivo) y graficado de (volatilidad, retorno).

### 6.6 Métricas de riesgo reportadas
Volatilidad anualizada, Sharpe, Sortino, máximo drawdown, Calmar, VaR/CVaR histórico (opcional), contribución marginal al riesgo por activo, concentración (índice de Herfindahl), rotación esperada.

---

## 7. Backtesting walk-forward

### 7.1 Diseño
- **Ventana de estimación:** móvil de 252 días (con prueba de sensibilidad a 126 y 504).
- **Frecuencia de rebalanceo:** mensual (con prueba de sensibilidad semanal y trimestral).
- **Ejecución simulada:** decisión con datos hasta $t-1$, ejecución a precios de $t$.
- **Costos:** el mismo $\lambda_{tc}$ y los mismos topes que producción.
- **Regla de umbral:** se aplica igual que en producción (no rebalancear si la desviación es menor al umbral).

### 7.2 Control de sobreajuste
- **Hiperparámetros** ($\gamma$, $\lambda_{tc}$, $\alpha$, tamaño de ventana): elegidos con **validación temporal anidada** (solo con datos anteriores al periodo evaluado). Nunca mirando el periodo de prueba.
- Reportar resultados **dentro y fuera de muestra** por separado.
- Probar al menos una crisis (2008, 2020, 2022) y un periodo alcista.

### 7.3 Benchmarks
1. **SPY** (buy and hold).
2. **1/N** (equiponderado con el mismo rebalanceo).
3. **Mínima varianza** con los mismos topes.
4. **Risk parity** (opcional).

### 7.4 Métricas comparativas
Retorno anualizado, volatilidad, Sharpe, Sortino, máximo drawdown, Calmar, rotación anual, costo total estimado, tiempo en drawdown.

### 7.5 Ejecución en la nube
- Cada fecha de rebalanceo es una iteración de un estado `Map` (con concurrencia máxima configurable), o el backtest completo corre en una tarea Fargate Spot si excede el tope de 15 min de Lambda.
- Salidas: `equity_curve.parquet`, `metrics.parquet`, `weights_history.parquet` en Gold.

### 7.6 Sesgos que se deben documentar
Look-ahead, supervivencia (mitigado con ETFs), sobreajuste, costos subestimados, diferencia entre IEX y datos consolidados, y que "resultados pasados no garantizan resultados futuros".

---

## 8. Capa de servido y dashboard

### 8.1 Por qué una SPA propia y no una herramienta BI

| Opción | Problema |
|---|---|
| Looker Studio | Sin conector nativo con Athena; no ejecuta acciones. |
| Looker (enterprise) | Licencia costosa; "Actions" añaden complejidad. |
| Amazon QuickSight | Conecta con Athena, pero cuesta ~$24/mes por Author y ~$3 por Reader, y no puede disparar la aprobación. Además, la promoción de capacidades Enterprise para Authors termina el 31 de diciembre de 2026. |
| Grafana / Streamlit | Requieren servidor persistente (costo y operación). App Runner cerró a clientes nuevos en abril de 2026. |
| **SPA estática + API** | Costo casi nulo, sin servidores, y permite el botón de aprobar. **Elegida.** |

### 8.2 Componentes
- **Front-end:** HTML/JS (o React) con Plotly.js, publicado en S3 y distribuido por CloudFront (con Origin Access Control).
- **Autenticación:** Cognito User Pool (50.000 usuarios activos mensuales en la capa gratuita; suficiente para este caso). Flujo OAuth2/OIDC con PKCE.
- **API:** API Gateway HTTP API con autorizador JWT.
- **Lambda `Reader`:** lee `latest.json` y Parquet de Gold, y la instantánea del monitoreo en DynamoDB. Opcionalmente usa DuckDB para leer Parquet con SQL dentro de la Lambda.

### 8.3 Capa analítica opcional
- **AWS Glue Data Catalog + Amazon Athena** sobre Silver y Gold para consultas SQL ad hoc, validación y análisis de la tesis.
- Particiones y Parquet reducen el escaneo. No es dependencia del dashboard ni de la ejecución.

---

## 9. Ejecución de órdenes segura (human-in-the-loop)

### 9.1 Flujo
1. El usuario revisa la propuesta (con resumen de órdenes y costos estimados) y pulsa **Aprobar**.
2. La SPA envía `POST /api/v1/rebalance` con `proposal_id` y `Idempotency-Key` (UUID generado en el cliente).
3. El `Executor` valida y ejecuta.

### 9.2 Validaciones previas
- **Autenticación y autorización:** JWT de Cognito válido y usuario autorizado.
- **Idempotencia:** `PutItem` condicional en DynamoDB con la clave de idempotencia. Si ya existe, se devuelve el resultado previo y **no** se envían órdenes nuevas.
- **Propuesta vigente:** el `proposal_id` debe estar en estado `PENDING` y no haber expirado (por ejemplo, 24 h).
- **Mercado abierto:** consulta el reloj de Alpaca. Si está cerrado, rechaza o difiere (política configurable).
- **Drift de precios:** si los precios actuales se desviaron más de X% desde la propuesta, se rechaza y se pide recalcular.
- **Modo seguro:** `paper=True` forzado por configuración; cambiar a real exige un cambio de infraestructura explícito.

### 9.3 Cálculo de órdenes
1. Lee el valor total de la cuenta y las posiciones reales desde el broker.
2. Calcula la diferencia de pesos $\Delta w_i = w^*_i - w_{i,actual}$ usando los **pesos reales**.
3. Convierte a monto o cantidad:

$$\text{Monto}_i = \Delta w_i \times \text{Capital Total}, \qquad \text{Acciones}_i = \frac{\Delta w_i \times \text{Capital Total}}{\text{Precio Actual}_i}$$

4. Usa **órdenes por monto (notional)** o cantidades fraccionarias cuando el activo lo permita; filtra órdenes por debajo de un mínimo (polvo).

### 9.4 Envío y seguimiento
- **Primero ventas, luego compras**, para liberar efectivo.
- `client_order_id` determinista (`{proposal_id}-{ticker}-{side}`), para que un reintento no duplique órdenes.
- Manejo de **fills parciales**, rechazos y órdenes pendientes: se consulta el estado hasta un tiempo límite; lo no ejecutado se reporta.
- Registro completo (auditoría) de cada orden y respuesta.

### 9.5 Cierre de circuito
- Tras el seguimiento, lee las posiciones reales y escribe $w_{actual}$ en DynamoDB con la hora de reconciliación.
- Marca la propuesta como `EXECUTED`, `PARTIAL` o `FAILED` y notifica por SNS.
- Si el sistema falla a medias, la **reconciliación periódica** (lazo rápido) corrige $w_{actual}$ con la realidad del broker.

---

## 10. Modelo de datos

### 10.1 S3 (lakehouse)

| Bucket / prefijo | Contenido | Formato | Particiones |
|---|---|---|---|
| `lakehouse-bronze/` | Respuesta cruda de la API | JSON | `year/month/day` |
| `lakehouse-quarantine/` | Lotes rechazados por validación | JSON + motivo | `year/month/day` |
| `lakehouse-silver/` | Retornos logarítmicos limpios | Parquet + Snappy | `year/month` |
| `lakehouse-gold/` | $w^*$, métricas, frontera, equity curve, `latest.json` | Parquet / JSON | `run_date` |

Todos con cifrado en reposo (SSE-S3 o KMS), bloqueo de acceso público, versionado en Bronze y reglas de ciclo de vida.

### 10.2 DynamoDB (`PortfolioState`, capacidad aprovisionada)

| `pk` | `sk` | Atributos principales |
|---|---|---|
| `STATE` | `CURRENT` | `weights` (mapa ticker→peso), `reconciled_at`, `account_value` |
| `PROPOSAL` | `{proposal_id}` | `w_star`, `created_at`, `expires_at`, `status`, `prices_ref` |
| `IDEMPOTENCY` | `{idempotency_key}` | `proposal_id`, `status`, `result`, `ttl` |
| `MONITOR` | `{timestamp}` | `weights_live`, `drift_l1`, `ttl` |

La capa gratuita de DynamoDB aplica a **capacidad aprovisionada** (no on-demand), así que la tabla se aprovisiona con 1–5 RCU/WCU.

---

## 11. Seguridad

- **IAM de mínimo privilegio:** un rol por Lambda con acceso solo a los recursos que usa (prefijos de S3, tabla DynamoDB, parámetros SSM específicos).
- **Secretos:** claves de Alpaca en **SSM Parameter Store (SecureString)**; nunca en código, variables de entorno en claro ni repositorio.
- **Autenticación de usuarios:** Cognito con MFA recomendado.
- **API:** autorizador JWT, límites de tasa (throttling) y CORS restringido al dominio de la SPA.
- **Red:** CloudFront con HTTPS obligatorio; S3 sin acceso público (OAC).
- **Datos:** cifrado en reposo y en tránsito; registros sin datos sensibles.
- **CI/CD:** GitHub Actions con **OIDC** hacia AWS (sin llaves de larga duración).
- **Dependencias:** versiones fijadas, escaneo de vulnerabilidades y de la imagen Docker.
- **Modo paper por defecto** y doble confirmación para pasar a real.

---

## 12. Observabilidad y operación

- **Logs estructurados (JSON)** con `run_id`, `proposal_id` y `idempotency_key`.
- **Métricas personalizadas:** duración del solver, estado del solver, número de activos válidos, filas en cuarentena, drift L1, rotación propuesta.
- **Alarmas CloudWatch:** fallos de Lambda, ejecuciones fallidas de Step Functions, mensajes en DLQ, latencias anómalas, ausencia de corrida diaria.
- **DLQ (SQS)** para eventos que fallan tras los reintentos.
- **Notificaciones:** SNS por correo.
- **AWS Budgets:** alertas al 50%, 80% y 100% del presupuesto mensual definido (y control de créditos).
- **X-Ray / trazas** opcionales en Step Functions y Lambdas.
- **Runbook:** documento breve con qué hacer ante cuarentena, fallo del solver, fallo parcial de ejecución y caída del broker.

---

## 13. Infraestructura como código y CI/CD

### 13.1 Herramienta
**Terraform** (requisito del proyecto), con provider `hashicorp/aws`, versiones fijadas y `.terraform.lock.hcl` commiteado.

- **Estado remoto:** bucket S3 dedicado (versionado, cifrado, acceso público bloqueado) con bloqueo nativo (`use_lockfile`, Terraform ≥ 1.10; verifica la versión). Un estado por entorno.
- **Bootstrap:** el bucket de estado y el rol OIDC se crean una sola vez, de forma manual y controlada.
- **Nota:** el estado puede contener datos sensibles; restringe el acceso al bucket de estado.

### 13.2 Módulos sugeridos
1. `data`: buckets S3, tabla DynamoDB, parámetros SSM, SNS, SQS (DLQ).
2. `pipeline`: Lambdas, imagen Docker (ECR), Step Functions, EventBridge Scheduler.
3. `backtest`: máquina de estado `Map` o tarea Fargate Spot.
4. `api`: Cognito, API Gateway (HTTP API), Lambdas `Reader` y `Executor`.
5. `web`: bucket de la SPA y distribución CloudFront.
6. `analytics` (opcional): Glue Data Catalog y Athena (workgroup con límite de escaneo).
7. `observability`: alarmas, tableros y budgets.

Los entornos `dev` y `prod` (`infra/envs/*`) componen estos módulos con distintas variables.

### 13.3 CI/CD (GitHub Actions)
- **Pull request:** lint (`ruff`), tipos (`mypy`), pruebas (`pytest`), `terraform fmt -check`, `validate`, `tflint`, `checkov` y `terraform plan` (rol OIDC de solo lectura), con el plan publicado en el PR.
- **Merge a `main`:** `terraform apply` al entorno `dev` solo con aprobación manual (environment protection) y usando el plan guardado; promoción a `prod` manual.
- **Autenticación:** rol IAM asumido por OIDC.
- **Pruebas clave:** validación Pydantic, propiedades del optimizador (suma de pesos = 1, topes, factibilidad), pruebas de idempotencia del ejecutor con un broker simulado (mock), `terraform test` en los módulos.

---

## 14. Matriz de selección tecnológica

| Capa | Servicio | ¿Por qué esta herramienta y no otra? |
|---|---|---|
| Programación | EventBridge Scheduler | Soporta zonas horarias (clave para el cierre de mercado) y reemplaza cron en servidores. |
| Orquestación | Step Functions (Standard) | Reintentos, manejo de errores, trazabilidad visual y estado `Map` para el backtest. |
| Ingesta y limpieza | Lambda (Python) | Sin servidores 24/7; tareas cortas y paralelizables. |
| Solver | Lambda con imagen Docker | Permite `cvxpy`, `scikit-learn`, `scipy`; el problema (N ≤ 30) se resuelve en segundos. |
| Backtest pesado | Step Functions Map / Fargate Spot | Evita el tope de 15 min de Lambda. |
| Data lake | S3 + Parquet | Almacenamiento barato y desacoplado; Parquet reduce lectura. |
| Estado | DynamoDB (provisioned) | Latencia de milisegundos; cubierto por la capa siempre gratuita con capacidad aprovisionada. |
| Catálogo y SQL (opcional) | Glue + Athena | Consultas SQL sin servidor sobre Parquet; fuera del camino crítico. |
| Visualización | SPA + Plotly.js en S3 + CloudFront | Costo mínimo y permite el botón de aprobación. |
| Autenticación | Cognito | Capa gratuita de 50.000 MAU; JWT para API Gateway. |
| API | API Gateway HTTP API | Más barato que REST API; autorizador JWT nativo. |
| Secretos | SSM Parameter Store | Gratis en el nivel estándar. |
| Datos de mercado | Alpaca Market Data (plan gratuito) | Histórico de 5+ años, 200 llamadas/min, misma cuenta que el broker. Respaldo: yfinance. |
| Broker | Alpaca Trading API (paper) | API REST, paper trading de primera clase, acciones fraccionarias. |
| IaC | Terraform | Requisito del proyecto; estado remoto, módulos y planes revisables antes de aplicar. |
| CI/CD | GitHub Actions + OIDC | Sin credenciales estáticas. |

---

## 15. Costos reales estimados

> Estimaciones aproximadas; verifica precios vigentes en tu región antes de comprometerte.

### 15.1 Qué cambió en el free tier
- Las cuentas creadas desde el **15 de julio de 2025** usan un modelo de **créditos** (alrededor de $100–$200 según el flujo de registro) en lugar de los antiguos beneficios de 12 meses por servicio.
- Las ofertas **siempre gratuitas** siguen vigentes: Lambda (1 M de solicitudes y 400.000 GB-segundos al mes) y DynamoDB (25 GB), entre otras.
- Las llamadas gratuitas de **API Gateway** duran 12 meses solo para clientes con el modelo antiguo; las cuentas del plan Free solo reciben las ofertas siempre gratuitas.
- **S3** no tiene capa siempre gratuita para cuentas nuevas: consume créditos o se paga (centavos en este volumen).

### 15.2 Tabla de costos

| Componente | Costo estimado mensual | Observación |
|---|---|---|
| EventBridge Scheduler | ~$0 | Volumen muy bajo. |
| Lambda (todas) | ~$0 | Dentro de la capa siempre gratuita. |
| Step Functions (Standard) | ~$0–$0.50 | Verifica transiciones gratuitas mensuales; el backtest semanal puede exceder. |
| S3 (Bronze/Silver/Gold) | < $0.10 | Pocos GB. |
| DynamoDB (provisioned 1–5 RCU/WCU) | ~$0 | Siempre gratuito dentro de 25 GB y capacidad. |
| API Gateway (HTTP API) | ~$0.01–$0.20 | Tarifa por millón de llamadas; tráfico mínimo. |
| Cognito | ~$0 | Muy por debajo de 50.000 MAU. |
| CloudFront + S3 (SPA) | ~$0 | Tráfico mínimo. |
| ECR (imagen Docker) | ~$0.05–$0.20 | Almacenamiento de la imagen. |
| CloudWatch Logs y alarmas | ~$0.50–$2 | Controla retención de logs (por ejemplo, 14–30 días). |
| SSM Parameter Store | $0 | Nivel estándar. |
| Athena + Glue (opcional) | < $0.10 | Solo si se usa; particiones y Parquet limitan el escaneo. |
| Fargate Spot (si se usa para backtest) | ~$0.05–$0.50 | Corridas cortas semanales. |
| Dominio propio (opcional) | ~$0.50 (Route 53) + registro | No necesario para el proyecto. |
| **Total realista** | **~$1–$5 al mes** | Alpaca (paper y datos gratuitos) sin costo. |

### 15.3 Salvaguardas de costo
- AWS Budgets con alertas desde el día 1.
- Evitar NAT Gateway (no es necesario con Lambda fuera de VPC).
- Retención corta de logs y ciclo de vida en S3.
- Workgroup de Athena con límite de datos escaneados por consulta.

---

## 16. Registro de riesgos

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Datos corruptos o faltantes | Optimización incorrecta | Validación Pydantic, cuarentena, fallback a yfinance, alerta. |
| API de datos caída o con límite | Sin propuesta del día | Reintentos con backoff, respaldo, alarma de "sin corrida". |
| Dependencia de yfinance (respaldo) | Roturas silenciosas | Solo respaldo, caché del último dato válido, registro explícito de la fuente usada. |
| Solver no converge / infactible | Sin propuesta | Estado `FAILED`, alerta, sin cambios en $w_{actual}$. |
| Sobreajuste del backtest | Expectativas irreales | Validación anidada, benchmarks, resultados fuera de muestra. |
| Estimación ruidosa de μ | Pesos inestables | Shrinkage, topes por activo, penalización de rotación, benchmark de mínima varianza. |
| Doble envío de orden | Exposición duplicada | Idempotencia, `client_order_id` determinista. |
| Orden con precios obsoletos | Ejecución a mal precio | Propuesta con vencimiento y verificación de drift. |
| Mercado cerrado / feriado | Órdenes rechazadas | Verificación de reloj y calendario. |
| Fills parciales | $w_{actual}$ inexacto | Reconciliación con posiciones reales del broker. |
| Credenciales filtradas | Acceso no autorizado | SSM SecureString, mínimo privilegio, rotación, OIDC en CI. |
| Costos inesperados | Facturación | Budgets, sin NAT, retención corta, límites en Athena. |
| Cambios en términos del free tier | Costos distintos | Revisar precios antes de desplegar y periódicamente. |
| Datos IEX ≠ mercado consolidado | Diferencias en precios | Documentar la limitación; el horizonte diario es poco sensible para ETFs líquidos. |
| Paso accidental a dinero real | Pérdida financiera | `paper=True` forzado, cambio solo por infraestructura con doble confirmación. |

---

## 17. Plan de implementación por fases

### Fase 0: Preparación
- Cuenta AWS nueva, MFA, usuario administrador, AWS Budgets.
- Cuenta de Alpaca (paper) y claves de API.
- Repositorio, estructura de proyecto y entorno local (Python, Docker, Terraform, tflint, checkov).

### Fase 1: Datos
- Cliente Alpaca (histórico diario ajustado) con respaldo yfinance.
- Modelos Pydantic y reglas de calidad.
- Escritura a Bronze y cuarentena; conversión a Silver (retornos logarítmicos en Parquet).
- Pruebas unitarias.

### Fase 2: Estrategia (local, antes de la nube)
- Optimizador en CVXPY (Ledoit-Wolf, μ con shrinkage, penalización L1).
- Backtest walk-forward en notebook con benchmarks y costos.
- Validación temporal anidada de hiperparámetros.
- **Punto de decisión:** si el modelo no aporta valor frente a 1/N y mínima varianza, ajustar el diseño antes de seguir.

### Fase 3: Pipeline en la nube
- Terraform: módulos `data` y `pipeline` (y bootstrap del estado remoto).
- Imagen Docker del optimizador, Step Functions, EventBridge Scheduler.
- Escritura de Gold y estado en DynamoDB; notificaciones SNS.

### Fase 4: API y dashboard
- Cognito, API Gateway, Lambda `Reader`.
- SPA con Plotly.js: composición, frontera eficiente, métricas, backtest.

### Fase 5: Ejecución
- Lambda `Executor` con idempotencia, validaciones, órdenes por monto, reconciliación.
- Pruebas con un broker simulado y luego con paper trading real.

### Fase 6: Monitoreo y backtest en la nube
- Lazo rápido de 15 minutos y panel de drift.
- Backtest semanal en Step Functions `Map` o Fargate Spot.

### Fase 7: Endurecimiento y entrega
- Alarmas, DLQ, runbook, revisión de IAM, pruebas de fallo (caos básico).
- Capa Athena/Glue opcional.
- Documentación de la tesis: metodología, resultados, limitaciones.

---

## 18. Estructura sugerida del repositorio

```text
portfolio-markowitz-aws/
├── README.md
├── pyproject.toml
├── config/
│   ├── universe.yaml            # tickers, topes, parámetros
│   └── strategy.yaml            # γ, λ_tc, α, ventana, umbral
├── infra/                       # Terraform
│   ├── modules/                 # data, pipeline, backtest, api, web, analytics, observability
│   ├── envs/
│   │   ├── dev/
│   │   └── prod/
│   ├── bootstrap/               # bucket de estado remoto + rol OIDC (una sola vez)
│   └── versions.tf
├── src/
│   ├── common/                  # modelos Pydantic, utilidades, logging
│   ├── ingest/                  # Lambda Ingest + CheckMarketCalendar
│   ├── clean/                   # Lambda Clean (Silver)
│   ├── optimizer/               # Lambda Optimizer (Dockerfile)
│   ├── backtest/                # walk-forward
│   ├── monitor/                 # Lambda Monitor
│   ├── reader/                  # Lambda Reader
│   └── executor/                # Lambda Executor
├── web/                         # SPA (Plotly.js)
├── notebooks/                   # investigación y validación
├── tests/
├── docs/
│   └── runbook.md
└── .github/workflows/           # CI/CD con OIDC
```

---

## 19. Criterios de éxito y evaluación

**Técnicos**
- Pipeline diario completo en menos de 5 minutos.
- Solver resuelve en menos de 10 segundos para N ≤ 30.
- 0 órdenes duplicadas en pruebas de idempotencia.
- Reconciliación: $w_{actual}$ coincide con las posiciones reales del broker tras cada ejecución.
- Cobertura de pruebas en validación, optimizador y ejecutor.

**Financieros (backtest, neto de costos)**
- Comparación honesta contra SPY, 1/N y mínima varianza.
- Mejora o equivalencia en Sharpe y menor drawdown frente a 1/N, o explicación documentada si no la hay.
- Rotación anual dentro de un nivel razonable.

**Operativos**
- Costo mensual dentro del presupuesto definido (≈ $1–$5).
- Alarmas funcionando y runbook probado.

---

## 20. Limitaciones y trabajo futuro

**Limitaciones**
- Datos gratuitos IEX con retraso de 15 minutos para el histórico; no apto para trading intradía.
- Modelo de costos simplificado (sin impacto de mercado ni impuestos detallados).
- Markowitz asume retornos estables y covarianza informativa; los regímenes cambian.
- Paper trading no refleja completamente liquidez y slippage reales.

**Trabajo futuro**
- Black-Litterman o views macro para μ.
- Cambio de régimen (HMM) o covarianza condicional (DCC-GARCH).
- Optimización con CVaR en lugar de varianza.
- Restricciones sectoriales y de factores.
- Migración a datos consolidados (SIP) si se justifica.
- Pruebas A/B entre estrategias en paralelo (múltiples portafolios en paper).

---

## 21. Fuentes consultadas

- AWS Builder Center, "The $0 AWS account in 2026: Free plan, credits, guardrails": https://builder.aws.com/content/3Kkt4kNiDSUnO3n6db2weSNubzE/the-dollar0-aws-account-in-2026-free-plan-credits-guardrails
- AWS Builder Center, "Everything you need to know to get started with AWS Free Tier" (actualizado 6 de mayo de 2026): https://builder.aws.com/content/3BlPcDGddaB6WkPvUf1LIy1rzIm/everything-you-need-to-know-to-get-started-with-aws-free-tier
- Help Net Security, nuevo registro de AWS con créditos de $100 y límites de gasto (17 de septiembre de 2026): https://www.helpnetsecurity.com/2026/09/17/aws-spend-limit-agent-set-permissions/
- DEV Community, "AWS Free Hands-on: S3 → Lambda → DynamoDB on the Always Free tier" (DynamoDB siempre gratuito solo en capacidad aprovisionada): https://dev.to/kanywst/aws-free-hands-on-build-s3-lambda-dynamodb-on-the-always-free-tier-plus-iam-cloudwatch-and-34le
- AWS, página de precios de QuickSight: https://aws.amazon.com/quicksight/pricing/
- Comparativa QuickSight vs Tableau (precios verificados en junio de 2026): https://thewearify.com/?p=245101
- Alpaca, documentación de Market Data API: https://docs.alpaca.markets/us/docs/about-market-data-api
- Alpaca, planes de datos y límites del plan gratuito: https://alpaca.markets/deprecated/docs/api-documentation/api-v2/market-data/alpaca-data-api-v2
- Comparativas de APIs gratuitas y reliability de yfinance (2026): https://dev.to/tildalice/yfinance-alternatives-2026-7-free-apis-compared-o4g · https://orthogonal.info/i-tested-4-free-stock-market-apis-heres-which-one-actually-works-for-side-projects/ · https://usahousinginformation.com/yahoo-finance-too-many-requests/
- Cognito (50.000 MAU gratuitos) y resumen del free tier 2026: https://infratally.com/articles/aws-free-tier-2026/
- Cierre de AWS App Runner a nuevos clientes (30 de abril de 2026): https://agentdeals.dev/aws-free-tier-2026

> **Nota:** los precios y límites de servicios cambian con frecuencia. Verifica las páginas oficiales de AWS y Alpaca antes de desplegar y vuelve a revisarlas periódicamente.