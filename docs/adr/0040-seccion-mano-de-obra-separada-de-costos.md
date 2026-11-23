# ADR-0040: Sección "Mano de obra" separada de Costos

- **Estado:** Aceptada (amplía [ADR-0022](0022-matriz-de-permisos-por-seccion.md))
- **Fecha:** 2026-11-18

## Contexto

El tareo de la pieza 6d lleva nombres, documentos y salarios. La sección Costos la tienen quienes ven y aprueban costos de obra (contadores, residentes, interventoría), que no necesitan ver cuánto gana cada trabajador. Usar Costos para el tareo habría abierto esos datos a todos ellos.

## Decisión

- Sección nueva **`LABOR` ("Mano de obra")** en la matriz fija de `core/access.py`: Gestor (crear, leer, editar, borrar, aprobar, rechazar, anular, exportar), Revisor (sin anular), Colaborador (crear, leer, editar, borrar y exportar; solo lo suyo) y Consulta (solo leer: los exportes llevan nombres y salarios). Bajar el escaneo de la planilla firmada **también es exportar** (403 para Consulta, auditado como el PDF y el Excel; revisado con el usuario el 2026-11-19).
- La migración `0031` da la sección a la administración existente (`OWNER`, `ADMIN`) como Gestora con alcance de organización, igual que la `0028` con Costos; `LEGACY_GRANTS` hace lo mismo con las membresías nuevas. A los demás se la da el administrador de accesos.
- El **registro** (trabajadores, jornales, factor y conciliación) pide la sección con alcance de **toda la organización**: leer para verlo, `create` y `update` para gestionarlo. Un alcance por proyectos solo abre la pestaña Tareo de esos proyectos, con el documento enmascarado.
- En Costos y Control la mano de obra aparece como costos por destino ("Tareo TA-001"), **sin nombres ni salarios**; el enlace a la planilla solo se muestra a quien lee Mano de obra en el proyecto.
- El costo de tareo conserva como soporte la planilla firmada (lleva nombres y documentos), pero **solo la descarga quien lee Mano de obra en el proyecto**: para los demás `/costos/<movimiento>/soporte/` responde 404 y el detalle del movimiento no muestra el enlace (el exporte del libro no enlaza soportes). Revisado con el usuario el 2026-11-19 (hallazgo C1 de la revisión).

- **Decisión aceptada (revisada con el usuario el 2026-11-19, hallazgo M2):** el selector de la asignación muestra a **todos los trabajadores activos de la organización** también a quien tiene Mano de obra solo en algunos proyectos. Hace falta para asignar a alguien que aún no está en la obra, y solo muestra nombre y documento enmascarado (sin jornal ni historial).

## Consecuencias

- Quien aprueba costos no ve salarios salvo que también tenga Mano de obra.
- Una sección más en la matriz de accesos de la web y en el perfil de sesión (`sections`) que recibe la app; la app ignora secciones que no conoce.

## Referencias

- Spec 6d, sección 3.5; `server/suno/core/access.py`, `server/suno/core/policies.py` (`can_*_labor`, `can_*_labor_registry`), `server/suno/core/migrations/0031_labor.py`.
