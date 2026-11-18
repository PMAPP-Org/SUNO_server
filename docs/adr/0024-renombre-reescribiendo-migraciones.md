# ADR-0024: Renombre a SUNO y «registro» reescribiendo las migraciones en el sitio

- **Estado:** Reemplazada por [ADR-0025](0025-solo-migraciones-aditivas-con-produccion.md)
- **Fecha:** 2026-11-06

## Contexto

El paquete se llamaba `visitacampo` y el concepto «visita». El producto pasó a llamarse SUNO y el concepto, «registro».

## Decisión

- Paquete `visitacampo` → `suno` (PR #57) y `Visit` → `Record` (PR #60).
- Reescribir las migraciones 0001–0014 en el sitio, con nombres de archivo y dependencias, sin `RenameModel`, porque «no hay producción y todos los ambientes recrean la base».
- Contrato con `SCHEMA_VERSION = 2` y `MIN_SUPPORTED_SCHEMA_VERSION = 2`, sin periodo de convivencia.
- Guardia `infra/scripts/check_naming.py` en CI.

## Consecuencias

El supuesto «no hay producción» dejó de ser cierto el 2026-11-08 con el despliegue continuo ([ADR-0017](0017-despliegue-continuo-de-master.md)): reescribir migraciones aplicadas habría roto la base de producción. Por eso se reemplazó.

## Referencias

- Spec y plan `plans/2026-11-06-suno-registro-rename-*.md`; `plans/suno-progreso.md` («Renombre: SUNO y registros»).
