# Runbook: respuesta a alertas e incidentes

Qué hacer cuando suena una alerta o se detecta un incidente. El procedimiento general (severidades, tiempos, notificación y post-mortem) está en [`docs/seguridad/politicas.md`](../seguridad/politicas.md) §8; aquí van los pasos concretos.

**Antes de tocar nada:** anota la hora y abre un issue con la etiqueta `incidente`, o usa el que abrió `uptime.yml`. Cada acción queda escrita allí. Si puede haber datos personales afectados, avisa al responsable de seguridad **ya**: hay plazos de notificación.

Acceso común a todas las secciones:

```bash
ssh deploy@<servidor>                     # solo con llave
cd /opt/visitacampo/infra                 # ruta de despliegue (ver despliegue.md)
docker compose ps                         # estado y healthchecks de los servicios
docker compose logs --since 30m api worker caddy
```

Paneles, por túnel SSH: Grafana en http://localhost:3000, Alertmanager en http://localhost:9093 ([`infra/monitoring/README.md`](../../infra/monitoring/README.md)).

## ApiDown — `/v1/healthz` no responde (S1)

1. `docker compose ps`: ¿están `api` y `caddy` en `running (healthy)`?
2. **`api` reinicia en bucle:** revisa `docker compose logs api --tail 100`.
   - `ImproperlyConfigured`: falta o es débil una variable (`DJANGO_SECRET_KEY`, la llave JWT o `DJANGO_ALLOWED_HOSTS`). Corrígela en `.env` y ejecuta `docker compose up -d api`.
   - Errores de migración tras un despliegue: haz **rollback** ([`despliegue.md`](despliegue.md#rollback)).
3. **`caddy` caído o sin certificado:** `docker compose logs caddy | grep -i -E 'error|acme'`. Comprueba que el DNS del dominio apunta al servidor y que los puertos 80 y 443 están abiertos.
4. **Ambos sanos pero falla desde fuera:** revisa el firewall del host o el proveedor (estado de la red del VPS).
5. Si nada de lo anterior lo resuelve en 30 min y hubo un despliegue reciente, haz rollback.

## ApiNotReady — `/v1/readyz` falla (S1)

La API responde pero no llega a la BD o al almacenamiento de adjuntos.
1. `docker compose ps db`. Si no está `healthy`, sigue con **PostgresDown**.
2. Almacenamiento (R2): `docker compose logs api | grep -i -E 's3|r2|storage'`. Revisa las credenciales `STORAGE_S3_*` y el estado de Cloudflare.

## PostgresDown (S1)

1. `docker compose logs db --tail 100`.
2. Disco lleno (`No space left on device`): sigue con **HostDiskAlmostFull**.
3. Volumen corrupto o BD irrecuperable: [`restauracion.md`](restauracion.md).

## HostDiskAlmostFull / HostDiskWillFillIn24h (S3 / S2)

```bash
df -h; docker system df
sudo du -xh /var/lib/docker --max-depth=2 | sort -h | tail
```
- Imágenes viejas: `docker image prune -a --filter "until=168h"` (conserva la versión actual y la anterior para el rollback).
- Logs de contenedores: la rotación la fija el aprovisionamiento. Si falta, `sudo truncate -s 0` sobre el log que más ocupe y corrige la configuración.
- Métricas de Prometheus: baja `PROMETHEUS_RETENTION`.
- **Nunca** borres volúmenes (`docker volume rm`) para liberar espacio: `pgdata` es la BD.

## HostMemoryLow / HostCpuHigh (S3)

`docker stats --no-stream`. Si es el monitoreo, puede apagarse temporalmente (`docker compose stop grafana`). Si es la API, revisa picos de tráfico en los logs de Caddy. Si se repite, toca dimensionar el VPS (A.8.6).

## TlsCertExpiringSoon / TlsCertExpiryCritical (S3 / S1)

Caddy renueva solo a 30 días del vencimiento; si la alerta salta, la renovación está fallando.
1. `docker compose logs caddy | grep -i acme`.
2. Causas típicas: el DNS ya no apunta al servidor, el puerto 80 está cerrado (desafío HTTP-01) o se superó el límite de Let's Encrypt.
3. Tras corregirlo: `docker compose restart caddy` y comprueba con `curl -vI https://<dominio>/v1/healthz`.

## BackupStale / BackupMissing (S2)

1. `docker compose logs backup --tail 50`.
2. `DESACTIVADO`: falta `BACKUP_AGE_RECIPIENT` o `BACKUP_REMOTE` en `.env`.
3. Fallo de subida: revisa las credenciales `BACKUP_S3_*` y que el bucket existe.
4. Forzar un backup y comprobarlo: `docker compose run --rm backup backup.sh`.

## Llave o secreto comprometido (S1)

1. **Rotar** el secreto (inventario en [`docs/seguridad/inventario-activos.md`](../seguridad/inventario-activos.md) §3) y `docker compose up -d`.
   - `DJANGO_SECRET_KEY` invalida las sesiones del Admin y la correlación de las IP de auditoría.
   - La llave JWT invalida todos los access tokens: los teléfonos se re-autentican con su refresh. **Comprometida, no se deja como `JWT_PREVIOUS_KEY`**: eso aceptaría durante 15 minutos los tokens que firme el atacante. Se cambia y punto.
2. Credenciales de R2 o GitHub: revócalas en el proveedor **antes** de crear las nuevas.
3. Llave privada de backups: genera un par nuevo, cambia `BACKUP_AGE_RECIPIENT` y decide si re-cifrar o borrar los backups existentes.
4. Revisa `AuditEvent` y los logs para acotar qué se hizo con el secreto.

## Rotar la llave JWT (planificado, sin cortar sesiones)

Rotación periódica o preventiva, con la llave **no** comprometida (si lo está, sección anterior). Dos claves vivas durante 15 minutos: la nueva firma y la anterior solo verifica los tokens que ya emitió (ADR-008, `server/visitacampo/authn/keys.py`).

1. Genera la llave nueva (base64 en una línea, como `JWT_SIGNING_KEY`):
   ```bash
   openssl genpkey -algorithm ed25519 | base64 -w0
   ```
2. En `infra/.env`: copia el valor **actual** de `JWT_SIGNING_KEY` a `JWT_PREVIOUS_KEY` y pon la nueva en `JWT_SIGNING_KEY`. (Con secretos de archivo: `JWT_PREVIOUS_KEY_FILE` apunta al archivo anterior y `JWT_SIGNING_KEY_FILE` al nuevo. Basta la clave pública de la anterior: `openssl pkey -pubout`.)
3. `docker compose up -d api worker`. Comprueba que entra un login y que `/v1/sync/pull` responde con un token emitido **antes** del cambio.
4. Pasados al menos 15 minutos (`JWT_ACCESS_TTL_SECONDS`), vacía `JWT_PREVIOUS_KEY` y repite el `up -d`. Los tokens de la llave anterior dejan de valer; los teléfonos ya se habían renovado con su refresh.
5. Guarda la llave nueva donde se guardan los secretos (`docs/seguridad/inventario-activos.md` §3) y destruye la copia anterior.

Cada access token lleva en la cabecera `kid` la huella de la clave que lo firmó: en un incidente, decodifica la cabecera (no la firma) para saber con cuál se emitió.

## Teléfono de campo perdido o robado (S2)

Los datos en el teléfono están cifrados (SQLCipher) y los tokens en el almacén seguro del sistema. Para cortar el acceso:
1. En el Admin, revoca los refresh tokens del dispositivo, o desactiva al usuario si hay duda.
2. El access token vigente caduca en ≤ 15 min.
3. Las visitas no sincronizadas de ese teléfono se pierden: avisa al cliente.

## Sospecha de acceso no autorizado o fuga de datos (S1)

1. **Contener:** restringe `CADDY_ADMIN_ALLOWED_IPS`, desactiva las cuentas implicadas y, si hace falta, para la API (`docker compose stop api`).
2. **Preservar** antes de cambiar nada:
   ```bash
   docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t audit_event -t axes_accessfailurelog > evidencias-$(date -u +%Y%m%dT%H%M).sql
   docker compose logs --no-color > logs-$(date -u +%Y%m%dT%H%M).txt
   ```
   Guárdalo fuera del servidor, con su hash (`sha256sum`).
3. Notificación: cliente afectado y SIC (§8.4 de las políticas).
4. Post-mortem en ≤ 5 días hábiles.
