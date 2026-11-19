# ADR 0002 - La ventana diaria del optimizador incluye el dia de ejecucion

- **Estado:** aceptado (decision del humano, 2026-11-19)
- **Registra:** la desviacion de `AGENTS.md` seccion 5.1 ("para decidir en el dia t
  solo se usan datos hasta t-1") detectada en mistakes.md QA-013.

## Contexto

El optimizador diario corre a las 16:30 `America/New_York`, despues del cierre.
La ingesta del dia t se escribe en Bronze y Silver antes de optimizar, de modo que
`SilverReturnsRepository.load` toma las particiones mas recientes, incluida la
de t. La ventana de 252 dias termina en el retorno de t.

`AGENTS.md` 5.1 y 5.6 exigen que la decision del dia t use datos hasta t-1.

## Decision

Se mantiene la ventana que incluye el dia de ejecucion. El cierre de t ya es
conocido a las 16:30 ET, asi que no es informacion futura respecto al momento de
la decision. No se anade filtro `as_of` en `load`.

## Consecuencias

- **Desviacion documentada** de `AGENTS.md` 5.1 para el regimen diario. Debe
  reflejarse en el manual antes de cualquier auditoria externa.
- **Inconsistencia con el backtest:** `BacktestEngine` decide con
  `matrix.values[row - window : row]`, es decir, solo hasta t-1. Las metricas del
  optimizador diario y las del backtest no usan exactamente la misma ventana.
- **Reruns y backfills NO quedan cubiertos por esta decision.** Un rerun de una
  fecha pasada leeria particiones posteriores a esa fecha. Ese caso sigue siendo
  look-ahead y queda abierto (QA-013 se cierra solo para el regimen diario). Para
  cerrarlo hace falta acotar `load` por `run_date` en un ADR posterior.

## Alternativas consideradas

- **Filtrar a t-1 en `load`** (cumple AGENTS 5.1 literalmente). Rechazada por el
  humano: cambiaria cada propuesta diaria sin aportar informacion nueva.
