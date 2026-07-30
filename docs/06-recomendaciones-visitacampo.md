# Recomendaciones y decisiones abiertas para VisitaCampo

Síntesis de los patrones extraídos de QField/QFieldCloud, traducidos a decisiones concretas para arrancar VisitaCampo. Este documento es el punto de partida de diseño, no una decisión cerrada — varias secciones requieren que el equipo defina alcance antes de fijar el stack.

## 1. Patrón central a adoptar: sync offline-first basado en deltas

Independientemente del stack elegido, el patrón más valioso observado es el de **QField ↔ QFieldCloud**:

1. Cada edición offline (crear/editar/eliminar una visita, formulario, evidencia) se registra localmente como un **delta** con `uuid` generado en el cliente (no autoincremental de servidor) — esto da idempotencia real ante reintentos de red.
2. Los deltas se acumulan en una cola local ("outbox") y se aplican de inmediato en la UI (optimistic UI), sin esperar confirmación del servidor.
3. Al recuperar conectividad, se suben en batch. El servidor valida, encola como job asíncrono, y aplica los cambios.
4. **La resolución de conflictos ocurre en el servidor**, no en el dispositivo: el cliente solo necesita saber "aplicado / conflicto / error" y, ante conflicto, descartar su copia local y re-sincronizar el estado autoritativo. Esto evita implementar merge de 3 vías en el móvil, que es la parte más costosa y propensa a bugs de cualquier sistema offline-first.
5. Adjuntos (fotos, firmas, documentos) se sincronizan **aparte** de los datos estructurados, referenciados por checksum, con su propia cola de reintento.

Ver el flujo completo en [03-integracion-sincronizacion.md](03-integracion-sincronizacion.md).

## 2. Cola de trabajos: Celery no es obligatorio

QFieldCloud demuestra que una cola sobre PostgreSQL (`SELECT ... FOR UPDATE SKIP LOCKED`) es suficiente para volumen moderado, sin infraestructura adicional (Redis/RabbitMQ). Si VisitaCampo no anticipa miles de jobs/segundo, esto simplifica el despliegue considerablemente frente a Celery+Redis. Ver [02-arquitectura-backend-qfieldcloud.md §4](02-arquitectura-backend-qfieldcloud.md#4-procesamiento-asíncrono--jobs-sin-celery).

## 3. Modelo de datos: patrones aplicables sin necesitar PostGIS

VisitaCampo no necesariamente requiere PostGIS completo (QFieldCloud lo usa porque maneja geometrías vectoriales complejas de QGIS). Si el alcance es solo "punto de ubicación de la visita" + formularios, un campo `POINT` simple (PostGIS ligero o incluso lat/lng planos) puede bastar. Patrones a conservar igual:
- Herencia o composición para tipos de usuario (individual/organización) si aplica.
- Modelo `Delta`/`Job` separado del modelo de negocio, con estados explícitos.
- Versionado de archivos con checksum (`FileVersion`) para adjuntos.
- Vistas SQL consolidadas solo si el modelo de permisos crece en complejidad (roles de organización + proyecto); si los permisos son simples, evitar esta complejidad extra al inicio.

Ver [04-base-de-datos.md](04-base-de-datos.md).

## 4. Infraestructura: empezar simple, dejar la puerta abierta

- Un `docker-compose.yml` base + overrides por entorno controlados por variable de entorno, no archivos compose paralelos completos.
- `.env.example` documentado desde el día uno.
- Postgres + storage de objetos (S3-compatible, o simplemente filesystem si el volumen inicial es bajo) desacoplados del proceso web vía capa de abstracción (equivalente a `django-storages`), para poder migrar a un proveedor gestionado sin tocar código de negocio.

Ver [05-infraestructura-despliegue.md](05-infraestructura-despliegue.md).

## 5. Seguridad/autenticación

- Tokens con expiración y detección de tipo de cliente (móvil vs. web vs. integración) permiten políticas distintas (p.ej. single-session solo para el cliente móvil).
- Nunca guardar tokens/credenciales en preferencias planas: usar keychain/keystore nativo en móvil.
- Protección contra fuerza bruta en login (equivalente a `django-axes`) desde el inicio, no como añadido posterior.
- **Nota importante**: ver [07-hallazgos-revision-codigo.md § Tokens de autenticación almacenados en plaintext](07-hallazgos-revision-codigo.md#qfieldcloud-tokens-de-autenticación-almacenados-en-plaintext) para detalles sobre almacenamiento seguro de tokens. Si soportas servidores auto-hospedados, revisar [07 § TLS bypass para self-hosted](07-hallazgos-revision-codigo.md#qfield-bypass-de-validación-tls-para-servidores-auto-hospedados) para evitar MITM.

## 6. Decisiones que este análisis NO puede resolver por ti

> **Actualización**: las cinco preguntas de esta sección ya fueron cerradas con una recomendación concreta y justificada (ADRs) en [08-arquitectura-propuesta-visitacampo.md](08-arquitectura-propuesta-visitacampo.md). Esta sección se conserva como registro histórico de las preguntas originales, no como guía vigente.

Estas dependen del alcance real de VisitaCampo y deben decidirse antes de fijar arquitectura:

1. **¿App móvil nativa/Qt-QML como QField, o multiplataforma más liviano (Flutter/React Native/Kotlin Multiplatform/PWA)?** QField usa Qt+QGIS porque necesita el motor SIG completo embebido; si VisitaCampo no requiere edición geoespacial compleja tipo SIG (solo captura de punto/formularios/fotos), un stack más liviano probablemente sea más rápido de desarrollar y mantener.
2. **¿Backend en Django (como QFieldCloud) u otro framework?** Django aporta el ecosistema (`allauth`, `axes`, ORM maduro, admin gratis) que QFieldCloud aprovecha extensamente; si el equipo ya tiene experiencia en otro stack (Node/NestJS, Laravel, etc.), los mismos patrones (cola sobre Postgres, deltas, storage abstraído) son portables.
3. **¿Es necesario procesar datos geoespaciales pesados (SIG) o basta con lat/lng + formularios?** Esto determina si hace falta un equivalente al "contenedor QGIS efímero" o si el backend puede procesar todo en el proceso principal.
4. **¿Modelo de organizaciones/equipos multi-tenant, o solo usuarios simples con roles fijos?** Afecta directamente si vale la pena el patrón de vistas SQL consolidadas de roles.
5. **¿Modo de despliegue objetivo?** (self-hosted en servidor propio vs. cloud gestionado tipo Render/Fly/AWS) — determina cuánto de la infraestructura de QFieldCloud (nginx, certbot, contenedores propios de BD/storage) conviene replicar vs. delegar a servicios gestionados desde el día uno.

Recomendación: antes de escribir código, hacer una sesión de brainstorming/definición de alcance (framework, plataformas objetivo, si hay componente SIG real) usando estas preguntas como guía, y solo después fijar el stack técnico concreto de VisitaCampo.
