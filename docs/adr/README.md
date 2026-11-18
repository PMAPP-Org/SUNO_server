# Registro de decisiones de arquitectura (ADR)

Proceso y formato: [ADR-0000](0000-registro-de-decisiones.md). Los ADR 0001–0014 conservan la numeración de [doc 08](../08-arquitectura-propuesta-suno.md#5-architecture-decision-records); del 0015 en adelante son decisiones posteriores. Las decisiones propias de la app Flutter están en `docs/adr/` de SUNO_client.

| ADR | Título | Estado |
|---|---|---|
| [0000](0000-registro-de-decisiones.md) | Registro de decisiones de arquitectura | Aceptada |
| [0001](0001-cliente-movil-flutter.md) | Cliente móvil en Flutter, un solo codebase | Aceptada |
| [0002](0002-almacen-local-drift-sqlcipher.md) | Almacén local SQLite con Drift y SQLCipher | Aceptada |
| [0003](0003-protocolo-de-sync-outbox-cas.md) | Sync con outbox, CAS por versión y aplicación síncrona | Aceptada |
| [0004](0004-backend-django-ninja.md) | Backend en Python 3.12, Django 5 y Django Ninja | Aceptada |
| [0005](0005-postgresql-sin-postgis.md) | PostgreSQL 16 sin PostGIS | Aceptada |
| [0006](0006-cola-de-jobs-en-postgres.md) | Cola de trabajos en PostgreSQL con SKIP LOCKED y lease | Aceptada |
| [0007](0007-adjuntos-s3-por-contenido.md) | Adjuntos en S3, por contenido y con subida prefirmada | Aceptada |
| [0008](0008-autenticacion-jwt-y-refresh-hasheado.md) | Argon2id, JWT corto y refresh hasheado por dispositivo | Aceptada |
| [0009](0009-tls-estricto-y-pinning.md) | TLS estricto; pinning con consentimiento | Aceptada |
| [0010](0010-hosting-vps-docker-compose-caddy.md) | Un VPS con Docker Compose y Caddy; R2 | Aceptada (enmendada por 0014, 0016 y 0017) |
| [0011](0011-multi-tenant-org-id.md) | Multi-tenant con `org_id` desde el día 1 | Aceptada |
| [0012](0012-formularios-declarativos.md) | Formularios como datos declarativos versionados | Aceptada (punto PMTiles reemplazado por 0021) |
| [0013](0013-autorizacion-requestcontext-politicas-puras.md) | Políticas puras sobre un RequestContext | Aceptada |
| [0014](0014-monitoreo-prometheus-grafana.md) | Monitoreo con Prometheus, Alertmanager y Grafana | Aceptada |
| [0015](0015-release-please-y-squash-merge.md) | release-please, Conventional Commits y Squash and merge | Aceptada |
| [0016](0016-despliegue-al-publicar-release.md) | Desplegar al publicar una release | Reemplazada por 0017 |
| [0017](0017-despliegue-continuo-de-master.md) | Despliegue continuo de master por digest | Aceptada |
| [0018](0018-dos-repositorios-con-contrato-fijado.md) | Dos repositorios unidos por un contrato fijado | Aceptada |
| [0019](0019-dependencias-fijadas-por-hash-y-digest.md) | Dependencias por hash, imágenes por digest, Actions por SHA | Aceptada |
| [0020](0020-plataforma-web-con-vistas-django.md) | Plataforma web con vistas Django y formularios sin JavaScript | Aceptada |
| [0021](0021-mapas-base-mbtiles-raster.md) | Mapas base MBTiles ráster por organización | Aceptada |
| [0022](0022-matriz-de-permisos-por-seccion.md) | Matriz de permisos por sección, fija en código | Aceptada |
| [0023](0023-scoped-qs-unica-puerta-a-los-datos.md) | `scoped_qs` como única puerta a los datos | Aceptada |
| [0024](0024-renombre-reescribiendo-migraciones.md) | Renombre reescribiendo migraciones en el sitio | Reemplazada por 0025 |
| [0025](0025-solo-migraciones-aditivas-con-produccion.md) | Con producción, migraciones solo aditivas | Aceptada |
| [0026](0026-calculo-de-control-al-momento.md) | Control calculado al pedirlo, con consultas fijas | Aceptada |
| [0027](0027-modelo-de-valor-ganado.md) | Modelo de valor ganado | Aceptada |
| [0028](0028-presupuesto-item-estable-y-versiones.md) | Presupuesto con ítem estable, contractual y vigente | Aceptada |
| [0029](0029-cantidades-dentro-del-registro.md) | Cantidades dentro del registro | Aceptada |
| [0030](0030-libro-de-costos-aprobado-y-reverso.md) | Libro de costos: solo lo aprobado; se reversa | Aceptada |
| [0031](0031-origen-unico-del-movimiento-del-libro.md) | Un solo documento de origen por movimiento | Aceptada |
| [0032](0032-aprobado-es-lo-firmado.md) | Lo aprobado es lo firmado | Aceptada |
| [0033](0033-promedio-ponderado-movil-por-bodega.md) | Promedio ponderado móvil por bodega y material | Aceptada |
| [0034](0034-kardex-movimientos-y-saldos.md) | Kárdex de movimientos + saldos con checks en BD | Aceptada |
| [0035](0035-orden-de-bloqueos.md) | Orden de bloqueos | Aceptada |
| [0036](0036-importaciones-en-dos-pasos.md) | Importaciones en dos pasos con vista previa | Aceptada |
| [0037](0037-tareas-de-campo-en-endpoint-propio.md) | Tareas de campo por endpoint propio | Aceptada |
| [0038](0038-registro-validado-inmutable-desde-el-push.md) | Un registro validado no cambia por el push | Aceptada |
