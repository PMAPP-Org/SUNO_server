# Arquitectura de SUNO (estado actual)

Esta carpeta describe **cómo está construido SUNO hoy**, según el código de `master` (v0.19.0, 2026-11-15) y, donde se indica, la pieza 6c de materiales aún sin integrar ([PR #84](https://github.com/PMAPP-Org/SUNO_server/pull/84)). Sigue el modelo [C4](https://c4model.com/): cada nivel acerca la vista.

La propuesta original de julio de 2026 está en [`../08-arquitectura-propuesta-suno.md`](../08-arquitectura-propuesta-suno.md). Es historia: donde ese documento y el código difieren, **manda el código**, y la diferencia queda anotada en estas páginas y en [`../adr/`](../adr/README.md).

| Documento | Qué responde |
|---|---|
| [`contexto.md`](contexto.md) | C4 nivel 1: quién usa SUNO y con qué sistemas externos habla. |
| [`contenedores.md`](contenedores.md) | C4 nivel 2: servicios Docker, base de datos, almacenamiento, monitoreo, app móvil y web. |
| [`componentes.md`](componentes.md) | C4 nivel 3: módulos de `server/suno/`, sus responsabilidades y dependencias, incluido el módulo de costos. |
| [`modelo-datos-costos.md`](modelo-datos-costos.md) | Modelo de datos del módulo de costos, a alto nivel. |
| [`flujos.md`](flujos.md) | Sincronización y flujo de datos entre la app y el servidor. |
| [`stack.md`](stack.md) | Lenguajes, frameworks y librerías clave, con versión y motivo. |

La arquitectura de la app (capas de Flutter) está en `docs/arquitectura/` de [SUNO_client](https://github.com/PMAPP-Org/SUNO_client). Las decisiones transversales y de backend están en [`../adr/`](../adr/README.md).

## Cómo se mantiene

Cada pieza que cambia la arquitectura actualiza estas páginas en su propio PR, junto con su ADR (regla en [`CONTRIBUTING.md`](../../CONTRIBUTING.md#documentación)). Los diagramas son Mermaid para que se revisen como texto en el diff.

## Diferencias entre la propuesta (doc 08) y el código

| Tema | Doc 08 | Código actual |
|---|---|---|
| Back-office | Solo Django Admin (ADR-0004). | Además existe la plataforma web `/plataforma/` con vistas Django propias (proyectos, presupuestos, matriz, control, costos, contratos, equipos, accesos). Ver [ADR-0020](../adr/0020-plataforma-web-con-vistas-django.md). |
| Overrides de Compose | `override.dev`, `override.selfhosted`, `override.managed`. | `docker-compose.yml` + `override.dev.yml`, `prod.yml` y `monitoring.yml`. No existen `selfhosted` ni `managed`. |
| Servicios de Compose | `caddy`, `api`, `worker`, `db`. | Los cuatro más `backup` (pg_dump + age + rclone). |
| Backups | `pg_dump` cifrado con age/gpg (§4.1 dice restic). | `pg_dump` + `age` + `rclone` (`infra/backup/`). Sin restic. |
| Errores de aplicación | Sentry (ADR-0010). | Sentry **no** está integrado (no aparece en `requirements*` ni en `settings`). |
| Mapa base offline | `flutter_map` + PMTiles (ADR-0012). | MBTiles ráster por organización ([ADR-0021](../adr/0021-mapas-base-mbtiles-raster.md)). |
| Despliegue | Al construir la imagen: `ssh` + `compose pull`. | Cada push a `master` se despliega por digest con procedencia verificada y rollback ([ADR-0017](../adr/0017-despliegue-continuo-de-master.md)). |
| Autorización | Roles de organización (`OWNER`…`VIEWER`). | Roles de organización **más** una matriz por sección con capacidades CRUD ([ADR-0022](../adr/0022-matriz-de-permisos-por-seccion.md)). |
| Rate limiting | `django-axes`. | `django-axes` para el login del Admin y la API, más buckets propios en BD (`core/ratelimit.py`, `RateLimitBucket`) para login, refresh y presign. |
