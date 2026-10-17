# Runbook: despliegue y rollback

Cómo poner una versión en producción y cómo volver a la anterior. Producción es **un VPS con Docker Compose** (ADR-010), con el código en `/opt/visitacampo` y el `.env` en `/opt/visitacampo/infra/.env` (permisos `600`, dueño `deploy`).

En producción `api` y `worker` **no se construyen en el servidor**. Corren la imagen que publicó la release en GHCR, fijada por digest y con su atestación de procedencia verificada (`infra/docker-compose.prod.yml`).

## Reglas

- Solo se despliega una **release** (tag `vX.Y.Z` creado por release-please), nunca una rama.
- Antes de cada despliegue hay un **backup**: `deploy.sh` lo hace solo si los backups están configurados y, si el backup falla, no despliega.
- Cada despliegue queda registrado en el run de GitHub Actions (quién, qué versión, qué imagen). Si se hace a mano, se anota en el issue de la release.

## Cómo funciona el despliegue automático

1. Al mergear el PR de release, `release.yml` publica la imagen en GHCR con SBOM y procedencia firmada.
2. Después llama a `deploy.yml`, que:
   - resuelve la imagen por digest y **verifica su atestación** (`gh attestation verify`), así que una imagen que no construyó este repositorio no se despliega;
   - entra por SSH como `deploy` y ejecuta `infra/scripts/deploy.sh vX.Y.Z <imagen@sha256:…>`;
   - comprueba `PRODUCTION_URL/v1/readyz` desde fuera.
3. En el servidor, `deploy.sh` hace checkout del tag, toma el backup previo, ejecuta `docker compose up --wait` con la imagen nueva y comprueba `/v1/readyz` a través de Caddy. **Si algo falla, vuelve solo a la versión anterior** (guardada en `.deploy-state`) y el workflow falla con el motivo.

| Código de `deploy.sh` | Significado | Qué hacer |
|---|---|---|
| 0 | desplegado | hacer las comprobaciones manuales de abajo |
| 2 | falló y volvió a la versión anterior; el servicio sigue en pie | revisar los logs del run, abrir un issue y corregir con otra release |
| 3 | falló y el rollback también | tratarlo como incidente: [`incidentes.md`](incidentes.md) y [`restauracion.md`](restauracion.md) |
| 1 | error de uso o de precondición; no tocó nada | corregir el `.env` o los argumentos |

Los dos scripts se prueban de verdad en CI (`.github/workflows/infra.yml`):

- `provision.sh` se ejecuta dos veces sobre una VM Ubuntu 24.04;
- `deploy.sh` despliega una versión buena, después una rota, y el CI exige que haga el rollback.

Para volver a una versión anterior a propósito: **Actions → Deploy → Run workflow** con el tag. Si el environment `production` tiene revisores, pedirá aprobación.

## Configuración en GitHub (una vez, la hace un admin)

En Settings → Environments, crear `production`, restringido a la rama `master`. Si se quiere aprobar a mano cada despliegue, añadirle revisores requeridos.

| Nombre | Tipo | Valor |
|---|---|---|
| `DEPLOY_HOST` | secreto | IP o nombre del VPS |
| `DEPLOY_SSH_KEY` | secreto | llave **privada** ed25519 dedicada solo a esto (su pública es la `DEPLOY_SSH_PUBKEY` del aprovisionamiento) |
| `DEPLOY_KNOWN_HOSTS` | secreto | salida de `ssh-keyscan -t ed25519 <host>`, **comparada a mano** con `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` en la consola del proveedor |
| `PRODUCTION_URL` | variable | `https://<dominio>` |

Mientras falten los secretos, `deploy.yml` termina en verde con un aviso de que no desplegó.

La llave la genera quien administra el servidor, en su propio equipo: `ssh-keygen -t ed25519 -f visitacampo-deploy -C github-deploy`. La privada solo se pega en el secreto; nunca se envía por chat ni por correo.

## Primer despliegue (servidor nuevo)

1. **Aprovisionar** el VPS (Ubuntu 24.04 o Debian 12) como root, desde la consola del proveedor o la primera sesión SSH:
   ```bash
   curl -fsSLO https://raw.githubusercontent.com/PMAPP-Org/Seguimiento_Proyectos/vX.Y.Z/infra/provision/provision.sh
   less provision.sh   # léelo antes de ejecutarlo como root
   DEPLOY_SSH_PUBKEY="ssh-ed25519 AAAA… github-deploy" bash provision.sh
   ```
   El script deja configurado:
   - el usuario `deploy`, que entra solo con llave y está en el grupo docker;
   - SSH sin contraseña y sin login de root;
   - firewall con 22, 80 y 443 abiertos;
   - fail2ban, NTP y actualizaciones de seguridad automáticas;
   - Docker con rotación de logs.

   **Antes de cerrar la sesión de root**, comprueba en otra terminal que `ssh deploy@<host>` entra con la llave. Para dar acceso a un operador, vuelve a ejecutar el script con su llave (las llaves se acumulan) o añádela a `~deploy/.ssh/authorized_keys`.
2. Apuntar el DNS del dominio a la IP del VPS.
3. Clonar el código, como `deploy`:
   ```bash
   git clone https://github.com/PMAPP-Org/Seguimiento_Proyectos.git /opt/visitacampo
   ```
   Si el repositorio es privado, clonar con una *deploy key* de solo lectura.
4. Dar acceso a la imagen. Si el paquete `visitacampo-api` de GHCR es privado, ejecutar `docker login ghcr.io` como `deploy` con un token *fine-grained* que solo tenga `read:packages`. Si el paquete es público, no hace falta.
5. Crear el `.env` de producción a partir de `infra/.env.example`, con secretos **nuevos** (nunca los de la plantilla):
   ```bash
   cd /opt/visitacampo
   cp infra/.env.example infra/.env && chmod 600 infra/.env
   # DJANGO_SECRET_KEY:     python3 -c 'import secrets; print(secrets.token_urlsafe(64))'
   # JWT (Ed25519, base64): openssl genpkey -algorithm ed25519 | base64 -w0
   # DJANGO_SETTINGS_MODULE=config.settings.prod   DJANGO_DEBUG=false
   # COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml:docker-compose.monitoring.yml
   bash infra/scripts/check_envvars.sh infra/.env infra/.env.example
   ```
   No debe quedar ningún `CAMBIAR_EN_PRODUCCION`: `grep CAMBIAR infra/.env` no debe devolver nada.
6. Arrancar. La forma normal es lanzar **Actions → Deploy** con el tag. A mano, se usa el digest que aparece en la release:
   ```bash
   bash infra/scripts/deploy.sh vX.Y.Z ghcr.io/<owner>/visitacampo-api@sha256:<digest>
   ```
   Si lo haces a mano, verifica antes la procedencia desde tu equipo: `gh attestation verify oci://ghcr.io/<owner>/visitacampo-api@sha256:<digest> --repo PMAPP-Org/Seguimiento_Proyectos`.
7. Crear la organización y el primer usuario: `cd infra && docker compose exec api python manage.py bootstrap_demo --org-name "<cliente>" --username "<usuario>"`. La contraseña generada se muestra una sola vez.
8. Crear el **usuario del back-office con MFA**. El Admin exige segundo factor, sin excepciones.
   ```bash
   docker compose exec api python manage.py createsuperuser
   docker compose exec api python manage.py enroll_admin_totp <usuario>
   ```
   El segundo comando muestra **una sola vez** la URI para la app de autenticación y 10 códigos de respaldo. Guárdalos en el gestor de contraseñas. Si se pierde el teléfono, vuelve a ejecutarlo con `--replace`.
9. Hacer las comprobaciones de la sección siguiente.

## Comprobaciones tras desplegar

- [ ] `docker compose ps`: todo `running`, con `api` en `healthy`.
- [ ] `curl -s -o /dev/null -w '%{http_code}' https://<dominio>/v1/readyz` devuelve `200`.
- [ ] `curl -sI https://<dominio>/v1/healthz` incluye una sola cabecera `strict-transport-security`.
- [ ] Un login de prueba desde la app funciona.
- [ ] `/admin/` responde 404 desde fuera de `CADDY_ADMIN_ALLOWED_IPS` y, por túnel SSH, pide usuario, contraseña **y código** de la app de autenticación.
- [ ] En Grafana todo está en verde y en Alertmanager no hay alertas críticas.

## Rollback

`deploy.sh` ya vuelve solo a la versión anterior si la nueva no arranca o no responde. Esto es para volver atrás después de un despliegue que salió bien, cuando el error aparece más tarde:

1. **Si la versión fallida no trae migraciones nuevas** (míralo en el CHANGELOG o con `git diff vANTERIOR vNUEVA -- server/visitacampo/*/migrations`): lanza **Actions → Deploy** con `vANTERIOR`, o a mano `bash infra/scripts/deploy.sh vANTERIOR <su imagen@sha256:…>`.
2. **Si trae migraciones nuevas**, el código anterior puede no funcionar sobre el esquema nuevo. Restaura el backup tomado justo antes del despliegue ([`restauracion.md`](restauracion.md)) y luego despliega `vANTERIOR`. Lo que cambió en el servidor después del backup se pierde, pero los teléfonos lo reenvían si aún no lo habían sincronizado.
3. Anota el rollback y abre un issue con la causa. La versión fallida no se vuelve a desplegar hasta que haya una corrección con su propia release.
