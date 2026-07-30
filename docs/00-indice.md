# Documentación de arquitectura de referencia — QField / QFieldCloud

Este directorio documenta la arquitectura, el funcionamiento interno y el modelo de integración de **QField** (cliente móvil/desktop) y **QFieldCloud** (backend Django), los dos proyectos analizados como referencia antes de iniciar **VisitaCampo**.

El objetivo no es documentar QField/QFieldCloud por sí mismos, sino extraer **patrones de diseño probados en producción** para una app de seguimiento/visitas de campo con backend propio: sincronización offline-first, manejo de adjuntos, colas de trabajo, modelo de datos geoespacial/no geoespacial, permisos, despliegue con Docker.

## Contenido

1. [01-arquitectura-cliente-qfield.md](01-arquitectura-cliente-qfield.md) — Stack, capas C++/QML, GPS/sensores, modo offline, plugins, testing, CI/CD del cliente QField.
2. [02-arquitectura-backend-qfieldcloud.md](02-arquitectura-backend-qfieldcloud.md) — Stack Django, apps, cola de trabajos sin Celery, procesamiento QGIS headless, API REST, seguridad.
3. [03-integracion-sincronizacion.md](03-integracion-sincronizacion.md) — Protocolo de sincronización delta, autenticación, adjuntos, resolución de conflictos, diagramas de flujo end-to-end.
4. [04-base-de-datos.md](04-base-de-datos.md) — Modelo de datos PostgreSQL/PostGIS, entidades, relaciones, migraciones, almacenamiento de archivos.
5. [05-infraestructura-despliegue.md](05-infraestructura-despliegue.md) — Servicios Docker Compose, entornos (local/standalone/test/prod/staging), variables de entorno, CI/CD.
6. [06-recomendaciones-visitacampo.md](06-recomendaciones-visitacampo.md) — Síntesis de patrones a adoptar, decisiones de stack sugeridas y preguntas abiertas para arrancar VisitaCampo.
7. [07-hallazgos-revision-codigo.md](07-hallazgos-revision-codigo.md) — Hallazgos de auditorías de código (calidad y seguridad) de QField y QFieldCloud, organizados por severidad, con recomendaciones específicas para VisitaCampo.
8. [08-arquitectura-propuesta-visitacampo.md](08-arquitectura-propuesta-visitacampo.md) — Arquitectura propuesta y cerrada para VisitaCampo (no solo referencia): stack móvil y backend, ADRs, modelo de datos, plan por fases y costo estimado, diseñada evitando explícitamente los hallazgos del documento 07.

## Resumen de una línea de cada proyecto

- **QField** (`../../QField`): app cliente en C++/Qt6/QML, construida sobre el motor de QGIS embebido, para levantamiento y edición de datos geoespaciales en campo, con soporte offline-first y sincronización por deltas hacia QFieldCloud.
- **QFieldCloud** (`../../qfieldcloud`): backend Django + PostgreSQL/PostGIS + Docker, que recibe los deltas de QField, los aplica sobre proyectos QGIS usando contenedores QGIS headless efímeros, gestiona usuarios/organizaciones/permisos, y almacena archivos en S3/WebDAV.

## Cómo se generó esta documentación

Generada a partir de análisis estático de ambos repositorios (sin ejecutar código), leyendo código fuente representativo (modelos, vistas, clases núcleo de sincronización, `docker-compose.yml`, CI). 

Los documentos 01-06 provienen de lectura manual. El documento 07 (hallazgos de revisión de código) proviene de cuatro auditorías especializadas realizadas por agentes revisores:
- **cpp-reviewer**: revisión de calidad de código de QField (C++/Qt/QML).
- **django-reviewer**: revisión de calidad de código de qfieldcloud (Django, Python).
- **security-reviewer (QField)**: revisión de seguridad del cliente.
- **security-reviewer (qfieldcloud)**: revisión de seguridad del backend.

Las referencias `archivo:línea` corresponden al estado de los repos en la fecha de este análisis (2026-07-30) y pueden desactualizarse si los proyectos cambian — verificar contra el código actual antes de asumir que un detalle puntual sigue vigente.
