# Conector comunitario de Looker Studio (Google-side, no se despliega a AWS)

Conector de Apps Script que alimenta Looker Studio desde la **Reader API**
(read-only, Cognito JWT). Vive solo en Google; nunca se importa desde `src/`
ni se empaqueta en las Lambdas.

## Tablas que expone

| Tabla | Fuente (`GET`) | Granularidad |
|---|---|---|
| `weights` | `/proposal` | una fila por (modo, ticker): optimizer, actual y benchmarks |
| `metrics` | `/metrics` | una fila por (modo, métrica) |
| `monitor` | `/monitor` | una fila por ticker con pesos propuestos vs en vivo |
| `backtest` | `/backtest` | una fila por modo comparado (in/out de sample) |
| `backtest_candidates` | `/backtest` | una fila por combinación evaluada en la validación anidada |

Las métricas de folds individuales y la sensibilidad por ventana del backtest
no se aplanan (se agregan después si se necesitan).

## Instalación (rol humano, una vez)

1. **Terraform:** aplica el entorno dev; recupera
   `terraform -chdir=infra/envs/dev output looker_connector_client_id`
   y `..._client_secret` (sensitive), más `cognito_domain` y `api_endpoint`.
2. **Apps Script** (<https://script.google.com>): proyecto nuevo → pega
   `Code.gs`. Añade la librería **OAuth2** oficial de googleworkspace
   (repositorio `github.com/googleworkspace/apps-script-oauth2`; usa el
   *Library ID* publicado en su README — no lo copies de aquí).
3. **Script Properties** (Project Settings): `API_ENDPOINT`,
   `COGNITO_DOMAIN` (con `https://`), `CLIENT_ID`, `CLIENT_SECRET`.
   Los valores no viven en este repo (§12).
4. **Deploy → New deployment → API**: copia el *Deployment ID*; la URL del
   conector es `https://script.google.com/macros/d/<DEPLOYMENT_ID>/exec` y
   el callback `https://script.google.com/macros/d/<DEPLOYMENT_ID>/usercallback`.
5. **Registra el callback** en `infra/envs/dev`: define
   `looker_connector_callback_urls = ["https://script.google.com/macros/d/<DEPLOYMENT_ID>/usercallback"]`
   y aplica (la variable se creó vacía a propósito para este paso).
6. **Looker Studio:** Crear → Fuente de datos → pega la URL `/dev` del script
   (modo desarrollo) o la `/exec` del deployment → autoriza con tu usuario
   Cognito → elige tabla → Conectar.

## Operación

- El refresh token lo gestiona la lib OAuth2 (cap 90 días); un re-login
  trimestral como máximo.
- Looker Studio cachea: usa "Refresh data" para traer la última propuesta.
- Datos: read-only vía API Gateway con throttling (5 burst / 10 rps) — el
  conector no puede alterar portafolio ni enviar órdenes.

## Verificación y límites (honestidad §19)

- El flujo OAuth↔Cognito y los endpoints se verificaron contra la
  documentación oficial de Google y AWS el 2026-11-15; **el `Code.gs` no se
  ha ejecutado en el runtime de Apps Script** (no hay entorno local de
  Apps Script); la primera conexión real la hace el humano en el paso 6.
- Cualquier ajuste del runtime (p. ej. `cc.newUserError().throw()`,
  `addOptions`) se corrige ahí mismo; el contrato con la API no cambia.
