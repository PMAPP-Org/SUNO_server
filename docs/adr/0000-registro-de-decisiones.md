# ADR-0000: Registro de decisiones de arquitectura

- **Estado:** Aceptada
- **Fecha:** 2026-11-17

## Contexto

Las decisiones de SUNO estaban repartidas: catorce ADR dentro de [`../08-arquitectura-propuesta-suno.md`](../08-arquitectura-propuesta-suno.md) y el resto en specs de `plans/`, cuerpos de PR y comentarios del código. Para saber por qué algo es como es había que conocer la historia del proyecto.

## Decisión

Registramos cada decisión de arquitectura en un archivo propio de esta carpeta, con el formato de Michael Nygard:

- **Nombre:** `NNNN-titulo-en-kebab.md`, numeración consecutiva de cuatro cifras.
- **Secciones:** Estado, Fecha, Contexto, Decisión, Consecuencias y Referencias (spec, PR, commit o archivo que lo prueba).
- **Estados:** `Propuesta`, `Aceptada`, `Reemplazada por ADR-NNNN`. Una decisión aceptada que aún no está en `master` lo dice en su estado.
- **Inmutables.** Un ADR aceptado no se reescribe. Si la decisión cambia, se escribe un ADR nuevo que la reemplaza y en el viejo solo se cambia el estado a «Reemplazada por ADR-NNNN». Se permiten erratas y enlaces rotos.
- **Dónde:** las decisiones de backend y las transversales van aquí (SUNO_server). Las propias de la app Flutter van en `docs/adr/` de SUNO_client, que enlaza a estas cuando depende de ellas.
- **Cuándo:** cada pieza que toma una decisión de arquitectura entrega su ADR en el mismo PR ([`CONTRIBUTING.md`](../../CONTRIBUTING.md#documentación)).

**Numeración heredada.** Los ADR-001…ADR-014 de doc 08 conservan su número: ADR-001 es `0001`, y así hasta `0014`. Se resumen aquí y enlazan al texto completo de doc 08, que conserva las alternativas evaluadas. Del `0015` en adelante son decisiones posteriores, reconstruidas de `plans/`, del historial de git y de los PR. Solo se registran decisiones con evidencia.

## Consecuencias

- Una decisión se encuentra por su número y su título, sin leer planes de miles de líneas.
- Doc 08 queda como documento histórico; la fuente vigente es esta carpeta más [`../arquitectura/`](../arquitectura/README.md).
- Cada cambio de rumbo deja rastro: el ADR viejo y el nuevo se enlazan.

## Referencias

- Michael Nygard, «Documenting Architecture Decisions» (2011).
- [`../08-arquitectura-propuesta-suno.md` §5](../08-arquitectura-propuesta-suno.md#5-architecture-decision-records).
