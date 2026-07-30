# Infraestructura, despliegue y CI/CD

## 1. QFieldCloud — entornos vía `COMPOSE_FILE`

No se usan flags manuales de `docker compose -f`; se combina todo mediante la variable de entorno `COMPOSE_FILE` (`.env.example:459`), que encadena el `docker-compose.yml` base con overrides según entorno:

| Override | Uso |
|---|---|
| `docker-compose.override.local.yml` | Desarrollo local: hot-reload (monta código fuente), `debugpy` en `app` y `worker_wrapper`, servicio `uvcompile` para recompilar requirements con `uv`. |
| `docker-compose.override.standalone.yml` | Añade servicios de infraestructura que en producción real serían gestionados externamente: `db` (postgis), `rustfs` (S3), `createbuckets`, `webdav`, `smtp4dev`. Pensado para self-hosted todo-en-uno. |
| `docker-compose.override.test.yml` | BD `test_${POSTGRES_DB}` + `test_postgis_db` dedicada, red Docker nombrada explícitamente. |
| `docker-compose.override.prod.yml` / `.staging.yml` | Mínimos: solo ajustan volúmenes de `nginx` (estáticos sin passthrough) y build args; asumen que BD/storage son servicios externos gestionados (RDS, S3 real, etc.), no contenedores locales. |

Esto separa claramente **"modo todo en Docker" (desarrollo/self-hosted)** de **"producción con servicios gestionados externos"** — el mismo `docker-compose.yml` base sirve para ambos casos, solo cambia qué overrides se cargan.

## 2. Variables de entorno relevantes (`.env.example`, 622 líneas documentadas)

Agrupadas por dominio:
- **Identidad/seguridad**: `QFIELDCLOUD_HOST`, `SECRET_KEY`, `SALT_KEY` (cifrado de campos).
- **TLS**: certbot/mkcert según entorno.
- **Postgres**: host, puerto, credenciales, nombre de BD.
- **Storage**: `STORAGES` (JSON, multi-backend).
- **Gunicorn**: tuning de workers/threads.
- **Email/SMTP**.
- **Workers**: `QFIELDCLOUD_WORKER_REPLICAS` (escalado horizontal del `worker_wrapper`), `QFIELDCLOUD_AUTH_TOKEN_EXPIRATION_HOURS`.
- **Debug remoto**: `DEBUG_APP_DEBUGPY_PORT`, `DEBUG_QGIS_DEBUGPY_PORT` — incluso el contenedor QGIS efímero puede depurarse remotamente.
- `scripts/check_envvars.sh` (usado en CI) valida consistencia entre `.env.example` y lo que el código espera — evita que una variable quede huérfana o sin documentar.

## 3. CI/CD — QFieldCloud

- `.github/workflows/test.yml`: matriz por app Django (`authentication`, `notifs`, `subscription`, `core`, `project`, `filestorage`, `__flaky__` aislado para tests inestables). Pipeline: `pre-commit` → `docker compose up` (perfil test) → `makemigrations --check` → `migrate` → `collectstatic` → `manage.py test --keepdb`. Job separado `django_checks`: `manage.py check --deploy` + `validate_templates`. Notifica a Google Chat en fallos.
- Otros workflows: `codeql.yml` (seguridad estática), `build_and_push.yml` (imágenes Docker), `sync_translations.yml` (Transifex), `check_urls.yml`, `pyqt6_check.yml`.
- Lint: `pre-commit`, `ruff.toml`, `.flake8`, `.mypy.ini` (tipado estricto con `django-stubs`/`djangorestframework-stubs`).

## 4. CI/CD — QField (cliente)

- `.github/workflows/`: pipelines por plataforma (`android.yml`, `ios.yml`, `linux.yml`, `macos.yml`, `windows.yml`), más `codeql.yml`, `pre-commit.yml`, `documentation.yml` (mkdocs), `translations.yml` (Transifex), `dependabot.yaml`, `stale.yml`.
- **Android**: matriz por arquitectura (`arm64/arm-neon/x64/x86-android`) × variante de permisos de archivos — 8 builds por push, en `ubuntu-24.04`, con cancelación de builds obsoletos por PR (`concurrency`).
- **Fastlane** (`fastlane/Fastfile`): Windows sube el `.exe` a S3-compatible (Exoscale); iOS usa `match` para certificados compartidos cifrados, sube a S3 y a TestFlight (App Store Connect API key desde secretos CI).
- **Sentry** para crash reporting en producción (`WITH_SENTRY`, `SENTRY_DSN`/`SENTRY_ENV` como cache vars de CMake).

## 5. Contenedores efímeros — patrón docker-in-docker

`worker_wrapper` monta `/var/run/docker.sock` y usa el SDK Python de Docker para lanzar/detener contenedores `qgis3`/`qgis4` bajo demanda, uno por job. Esto:
- Aísla dependencias pesadas/inestables (QGIS headless, con su propio ciclo de vida y memoria) del proceso web principal.
- Permite versiones de QGIS en paralelo (3.44 LTR y 4.0) sin conflicto de dependencias.
- Requiere que el host donde corre `worker_wrapper` tenga acceso al socket Docker — implicación de seguridad a considerar (el contenedor que procesa jobs puede, en teoría, lanzar cualquier imagen Docker en el host).

## 6. Patrón a replicar en VisitaCampo

1. **Un solo `docker-compose.yml` base + overrides seleccionados por variable de entorno (`COMPOSE_FILE`)**, en vez de múltiples archivos compose completos e independientes por entorno — reduce duplicación y drift entre entornos.
2. **`.env.example` exhaustivo y documentado línea por línea**, con un script de validación en CI que compare contra las variables realmente usadas en el código — evita variables "fantasma" o sin documentar.
3. Si VisitaCampo no necesita QGIS headless, probablemente **no necesita el patrón docker-in-docker** — pero si hay procesamiento pesado análogo (generación de PDFs/reportes, procesamiento de imágenes de evidencias), el mismo patrón de "contenedor efímero lanzado por un worker" aplica igual de bien sin acoplarlo al proceso web.
4. **Separar explícitamente "modo self-hosted todo-en-Docker" de "modo producción con servicios gestionados"** desde el diseño del compose, incluso si al inicio VisitaCampo corre todo en Docker — facilita migrar a RDS/S3 gestionado más adelante sin reescribir el compose base.
