# Operación de VisitaCampo

Procedimientos documentados de operación (ISO/IEC 27001:2022 A.5.37). Cada runbook es una lista de pasos concretos para alguien con acceso al servidor y sin contexto previo.

| Runbook | Cuándo |
|---|---|
| [`despliegue.md`](despliegue.md) | Primer despliegue, actualizar a una release, comprobaciones y **rollback** |
| [`incidentes.md`](incidentes.md) | Suena una alerta (una sección por alerta) o hay un incidente de seguridad |
| [`restauracion.md`](restauracion.md) | Hay que restaurar la BD desde un backup; registro de simulacros |
| [`mapas-base.md`](mapas-base.md) | Importar o retirar un mapa base offline (MBTiles) para una organización |

Referencias:
- Backups: [`infra/backup/README.md`](../../infra/backup/README.md).
- Monitoreo y acceso a los paneles: [`infra/monitoring/README.md`](../../infra/monitoring/README.md).
- Políticas (severidades, plazos, notificación): [`docs/seguridad/politicas.md`](../seguridad/politicas.md).
- Reportar vulnerabilidades: [`SECURITY.md`](../../SECURITY.md).
