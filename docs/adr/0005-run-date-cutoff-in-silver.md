# ADR 0005 - Corte inclusivo por run_date en Silver

- **Estado:** aceptado (implementa el cierre de QA-013 acordado)
- **Registra:** cómo se acota `SilverReturnsRepository.load` por la fecha de
  ejecución sin romper la decisión del ADR 0002.

## Contexto

QA-013 (MEDIUM): un rerun o backfill de una fecha pasada leía las particiones
Silver más recientes del bucket, incluidas las posteriores a esa fecha. La
decisión del día t usaba datos futuros (look-ahead, AGENTS.md §5.1/§5.6).

El ADR 0002 (decisión del humano) mantuvo la ventana diaria **incluyendo** el
retorno del propio día t, porque el cierre ya es conocido a las 16:30 ET, y
dejó abierto el caso reruns: "Para cerrarlo hace falta acotar `load` por
`run_date` en un ADR posterior". Este es ese ADR.

## Decisión

`load(symbols, window_days, max_days, run_date)` aplica un corte **inclusivo**
(`date <= run_date`) sobre las filas de cada partición:

- **Régimen diario:** `run_date` es hoy y la partición de hoy contiene el
  retorno de t; el corte inclusivo la mantiene. El comportamiento diario no
  cambia nada respecto al ADR 0002.
- **Reruns de fechas pasadas:** toda partición posterior a `run_date` se
  descarta; la decisión del día t solo usa datos hasta t. Ya no hay
  look-ahead en reruns.
- **Backtest semanal:** el handler pasa el `run_date` del evento; una
  re-ejecución semanal no puede leer particiones más nuevas que su reporte.
- Sin `run_date` (runs locales con caché) el corte se desactiva y el
  comportamiento es el anterior.

## Consecuencias

- El régimen diario queda **cubierto por ADR 0002** y los reruns por este;
  entre ambos no queda look-ahead conocido en Silver.
- No aplica al historial del backtest en sus corridas normales: el corte solo
  actúa si existen particiones futuras al `run_date`.
- Los runs de `backtest/local.py` no pasan `run_date` (caché local, sin
  semántica de rerun).
- Los tests fijan las tres conductas: rerun ignora el futuro, el día t se
  mantiene y un rerun sin historial previo falla de forma controlada.
