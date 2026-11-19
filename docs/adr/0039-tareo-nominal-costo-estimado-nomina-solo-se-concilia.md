# ADR-0039: Tareo nominal con costo estimado; la nómina solo se concilia

- **Estado:** Aceptada
- **Fecha:** 2026-11-18

## Contexto

Hasta la pieza 6c, la mano de obra por jornal solo entraba al libro de costos como un movimiento manual de "Mano de obra" o "Nómina", sin saber quién trabajó ni en qué. Las constructoras llevan el tareo (quién trabajó, qué día y en qué frente) en papel o Excel y la nómina en su software de nómina, que liquida prestaciones, seguridad social, PILA y nómina electrónica.

## Decisión

- SUNO lleva el **tareo nominal**: un registro de trabajadores de la organización con datos personales mínimos (documento, nombre, cargo, ingreso y retiro), su jornal básico y un factor prestacional de la organización, ambos con vigencia.
- El **costo es estimado**: fracción de jornal × jornal vigente ese día × factor vigente ese día, redondeado por línea; el de un destino es la suma de sus líneas. Las horas extra se registran como fracción adicional (hasta 1,5 jornales por persona y día entre todas las obras).
- La **planilla** (por proyecto y periodo de hasta 31 días) se aprueba con el soporte firmado, por alguien distinto de quien la hizo, y "lo aprobado es lo firmado" ([ADR-0032](0032-aprobado-es-lo-firmado.md)). Al aprobar se congelan jornal, factor y valor y se escribe un costo de Mano de obra por destino con origen `timesheet` ([ADR-0031](0031-origen-unico-del-movimiento-del-libro.md)). Se corrige anulando la planilla (reversos de hoy) y haciendo otra.
- **SUNO no liquida nómina.** La nómina real se importa desde Excel solo para un **reporte de conciliación** (tareado contra pagado, por trabajador y por proyecto con prorrateo); no ajusta el libro.
- El tope diario se serializa con el orden de bloqueos de [ADR-0035](0035-orden-de-bloqueos.md): proyecto, planilla y filas de los trabajadores en orden de `pk`. Las tres importaciones (trabajadores, tareo y nómina) siguen [ADR-0036](0036-importaciones-en-dos-pasos.md) con un solo token (`LaborImportToken`).

## Consecuencias

- El costo real de la mano de obra en Control (AC y CPI) es una estimación con el factor de la organización; la diferencia con lo pagado se ve en la conciliación, no en el libro.
- SUNO pasa a tratar datos personales de trabajadores que no son usuarios (documento y salario): ver `docs/seguridad/tratamiento-datos-personales.md` (T-10) y [ADR-0040](0040-seccion-mano-de-obra-separada-de-costos.md).
- La captura en la app móvil, las novedades (incapacidades, vacaciones) y los recargos por tipo quedan fuera.

## Referencias

- Spec y plan de la pieza 6d: `plans/2026-11-18-costos-pieza6d-tareo-design.md`, `plans/2026-11-18-costos-pieza6d-tareo-plan.md`.
- `server/suno/labor/calc.py`, `timesheets.py`, `approval.py`, `payroll.py`; `server/suno/core/models/labor.py`.
