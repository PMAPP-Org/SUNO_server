# Runbook: despliegue y rollback

Cómo poner una versión en producción y cómo volver a la anterior. Producción es **un VPS con Docker Compose** (ADR-010), con el código en `/opt/visitacampo` y el `.env` en `/opt/visitacampo/infra/.env` (permisos `600`, dueño `deploy`).

> Este runbook describe el **procedimiento manual**, que funciona desde hoy. Cuando exista el workflow de despliegue automático, este documento se actualiza para usarlo y el manual queda como plan B.

## Reglas

- Solo se despliega una **release** (tag `vX.Y.Z` creado por release-please), nunca una rama.
- Antes de cada despliegue debe existir un **backup reciente**: la métrica del último backup tiene menos de 24 h, o lo fuerzas tú.
- Cada despliegue se anota en el issue de la release o en el registro de operación: versión, hora y quién.

## Primer despliegue (servidor nuevo)

1. Servidor aprovisionado según el script de aprovisionamiento (pendiente): usuario `deploy`, SSH solo con llave, firewall con 22, 80 y 443, Docker.
2. DNS del dominio apuntando a la IP del VPS.
3. Código:
   ```bash
   sudo mkdir -p /opt/visitacampo && sudo chown deploy: /opt/visitacampo
   git clone https://github.com/Victor-Pulido/Seguimiento_Proyectos.git /opt/visitacampo
   cd /opt/visitacampo && git checkout vX.Y.Z
   ```
4. `.env` de producción a partir de `infra/.env.example`, con secretos **nuevos** (nunca los de la plantilla):
   ```bash
   cp infra/.env.example infra/.env && chmod 600 infra/.env
   # DJANGO_SECRET_KEY:     python3 -c 'import secrets; print(secrets.token_urlsafe(64))'
   # JWT (Ed25519, base64): openssl genpkey -algorithm ed25519 | base64 -w0
   # DJANGO_SETTINGS_MODULE=config.settings.prod   DJANGO_DEBUG=false
   # COMPOSE_FILE=docker-compose.yml:docker-compose.monitoring.yml
   bash infra/scripts/check_envvars.sh infra/.env infra/.env.example
   ```
   Que no quede ningún `CAMBIAR_EN_PRODUCCION`: `grep CAMBIAR infra/.env` no debe devolver nada.
5. Arrancar: `cd infra && docker compose up -d --build`.
6. Organización y primer usuario: `docker compose exec api python manage.py bootstrap_demo --org-name "<cliente>" --username "<usuario>"`. La contraseña generada se muestra una sola vez.
7. **Usuario del back-office con MFA**: el Admin exige segundo factor, sin excepciones.
   ```bash
   docker compose exec api python manage.py createsuperuser
   docker compose exec api python manage.py enroll_admin_totp <usuario>
   ```
   El segundo comando muestra **una sola vez** la URI para la app de autenticación y 10 códigos de respaldo. Guárdalos en el gestor de contraseñas. Si se pierde el teléfono, vuelve a ejecutarlo con `--replace`.
8. Verificar (sección *Comprobaciones*).

## Actualizar a una versión nueva

```bash
cd /opt/visitacampo
docker compose -f infra/docker-compose.yml run --rm backup backup.sh   # backup previo
git fetch --tags && git checkout vX.Y.Z
cd infra && docker compose up -d --build
```

La API aplica las migraciones al arrancar (`entrypoint.sh`) y el worker espera a que la API esté sana. Después, las comprobaciones.

## Comprobaciones tras desplegar

- [ ] `docker compose ps`: todo `running`, con `api` en `healthy`.
- [ ] `curl -s -o /dev/null -w '%{http_code}' https://<dominio>/v1/readyz` devuelve `200`.
- [ ] `curl -sI https://<dominio>/v1/healthz` incluye una sola cabecera `strict-transport-security`.
- [ ] Un login de prueba desde la app funciona.
- [ ] `/admin/` responde 404 desde fuera de `CADDY_ADMIN_ALLOWED_IPS` y, por túnel SSH, pide usuario, contraseña **y código** de la app de autenticación.
- [ ] En Grafana todo está en verde y en Alertmanager no hay alertas críticas.

## Rollback

Si una comprobación falla o aparece un error grave tras desplegar:

1. **Sin migraciones nuevas en la versión fallida** (mira el CHANGELOG o `git diff vANTERIOR vNUEVA -- server/visitacampo/*/migrations`):
   ```bash
   cd /opt/visitacampo && git checkout vANTERIOR && cd infra && docker compose up -d --build
   ```
2. **Con migraciones nuevas**: el código anterior puede no funcionar sobre el esquema nuevo. Restaura el backup tomado justo antes del despliegue ([`restauracion.md`](restauracion.md)) y luego despliega `vANTERIOR`. Lo cambiado después del backup se pierde en el servidor, pero los teléfonos lo reenvían si aún no lo habían sincronizado.
3. Anota el rollback y abre un issue con la causa. La versión fallida no se vuelve a desplegar hasta tener una corrección con su release.
