# ADR-0032: Lo aprobado es lo firmado

- **Estado:** Aceptada
- **Fecha:** 2026-11-14

## Contexto

Un acta de contrato (6b) o un corte de consumos (6c) se imprime, se firma en papel y se sube escaneado. Entre la firma y la aprobación los datos pueden cambiar (otra validación de campo, otro otrosí, una salida nueva).

## Decisión

- La aprobación **recalcula** el documento y lo **rechaza** si los totales o los valores por línea difieren de lo que se firmó.
- Subir el acta firmada no cuenta como edición; editar el borrador quita la firma.
- En 6c, la huella del corte incluye cada línea (id, material, cantidad y valor) y la aprobación no reescribe el resumen impreso.
- No se anula el acta anterior ni se aprueba un otrosí con un acta abierta.

## Consecuencias

- Lo que entra al libro coincide con el papel firmado en montos.
- Las descripciones del libro se arman al aprobar con el nombre vigente del ítem, no con el impreso: un renombre entre firma y aprobación cambia el texto, no los montos (problema conocido).
- Limitación conocida: el PDF de un acta vieja muestra el contratado vigente de hoy.

## Referencias

- `plans/suno-progreso.md` (pieza 6b, «Revisión de código»; pieza 6c); PR #79 y #84.
- `server/suno/contracts/certificates.py`.
