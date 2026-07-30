# infra/ — Docker Compose de VisitaCampo

Este directorio contiene el entorno de contenedores del backend, siguiendo
**ADR-010** (`docs/08-arquitectura-propuesta-visitacampo.md`): cuatro
servicios fijos (`caddy`, `api`, `worker`, `db`) definidos en un
`docker-compose.yml` **base**, más overrides por entorno seleccionados con la
variable de entorno `COMPOSE_FILE` (patrón C-16, heredado de
[docs/05 §6.1–6.2](../docs/05-infraestructura-despliegue.md#6-patrón-a-replicar-en-visitacampo)).

No se usan flags `-f` manuales: se combina todo mediante `COMPOSE_FILE` en
`infra/.env`.

## Arranque rápido (desarrollo)

```bash
cd infra
cp .env.example .env
# Editar .env: como mínimo revisar POSTGRES_PASSWORD, DJANGO_SECRET_KEY,
# JWT_SIGNING_KEY, STORAGE_S3_ACCESS_KEY_ID/SECRET_ACCESS_KEY (pueden quedar
# como placeholders de desarrollo, pero deben existir).

bash scripts/check_envvars.sh   # falla si falta alguna variable en .env

docker compose config           # valida el YAML resultante (base + override.dev)
docker compose up db caddy      # arranca Postgres y Caddy (api/worker son
                                 # placeholders hasta los Pasos 4 y 19)
```

`docker compose` se ejecuta **desde `infra/`** para que recoja `infra/.env`
automáticamente (incluida la variable especial `COMPOSE_FILE`, que Docker
Compose lee de ese `.env` antes de resolver qué archivos combinar). Si
prefieres ejecutarlo desde la raíz del repo, usa
`docker compose --project-directory infra ...` o exporta `COMPOSE_FILE` tú
mismo en el shell.

## Selección de entorno vía `COMPOSE_FILE`

| Override | Archivo | Estado | Contenido |
|---|---|---|---|
| Desarrollo | `docker-compose.override.dev.yml` | **Creado en este paso** | Puertos publicados al host, hot-reload placeholder de `api`, MinIO (+ `createbuckets`) como emulador S3 local (anticipa ADR-007/Paso 17), MailHog para correo saliente simulado. |
| Self-hosted (todo en Docker, un único VPS) | `docker-compose.override.selfhosted.yml` | **Fuera de alcance de este paso** — se crea en un paso posterior de infraestructura de producción (ver Paso 24/25 del plan si aplica despliegue real). Incluirá `db` con volumen persistente en el propio VPS, tal como describe ADR-010. | — |
| Managed (Postgres gestionado externo) | `docker-compose.override.managed.yml` | **Fuera de alcance de este paso**, misma razón. Sustituirá el servicio `db` local por variables de conexión a un Postgres gestionado (ruta de evolución documentada en ADR-010). | — |

La variable se define en `infra/.env` (ver `.env.example`):

```
COMPOSE_FILE=docker-compose.yml:docker-compose.override.dev.yml
```

En Windows nativo (no Git Bash/WSL) el separador de `COMPOSE_FILE` es `;` en
vez de `:`; con Git Bash usa `:` como en Linux/macOS.

## `api` y `worker`: placeholders hasta que exista código

`server/` todavía no tiene código Django (se crea en el **Paso 4**; la cola
de trabajos del **Paso 19**). Por eso, en `docker-compose.yml` base, `api` y
`worker` son placeholders funcionales: imagen `python:3.12-slim` +
`sleep infinity`, para que la topología de servicios/red ya exista y
`docker compose up` no falle, sin escribir código de aplicación en este paso.

Cada servicio tiene, en `docker-compose.yml`, un comentario con las
instrucciones exactas de activación (sustituir `image:` por `build:` contra
`server/`, y `command:` por el proceso real). El override de desarrollo ya
deja preparado el montaje de código fuente (`../server:/app`) para hot-reload
en cuanto exista `manage.py`.

## Caddy y TLS (ADR-010, RD-15)

Un único `Caddyfile` sirve para desarrollo y producción; lo que cambia es el
valor de `CADDY_DOMAIN`:

- **Producción**: `CADDY_DOMAIN=<dominio real>` → Caddy emite y renueva
  automáticamente un certificado Let's Encrypt (ACME). Requiere que el
  dominio resuelva públicamente al VPS y que los puertos 80/443 estén
  abiertos.
- **Desarrollo local**: `CADDY_DOMAIN=localhost` (valor por defecto de
  `.env.example`) → al no ser un nombre DNS público, Caddy usa
  automáticamente su CA interna y sirve HTTPS con un certificado
  autofirmado (aviso de "no confiable" esperado en el navegador la primera
  vez). Alternativa sin TLS en absoluto para pruebas rápidas:
  `CADDY_DOMAIN=:80`.

**RD-15** (hallazgo de seguridad MEDIUM, `docs/07-hallazgos-revision-codigo.md`):
el `Caddyfile` sobrescribe **incondicionalmente** `X-Forwarded-Proto` (junto
con `X-Forwarded-Host`/`X-Forwarded-For`/`Host`) con el valor real de la
conexión que llega a Caddy, para que un cliente en HTTP plano no pueda
spoofear `X-Forwarded-Proto: https` y engañar a
`SECURE_PROXY_SSL_HEADER` de Django. Ver comentarios inline en
`infra/Caddyfile`. La verificación funcional de que Django efectivamente
confía solo en este proxy se hace en el **Paso 25**.

## Variables de entorno (`.env.example` / patrón C-16)

`infra/.env.example` documenta, línea por línea, todas las variables que usan
los servicios de este directorio (Postgres, Django/JWT, storage S3-compatible,
correo, Caddy). `infra/.env` (real, nunca versionado — ver `.gitignore`) debe
contener como mínimo las mismas claves.

`infra/scripts/check_envvars.sh` valida justamente eso: compara las claves de
`.env` contra `.env.example` y falla con un mensaje explícito si falta
alguna. Pensado para ejecutarse en CI (cuando se configure, Paso 4 en
adelante) además de localmente:

```bash
bash infra/scripts/check_envvars.sh                        # infra/.env vs infra/.env.example
bash infra/scripts/check_envvars.sh <env> <env.example>     # rutas explícitas (tests)
```

Solo valida que la **clave** exista (no el valor) — la validación de valores
concretos (secretos no triviales, `DEBUG=false` en producción, etc.) es
responsabilidad de `manage.py check --deploy` y otras validaciones
específicas de Django que se añaden a partir del Paso 4.

## Volúmenes y estado

Los archivos de compose son declarativos. El único estado persistente en
desarrollo es el volumen `pgdata` (Postgres) y, en el override de dev,
`minio_data`. Ambos son descartables:

```bash
docker compose down -v   # limpia también los volúmenes (¡destruye datos locales!)
```

## Comandos de verificación de este paso

| Qué verifica | Comando |
|---|---|
| YAML válido, interpolación de variables correcta | `docker compose -f infra/docker-compose.yml -f infra/docker-compose.override.dev.yml config` (ejecutar desde la raíz del repo, o `docker compose config` desde `infra/` con `.env` presente) |
| Postgres arranca y acepta conexiones | `docker compose up db` → `docker compose exec db pg_isready -U $POSTGRES_USER` |
| `.env.example` completo / detecta variables faltantes | `bash infra/scripts/check_envvars.sh` (ver sección anterior) |
