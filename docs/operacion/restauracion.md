# Runbook: restaurar desde un backup

Cuándo: la BD de producción se perdió o se corrompió, o hay que volver a un estado anterior (por ejemplo, tras borrar datos por error). El mecanismo y la custodia de llaves están en [`infra/backup/README.md`](../../infra/backup/README.md).

**Objetivos propuestos** ([`politicas.md`](../seguridad/politicas.md) §7): perder como máximo 24 h de datos sincronizados (RPO) y recuperar el servicio en 8 h (RTO). Las visitas que todavía no se habían sincronizado siguen en los teléfonos y se reenvían solas.

## Antes de empezar

- [ ] Tienes la **llave privada age** (gestor de secretos o copia offline). Sin ella no hay restauración.
- [ ] Sabes **qué volcado** restaurar: `latest`, o uno concreto si el problema es anterior al último backup.
  ```bash
  docker compose run --rm --entrypoint rclone backup lsf backup:<bucket>/db | sort | tail
  ```
- [ ] Lo anotaste en el issue del incidente.

## Pasos

1. **Parar lo que escribe** en la BD: `docker compose stop api worker`.
2. **Copiar el estado actual**, aunque esté dañado, por si hace falta para analizar:
   `docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > antes-de-restaurar.dump`
3. **Restaurar.** La llave se monta solo durante este comando:
   ```bash
   docker compose run --rm \
     -v /ruta/segura/visitacampo-backup.key:/run/secrets/age-identity:ro \
     -e BACKUP_AGE_IDENTITY_FILE=/run/secrets/age-identity \
     backup restore.sh latest      # o db/visitacampo-AAAAMMDDTHHMMSSZ.dump.age
   ```
4. **Arrancar:** `docker compose up -d api worker`. La API aplica las migraciones pendientes al arrancar.
5. **Verificar:**
   - `curl -s https://<dominio>/v1/readyz` responde 200;
   - en el Admin, las visitas recientes están y la última es de la hora esperada;
   - un teléfono de prueba sincroniza.
6. **Borrar la llave** del servidor si la copiaste (`shred -u`). No debe quedarse ahí.
7. Si se restauró un servidor nuevo: restaura también los adjuntos (`rclone copy backup:<bucket>/blobs blobs:<bucket-adjuntos>`) y comprueba que las fotos se ven.

## Simulacro trimestral

El CI ya hace un simulacro en cada PR ([`.github/workflows/backup.yml`](../../.github/workflows/backup.yml)), pero con datos sintéticos y sin R2. Cada trimestre se repite este runbook **completo** sobre un entorno de prueba con el backup real de producción, y se registra aquí. Es la evidencia de A.8.13 y del RTO.

| Fecha | Volcado restaurado | Duración (inicio → readyz OK) | Resultado | Quién |
|---|---|---|---|---|
| — | — | — | Pendiente: primer simulacro al tener producción | — |
