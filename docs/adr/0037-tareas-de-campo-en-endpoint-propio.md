# ADR-0037: Las tareas de campo se descargan por un endpoint propio, no por el pull

- **Estado:** Aceptada
- **Fecha:** 2026-10-31

## Contexto

El agente necesita sus tareas del cronograma (y desde 4a, ítems planeados, ubicaciones y el catálogo de ítems del proyecto). Los responsables y la importación del cronograma cambian la lista entera de golpe.

## Decisión

- `GET /v1/projects/tasks` devuelve la **lista completa** del agente con una `version` (huella); si no cambió, responde `changed: false`.
- No entra en el pull por `change_seq`: la app reemplaza su copia completa en cada ciclo y la borra al cambiar de usuario u organización.
- Solo se reporta avance en tareas de detalle, y quien vincula una tarea debe ser su responsable efectivo.

## Consecuencias

- Sin tombstones ni cursor para tareas; una reasignación se refleja en el siguiente ciclo.
- La respuesta crece con las tareas del agente, no con el proyecto.

## Referencias

- `plans/suno-progreso.md` (etapa 1, avance desde campo, «Decisiones que cambian la spec» 1, 4, 7 y 13); PR #46.
- `server/suno/projects/field_tasks.py`, `server/suno/projects/api.py`.
