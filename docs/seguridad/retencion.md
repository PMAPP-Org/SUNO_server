# Retención y eliminación de datos

Plazos de conservación de los datos que VisitaCampo guarda por razones de seguridad u operación, y el mecanismo **automático** que los aplica. Sirve de evidencia para ISO/IEC 27001:2022 A.8.10 (eliminación de información) y A.8.15 (registro), y para el principio de temporalidad de la Ley 1581 de 2012: no conservar datos personales más tiempo del necesario para su finalidad.

Los plazos se configuran con variables de entorno, con valor por defecto en `server/config/settings/base.py`. Cambiar un plazo es una decisión del responsable del tratamiento y se registra en un PR que actualiza este documento.

| Dato | Contiene datos personales | Plazo por defecto | Variable | Mecanismo |
|---|---|---|---|---|
| `AuditEvent` (traza de auditoría) | Usuario actor; IP solo como HMAC | 365 días | `AUDIT_EVENT_RETENTION_DAYS` | Periódico `security.purge_logs`, diario, por organización |
| `AccessFailureLog` / `AccessLog` de django-axes | Usuario e IP **en claro** | 90 días | `AXES_LOG_RETENTION_DAYS` | Periódico `security.purge_logs` (API de axes) |
| Tombstones de sync (filas borradas lógicamente) | Según la visita | Ventana de sync (`sync/retention.py`) | — | Periódico `sync.purge_tombstones` |
| Adjuntos subidos y nunca confirmados | Fotos | 24 h | `ATTACHMENT_UNCONFIRMED_MAX_AGE_HOURS` (setting) | Periódico `attachments.gc_unconfirmed_uploads` |
| Copias de seguridad de la BD | Todo el contenido | Ver `infra/backup/README.md` | `BACKUP_RETENTION_DAYS` | Rotación en el job de backup |

**Por qué estos plazos**

- **Traza de auditoría, 1 año**: cubre la investigación de un incidente descubierto tarde y la revisión anual del SGSI. No guarda la IP en claro.
- **Registros de axes, 90 días**: son útiles para detectar fuerza bruta y relleno de credenciales recientes, y guardan IP y nombre de usuario en claro, también de intentos con usuarios que no existen. Pasado ese plazo su valor de seguridad es bajo y su coste en privacidad no.

**Cómo se verifica**

- `server/visitacampo/jobs/tests/test_retention.py` comprueba que la purga borra lo vencido, conserva lo vigente y nunca toca la traza de otra organización.
- El periódico se ejecuta en el servicio `worker` (`infra/docker-compose.yml`). El DAST de CI comprueba que el worker procesa la cola contra el stack de producción. El worker vuelve a programar los periódicos cada `JOB_BOOTSTRAP_INTERVAL_SECONDS` (300 s), así que una organización nueva queda cubierta sin reiniciar nada.
- El resultado de cada pasada (cuántas filas se borraron) queda en `job.output`, visible para ADMIN.

**Lo que este documento no cubre (pendiente)**: el plazo de conservación de los datos de negocio (visitas, fotos, respuestas de formulario). Depende del contrato con cada organización cliente y de su finalidad, y debe definirlo el responsable del tratamiento antes de producción.
