# ADR-0027: Modelo de valor ganado de SUNO

- **Estado:** Aceptada
- **Fecha:** 2026-11-11

## Contexto

Hay varias formas de repartir el valor planeado y de medir lo ganado. La elección debe ser explicable a la oficina de obra y no depender de datos sin validar.

## Decisión

- **PV:** el valor de cada par ítem × tarea se reparte parejo por los días laborables de la línea base de la tarea. Hitos y ventanas sin días laborables cargan en su fin.
- **EV:** cantidades **validadas** × precio de la versión vigente, acotado por par a [0, cantidad planeada]. Las tareas por % usan su % validado al corte.
- **Índices:** SV, SPI y, desde 6a, CPI a dos decimales; estado con umbrales 0,95 y 0,85 sobre el valor redondeado.
- **EAC** solo como `AC + (BAC − EV) / CPI`, con VAC y TCPI; sin rangos optimista y pesimista.
- **Indirectos separados:** CPI directo, CPI de administración (AC indirecto frente a la administración ganada) y CPI total.
- Fecha del registro = `captured_at` en hora de Colombia.

## Consecuencias

- Lo que el agente envía no mueve el EV hasta que alguien lo valida.
- La sobreejecución no suma EV: se avisa.
- Hay avisos explícitos para lo que no se puede medir (sin línea base, sin precio, tareas retiradas, costo sin asignar).

## Referencias

- `plans/2026-11-11-costos-pieza5-control-design.md`, `plans/2026-11-12-costos-pieza6a-costo-real-design.md`.
- `server/suno/control/earned_value.py`, `actual_cost.py`, `indices.py`.
