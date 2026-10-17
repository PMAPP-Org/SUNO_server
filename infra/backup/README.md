# Backups (servicio `backup`)

Copias de seguridad cifradas de PostgreSQL y de los adjuntos (ISO/IEC 27001:2022 A.8.13). El servicio corre dentro del mismo `docker compose` que la API.

## Qué hace

Cada `BACKUP_INTERVAL_HOURS` (24 h por defecto) y una vez al arrancar, `backup.sh`:

1. Hace un `pg_dump` en formato *custom* y comprueba que se puede leer.
2. Lo **cifra con [age](https://age-encryption.org) usando una llave pública**. El servidor no tiene la llave privada: quien comprometa el servidor o el bucket no puede leer los backups.
3. Lo sube a `BACKUP_REMOTE` como `db/visitacampo-AAAAMMDDTHHMMSSZ.dump.age`. Primero se cifra en disco y solo después se sube, así que un `pg_dump` que falle a medias nunca deja en el bucket un archivo truncado que parezca válido.
4. Copia los adjuntos desde `BACKUP_BLOBS_SOURCE` a `blobs/`. Usa `copy` y no `sync`: los adjuntos son inmutables (ADR-007), y un borrado en origen no se propaga al backup.
5. Borra los volcados de más de `BACKUP_RETENTION_DAYS` (30 por defecto). Los adjuntos no rotan.
6. Escribe la métrica `visitacampo_backup_last_success_timestamp_seconds` para el monitoreo. Si no avanza, se dispara la alerta `BackupStale`; si nunca existió, `BackupMissing`.

Sin `BACKUP_AGE_RECIPIENT` el servicio queda **desactivado**, que es lo normal en desarrollo, y lo indica en su log.

El contenedor corre sin root (UID 999), con el sistema de archivos de solo lectura, `/tmp` en memoria, sin capacidades de Linux y con `no-new-privileges`.

## Puesta en marcha (una vez, antes de producción)

1. **Generar el par de llaves** en un equipo de confianza, nunca en el servidor:

   ```bash
   docker run --rm -it visitacampo-backup:local age-keygen
   # o, con age instalado: age-keygen -o visitacampo-backup.key
   ```

   - La línea `# public key: age1…` va a `BACKUP_AGE_RECIPIENT` en el `.env` del servidor.
   - La línea `AGE-SECRET-KEY-1…` es la **llave privada**. Guárdala en el gestor de secretos del equipo **y** en una copia offline (papel o USB cifrado en lugar seguro). **Si se pierde, los backups son irrecuperables.** No la copies al servidor.

2. **Crear un bucket de R2 solo para backups**, distinto del de adjuntos, con un token de API limitado a ese bucket (*Object Read & Write*). Rellena `BACKUP_S3_*`. Recomendado: una regla de *bucket lock* en R2 con un plazo igual o mayor que `BACKUP_RETENTION_DAYS`, para que ni un atacante con las credenciales del servidor pueda borrar los backups antes de tiempo. Con el lock activo, `rclone delete` no borra lo que sigue protegido, y es lo esperado.

3. Definir `BACKUP_BLOBS_SOURCE`: `blobs:<bucket-de-adjuntos>` con `STORAGE_BACKEND=s3`, o `/blobs` con `localfs`.

## Restaurar

Hace falta la llave privada. Se monta solo durante la restauración y se borra después.

```bash
cd infra
# Restaura el volcado más reciente sobre la BD configurada. BORRA su contenido actual.
docker compose run --rm \
  -v /ruta/segura/visitacampo-backup.key:/run/secrets/age-identity:ro \
  -e BACKUP_AGE_IDENTITY_FILE=/run/secrets/age-identity \
  backup restore.sh latest

# O un volcado concreto:
#   ... backup restore.sh db/visitacampo-20261017T060000Z.dump.age
```

Después de restaurar: `docker compose restart api worker`.

Los adjuntos restaurados están en `blobs/` del bucket de backups. Se copian de vuelta al bucket de la API con `rclone copy backup:<bucket>/blobs blobs:<bucket-adjuntos>`.

## Cómo se verifica

`.github/workflows/backup.yml` hace un **simulacro completo en cada PR y cada semana**:
1. Siembra datos y toma la huella SHA-256 de `pg_dump --data-only`.
2. Ejecuta `backup.sh` y comprueba que el archivo subido está cifrado con age y que no contiene la firma de `pg_dump` en claro.
3. **Borra toda la BD** y comprueba que una llave equivocada no restaura.
4. Restaura con `restore.sh latest` y exige que la huella coincida byte a byte.

En CI el destino es un directorio local (`infra/ci/docker-compose.backup-drill.yml`). La conexión real con R2 se comprueba en el primer despliegue: ejecuta `docker compose run --rm backup backup.sh` y revisa que el objeto aparece en el bucket.

**Recomendado en producción:** un simulacro de restauración trimestral sobre una BD de prueba, anotado en el registro de operación (A.5.37).
