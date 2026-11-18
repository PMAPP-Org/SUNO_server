# ADR-0030: Libro de costos: solo cuenta lo aprobado y lo aprobado se reversa

- **Estado:** Aceptada
- **Fecha:** 2026-11-12

## Contexto

El costo real (AC) se registra en SUNO. Debe ser auditable: nadie aprueba lo propio y una corrección no borra la historia.

## Decisión

- Un libro común (`CostEntry`) para todos los tipos de costo: borrador → aprobado o rechazado. **Solo cuenta lo aprobado.**
- Lo aprobado no se edita ni se borra: se **reversa** con un movimiento de signo contrario, con fecha de hoy, una sola vez.
- **Sin autoaprobación:** no aprueba ni rechaza quien registró ni quien modificó por última vez. Aprobar exige soporte adjunto. Rechazar exige motivo.
- Estas reglas son **restricciones de la BD** además del servicio (`cost_entry_approval_ok`, `cost_entry_rejection_note`, `cost_entry_reversal_approved`, `reverses` único, `cost_entry_amount_sign`).
- Valor único con IVA incluido. Asignación a ítem, capítulo, selección de ítems de un capítulo o indirecto; tarea opcional.
- Cada pieza posterior (contratos, materiales, nómina) reemplaza el registro genérico de su tipo por su captura especializada y escribe en el mismo libro.

## Consecuencias

- El control siempre lee una sola fuente de AC.
- Las auditorías `cost.*` guardan quién, qué y cuándo.

## Referencias

- `plans/2026-11-12-costos-pieza6a-costo-real-design.md`; PR #75.
- `server/suno/core/models/cost.py`, `server/suno/costs/services.py`.
