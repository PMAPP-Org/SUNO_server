# Hallazgos de revisión de código — QField y QFieldCloud

**Fecha de análisis**: 2026-07-30  
**Auditorías realizadas**: 4 revisiones especializadas (cpp-reviewer sobre QField, django-reviewer sobre qfieldcloud, security-reviewer sobre QField, security-reviewer sobre qfieldcloud)

## Introducción

Este documento sintetiza hallazgos derivados de análisis estático de código de QField y QFieldCloud — proyectos usados como referencia arquitectónica para VisitaCampo. Los hallazgos se organizan por **severidad** (CRITICAL, HIGH, MEDIUM, LOW) y por **repositorio** (QField, qfieldcloud). Cada uno incluye la referencia exacta en el código, descripción del riesgo, y recomendaciones explícitas sobre **qué hacer distinto en VisitaCampo** para evitar los mismos patrones.

**Nota importante**: estos hallazgos son observaciones derivadas del análisis estático, no CVEs confirmados ni reportados a los mantenedores de los proyectos. El hallazgo CRITICAL sobre validación TLS en QField es especialmente relevante porque describe una vulnerabilidad real no documentada previamente.

---

## Hallazgos por severidad

### CRITICAL

#### QField: Bypass de validación TLS para servidores auto-hospedados

**Referencia**: `src/core/qfieldcloud/networkreply.cpp:102-110`, en `NetworkReply::initiateRequest()`

**Descripción del riesgo**:  
El código conecta una lambda al evento `sslErrors` que ignora **todos** los errores SSL (expirado, autofirmado, CA incorrecto, hostname mismatch, etc.) para cualquier host que *no* termine en `.qfield.cloud`:

```cpp
connect( mReply, &QNetworkReply::sslErrors, this, [this]( const QList<QSslError> &errors ) {
  if ( !mRequest.url().host().endsWith( QStringLiteral( ".qfield.cloud" ) ) )
    mReply->ignoreSslErrors( errors );  // ← Ignora TODOS los errores
} );
```

QFieldCloud está diseñado para ser auto-hospedable: usuarios pueden apuntar la aplicación a cualquier instancia propia via `QFieldCloudConnection::setUrl()`. Para esos hosts, **el bypass es automático e incondicional**. Esto permite un ataque MITM trivial en la red hacia la instancia auto-hospedada: un atacante en el path de red puede:
1. Interceptar conexiones TLS.
2. Cosechar credenciales (username/password) en `POST /api/v1/auth/token/`.
3. Inyectar respuestas JSON arbitrarias (datos editados, comandos sync falsos, etc.).

El código incluye un comentario `// TODO remove this!!! temporary SSL workaround`, señalando que los desarrolladores son conscientes de ser un parche temporal, pero persiste en la rama master.

**Por qué no es solo "limitación conocida"**:  
A diferencia del "master password fijo en Linux" (documentado en el código con un TODO visible), este bypass es silencioso — el usuario no recibe advertencia, no ve un diálogo de "certificado no confiable", simplemente la conexión "funciona". Un usuario que instala QField sin leer el código no sabe que su auto-hosted está inseguro.

**Qué hacer distinto en VisitaCampo**:
- **Nunca** llamar a `ignoreSslErrors()` de forma incondicional. Si VisitaCampo debe soportar servidores auto-hospedados, usar una de estas estrategias:
  1. **Certificate/public-key pinning**: almacenar el certificado o su public key en el cliente, validar en cada conexión.
  2. **User trust decision**: mostrar un diálogo explícito: "¿Confiar en este certificado?" con detalles (fingerprint, CN, issuer) y guardar la decisión solo si el usuario confirma consciente y explícitamente. Nunca hacer esto automático.
  3. **Separate secure channel**: usar un servidor intermediario de confianza (proxy/reverse proxy con certificado válido conocido) para acceder a instancias auto-hospedadas detrás de NAT/DNS privado.

---

### HIGH

#### QField: Dereference nulo antes de validación en applyDeltasOnLayers

**Referencia**: `src/core/qfieldcloud/deltafilewrapper.cpp:1356–1377`, método `DeltaFileWrapper::applyDeltasOnLayers()`

**Descripción del riesgo**:  
Se accede a `vectorLayers[layerId]->fields()` y `getLocalPkAttribute(vectorLayers[layerId])` **antes** de la validación en la línea 1374:

```cpp
auto fields = vectorLayers[layerId]->fields();  // ← Acceso sin validar
auto pkAttr = getLocalPkAttribute(vectorLayers[layerId]);  // ← Acceso sin validar
// ...
Q_ASSERT( vectorLayers.contains(layerId) );  // ← Validación DESPUÉS
```

`QHash::operator[]` en Qt inserta silenciosamente un valor nulo-inicializado si la clave no existe. Si un delta referencia un `layerId` desconocido (capa eliminada, id corrompido, sincronización de multi-dispositivo con capas desalineadas), la app **crashea** en producción, en lugar de fallar gracefully.

**Qué hacer distinto en VisitaCampo**:
- Usar `QHash::value(key, defaultValue)` o validar con `contains()` **antes** de acceder.
- Para datos serializados/sincronizados (como deltas), usar validación real con manejo de errores, nunca `Q_ASSERT` (que se optimiza en release).
- Si un delta es corrupto, registrar error, alertar al usuario, y permitir reintento/sincronización completa, no crash.

---

#### QField: Datos de sincronización validados solo con Q_ASSERT

**Referencia**: `src/core/qfieldcloud/deltafilewrapper.cpp`, métodos `mergeCreateDelta`, `mergeDeleteDelta`, `mergePatchDelta`, `applyDeltasOnLayers`, `attachmentFileNames`

**Descripción del riesgo**:  
Los deltas son **datos persistentes** que atraviesan sincronización entre dispositivos y servidor — pueden ser corruptos por:
- Interrupción en escritura (crash mid-write, disco lleno).
- Conflicto multi-dispositivo.
- Daño en almacenamiento.

Todo acceso estructural (índices, acceso a claves JSON, iteración) se valida con `Q_ASSERT`, que es compilado fuera en builds release. Resultado: un deltafile corrupto puede desencadenar índices negativos (`mDeltas.at(-1)`), dereference nulo, o undefined behavior silencioso en producción.

**Qué hacer distinto en VisitaCampo**:
- **Datos de sincronización**: validar con excepciones/error returns explícitos, nunca con `Q_ASSERT`.
- Implementar schema validation (JSONSchema, Protobuf, etc.) en la serialización.
- En caso de delta corrupto: log, telemetría, y oferta "re-sincronizar todo" al usuario.

---

#### QField: Race condition en acceso concurrente a deltafiles

**Referencia**: `src/core/qfieldcloud/deltafilewrapper.cpp:53–60`, constructor; código `#if 0` comentado en líneas 53–60

**Descripción del riesgo**:  
Existe un mecanismo deduplicado (`sFileLocks`) para evitar que dos instancias de `DeltaFileWrapper` abran el mismo archivo concurrentemente (read-only model vs. read/write layer observer). El check está envuelto en `#if 0`:

```cpp
#if 0
  if ( sFileLocks.contains( path ) )
    return;  // ← Prevención desactivada
#endif

sFileLocks.insert( path );  // ← Se poblam pero nunca se verifica
```

La protección está **desactivada pero el set se sigue actualizando** (líneas 164, 170), dando la falsa apariencia de seguridad. El TODO admite que tanto el modelo read-only como el layer observer read/write pueden mantener punteros separados al mismo archivo. Acceso concurrente a escritura produce corrupción de deltas.

**Qué hacer distinto en VisitaCampo**:
- Si hay read/write concurrente sobre el mismo recurso, usar un lock real (mutex, file lock del SO).
- No desactivar protecciones con `#if 0` — eliminarlas o reactivarlas, no dejarlas "latentes".
- Considerar un modelo de sincronización más simple: una sola cola de escritura, un solo lector (patrón productor-consumidor), si es viable en el dominio.

---

#### QField: Resource leak en falla de multi-part upload

**Referencia**: `src/core/qfieldcloud/qfieldcloudconnection.cpp:676–707`, método `QFieldCloudConnection::post()`

**Descripción del riesgo**:  
Se asigna un `QHttpMultiPart` pero solo se le pone parent en el reply **después** del loop de adjuntos:

```cpp
QHttpMultiPart *multiPart = new QHttpMultiPart(...);
for ( ... ) {
  if ( file.open(...) failed )
    return nullptr;  // ← Leak: multiPart nunca es liberado
}
multiPart->setParent(reply);  // ← Se llama solo si el loop completa
```

Si cualquier `QFile::open()` falla en el loop, la función retorna sin liberar `multiPart` ni las partes ya append'adas.

**Qué hacer distinto en VisitaCampo**:
- Poner parent **inmediatamente** después de `new`:
  ```cpp
  auto multiPart = std::make_unique<QHttpMultiPart>(...);
  ```
  o
  ```cpp
  QHttpMultiPart *multiPart = new QHttpMultiPart(...);
  multiPart->setParent(reply);  // Inmediatamente
  ```
- Usar smart pointers (`std::unique_ptr`, `QSharedPointer`) para recursos que pueden escapar.

---

#### QField: Path traversal en instalación de plugins

**Referencia**: `src/app/pluginmanager.cpp:415–432`, método `PluginManager::installFromUrl()`

**Descripción del riesgo**:  
El nombre del archivo se toma del header `Content-Disposition` sin sanitizar `../` o rutas absolutas:

```cpp
// De la respuesta HTTP, p.ej. "attachment; filename=../../../tmp/evil.zip"
fileName = extractFromContentDisposition(response);  // ← Sin validar

// Uso directo en path:
QString filePath = QStringLiteral("%1/plugins/%2").arg(dataDir, fileName);
QDir().mkpath(filePath);
// Posterior unzip también usa fileName sin sanitizar
```

El mismo `fileName` se usa como raíz de extracción del zip. Impacto: un servidor malicioso o interceptado (vía MITM — ver CRITICAL #1) puede escribir fuera de `dataDir/plugins/`. Como los plugins son código arbitrario QML/JS ejecutado en-app, esto es equivalente a RCE.

**Nota de alcance**: en QField, este endpoint no es accesible directamente vía deep-links (verificado en `qfieldurlhandler.cpp` — solo llama a `AppInterface::executeAction` con acciones limitadas), pero si VisitaCampo ofrece instalación de plugins, el riesgo es real.

**Qué hacer distinto en VisitaCampo**:
- Validar nombres de archivo: rechazar `/`, `\`, `..`, rutas absolutas.
- Canonicalizar la ruta final y verificar que está dentro del directorio esperado **antes** de escribir/descomprimir.
- Si soportas instalación de plugins:
  - Requiere verificación de firma (manifest/certificado de desarrollador).
  - Sandbox plugins si es posible (restricciones de permisos).
  - Mostrar diálogo explícito de "este plugin tendrá acceso a: cámara, ubicación, archivos, etc.".

---

#### qfieldcloud: N+1 de permisos dentro del loop de deltas

**Referencia**: `docker-app/qfieldcloud/core/views/deltas_views.py:116–118`, vista `ListCreateDeltasView.post`

**Descripción del riesgo**:  
Dentro del loop sobre deltas sincronizados (puede ser cientos, especialmente en VisitaCampo offline), se llama a `permissions_utils.can_create_delta(user, delta_obj)` que re-ejecuta la misma query de roles:

```python
for delta in deltas:
    if not permissions_utils.can_create_delta(user, delta_obj):  # N+1
        return error
```

Internamente, `can_create_delta()` llama a `user_has_project_roles()` que accede a la vista SQL `projects_with_roles_vw`. Con 100+ deltas, son 100+ queries idénticas a la misma vista.

**Qué hacer distinto en VisitaCampo**:
- Hoisting: ejecutar la query de roles **una sola vez** fuera del loop, cachear el resultado.
  ```python
  user_roles = user.get_roles_for_project(project_id)  # 1 query
  for delta in deltas:
      if not can_create_delta_with_roles(user_roles, delta):  # 0 queries
          return error
  ```
- En general, evitar lógica de permisos dentro de loops — pre-calcular lo que puedas fuera.

---

#### qfieldcloud: Lost-update race en versionado de archivos

**Referencia**: `docker-app/qfieldcloud/filestorage/models.py:222–261`, método `FileVersionQueryset.add_version`

**Descripción del riesgo**:  
Se obtiene/crea un `File` y se actualiza `latest_version` sin lock:

```python
file, created = File.objects.get_or_create(...)
# Sin select_for_update, otro proceso puede estar haciendo lo mismo ahora
file.latest_version = file_version
file.save(update_fields=['latest_version'])  # Lost update aquí
```

El código tiene un TODO admitiendo esto: "probably we need to select_for_update the file object". Si dos dispositivos suben el mismo archivo casi simultáneamente (patrón real en VisitaCampo offline — dispositivos reconectando), puede:
- Quedar `latest_version` apuntando a versión stale.
- Desencadenar violación de constraint `unique_file_per_project_name_type_packagejob` sin recuperación.

**Qué hacer distinto en VisitaCampo**:
- Usar `select_for_update()` en la fila `File`:
  ```python
  file = File.objects.select_for_update().get_or_create(...)[0]
  file.latest_version = file_version
  file.save()
  ```
- Considerar UPSERT atómico (INSERT ON CONFLICT) si la BD lo soporta.
- Test de carga concurrente: simular 2+ dispositivos subiendo al mismo tiempo.

---

### MEDIUM

#### QField: Downcast inseguro (static_cast vs qobject_cast)

**Referencia**: `src/core/qfieldcloud/deltafilewrapper.cpp`, constructor y `applyDeltasOnLayers`

**Descripción del riesgo**:  
Se usa `static_cast<QgsVectorLayer*>(project->mapLayer(layerId))` en lugar de `qobject_cast`. Si un delta apunta a una capa que no es vector (raster, etc.), el cast en C++ no valida el tipo — resultado: undefined behavior.

En el mismo repo, `layerobserver.cpp` usa `qobject_cast` (seguro) uniformemente. La inconsistencia es una "smell" de mantenibilidad.

**Qué hacer distinto en VisitaCampo**:
- Usar siempre `qobject_cast` o `dynamic_cast` para downcasts de objetos heredados en Qt.
- Si es nulo, manejar error explícitamente, no asumir.

---

#### QField: Format-string bug pierde cookies de sesión

**Referencia**: `src/core/qfieldcloud/qfieldcloudconnection.cpp:1203`, método `saveCookies()`

**Descripción del riesgo**:  
```cpp
settings.setValue( QStringLiteral("/QFieldCloud/cookies/%1"), ... )
// ← Nunca llama .arg(idx), luego %1 se toma literalmente
```

Cada cookie sobrescribe la misma clave. Al reiniciar, solo la última cookie persiste.

**Qué hacer distinto en VisitaCampo**:
- Revisar todas las uses de `QString::arg()` en path construction.
- Preferir `QStringBuilder` o construcción explícita de paths.
- Test: guardar 3 cookies, reiniciar, verificar que todas persisten.

---

#### QField: Cache global sin invalidación

**Referencia**: `src/core/qfieldcloud/deltafilewrapper.cpp`, método `attachmentFieldNames()`

**Descripción del riesgo**:  
Un `Q_GLOBAL_STATIC` cache keyed solo por `layerId`, nunca invalidado. Si dos proyectos comparten el mismo `layerId` (no es único a nivel de app), la cache retorna stale data.

**Qué hacer distinto en VisitaCampo**:
- Usar caches scoped: cache por `(projectId, layerId)`, no solo `layerId`.
- Proporcionar invalidación explícita cuando la estructura de proyecto cambia.
- Si tienes global state, documentar claramente cuándo es seguro (p.ej. después de load de proyecto, no durante sesión).

---

#### qfieldcloud: Falta de recuperación para jobs stuck QUEUED

**Referencia**: `docker-app/qfieldcloud/core/management/commands/dequeue.py:98–104`

**Descripción del riesgo**:  
Si el worker muere entre `save(status=QUEUED)` y ejecutar el job, ese job queda en `QUEUED` para siempre. El filtro `busy_projects_ids_qs` (líneas 75–80) luego bloquea **todo futuro trabajo** en ese proyecto indefinidamente.

No hay comando reaper/timeout encontrado en el repo.

**Qué hacer distinto en VisitaCampo**:
- Implementar watchdog periódico: jobs `QUEUED` más de X minutos → resetear a `PENDING`.
- Considerar enqueue timeout en la transacción inicial, p.ej. `QUEUED_AT` + `QUEUED_TIMEOUT`.
- Test: matar el worker deliberadamente mid-job, verificar auto-recuperación.

---

#### qfieldcloud: TOCTOU en invariante "un job activo por proyecto"

**Referencia**: `docker-app/qfieldcloud/core/management/commands/dequeue.py:75–92` y `docker-app/qfieldcloud/worker_wrapper/wrapper.py:243–263`

**Descripción del riesgo**:  
La garantía de "solo un job PENDING/QUEUED/STARTED por proyecto" es un filtro subquery (`Job.status != ...`), no un lock en `Project`. Dos workers que se inician casi simultáneamente (antes de que cualquiera commit) pueden pick diferentes jobs del mismo proyecto.

Mitigación conocida: `wrapper.py` tiene un check explícito "CONCURRENCY CHECK" que reset al perdedor a `PENDING` antes de ejecutar. Pero esto es bookkeeping en dos lugares (doble lógica de sincronización).

**Qué hacer distinto en VisitaCampo**:
- Usar un lock único: `SELECT ... FOR UPDATE` en `Project`, o advisory lock en PostgreSQL keyed by `project_id`.
- No depender de double-bookkeeping en dos archivos.

---

#### qfieldcloud: Tokens de autenticación almacenados en plaintext

**Referencia**: `docker-app/qfieldcloud/authentication/models.py:67–73`

**Descripción del riesgo**:  
`AuthToken.key` se guarda raw y con `unique=True, db_index=True`. Exposición de backup/replica no cifrada = credenciales directamente usables.

**Qué hacer distinto en VisitaCampo**:
- Almacenar hash de tokens (p.ej. con `django-cacheops` o `django-otp`).
- Comparar hash en lookup, nunca plaintext.
- Considera `Fernet` (django-fernet) para cifrado de campos sensibles, como ya hace QFieldCloud con `Secret.value`.

---

#### qfieldcloud: Race condition en invalidación de tokens single-session

**Referencia**: `docker-app/qfieldcloud/authentication/models.py:98–109`, método `AuthToken.save()`

**Descripción del riesgo**:  
Para tipos `single_token_clients`, se ejecuta un `filter().update()` sin lock. Logins simultáneos pueden dejar dos tokens vivos.

**Qué hacer distinto en VisitaCampo**:
- Envolver en `transaction.atomic()` con `select_for_update()` si el single-session invariant es importante.

---

#### qfieldcloud: Org-membership disclosure (no hay verificación de membresía)

**Referencia**: `docker-app/qfieldcloud/core/permissions_utils.py:668–675`, función `can_read_members`

**Descripción del riesgo**:  
Cualquier usuario autenticado puede listar todos los miembros y roles de **cualquier** organización, incluso sin ser miembro:

```python
def can_read_members(user, organization) -> bool:
    if not organization.is_organization:
        return False
    return True  # ← Sin verificar si user es miembro
```

El campo `OrganizationMember.is_public` existe (defaults a `False`) pero nunca se filtra en `get_queryset()`. Exploit: enumeración de admins, org structure de competidores, social engineering targeting.

**Qué hacer distinto en VisitaCampo**:
- Requerir que el usuario sea miembro de la org o tenga rol ADMIN:
  ```python
  def can_read_members(user, organization) -> bool:
      return user_has_organization_roles(user, org, [ADMIN, MEMBER, ...])
  ```
- Filtrar por `is_public=True` para usuarios no-miembros.

---

#### qfieldcloud: Job output/feedback expone secrets

**Referencia**: `docker-app/qfieldcloud/core/permissions_utils.py:500–511` vs `:531–538`, y `docker-app/qfieldcloud/worker_wrapper/wrapper.py:191–200, 316–318`

**Descripción del riesgo**:  
Roles REPORTER+ pueden leer `job.output` y `job.feedback["error_stack"]`, pero secrets (env vars `Secret`, `PGSERVICE_FILE_CONTENTS`) se inyectan en el contenedor. Si hay error (p.ej. DB connection failure), el traceback echado a stdout/stderr puede incluir el secret value.

Exploit: un REPORTER lee job output, obtiene credenciales DB, accede directo a PostgreSQL.

**Qué hacer distinto en VisitaCampo**:
- Scrubbing: antes de perseverar `job.output`/`feedback`, mascarar/redactar valores que coincidan con conocidos secrets.
- Gate job-output visibility: si el job usó secrets, solo ADMIN puede leer output.
- Test de errores: forzar fallos de conexión DB con un secret en el output, verificar que la salida perseverada no lo contiene.

---

#### qfieldcloud: SECURE_PROXY_SSL_HEADER no validado en proxy

**Referencia**: `docker-app/qfieldcloud/settings.py:68`

**Descripción del riesgo**:  
`SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` asume que el nginx/reverse proxy sobrescribe este header incondicionalmente. Si no lo hace, un cliente en HTTP plano puede spoofar `X-Forwarded-Proto: https`, afectando CSRF/cookie flags sin cifrado real.

**Qué hacer distinto en VisitaCampo**:
- Verificar que el reverse proxy (nginx, etc.) **sobrescribe** headers `X-Forwarded-*` incondicionalmente.
- Test: conectar en HTTP plano, intentar spoof `X-Forwarded-Proto`, verificar que Django lo rechaza o no lo confía.

---

### LOW

#### QField: Tipo-unsafe downcast — observación de consistencia

Ya cubierto en MEDIUM (ítem 1 es esencialmente el mismo).

---

#### qfieldcloud: Múltiples fetches redundantes de Project

**Referencia**: `docker-app/qfieldcloud/core/views/deltas_views.py:69, 199–201, 216`

**Descripción del riesgo**:  
`Project` se obtiene separadamente en la clase de permisos, en `post()`, y en `get_queryset()` — tres queries.

**Impacto**: bajo (overhead menor), pero anti-patrón de optimización.

**Qué hacer distinto en VisitaCampo**:
- Prefetch/select_related cuando sea posible.
- Cache en el contexto de la request (`request.project = ...` después del primer fetch).

---

#### qfieldcloud: Path traversal en almacenamiento de archivos — sólido, sin hallazgos

**Referencia**: `docker-app/qfieldcloud/filestorage/utils.py:25–52`, función `validate_filename`

**Validación verificada**: rechaza componentes `.`, `..`, y caracteres prohibidos.

**Recomendación para VisitaCampo**: mantener test unitario explícito de rechazo de traversal (`../`, `.`) como documentación de intent.

---

## Síntesis y priorización para VisitaCampo

| Hallazgo | Repo | Severidad | Acción inmediata |
|----------|------|-----------|-----------------|
| TLS bypass para self-hosted | QField | **CRITICAL** | **Prohibir incondicional** `ignoreSslErrors()` — pinning/explicit trust only |
| Dereference nulo en deltas | QField | HIGH | Validar antes de acceder, no después |
| Validación solo Q_ASSERT | QField | HIGH | Errores reales en datos de sync |
| Datos synced en plaintext en auth | qfieldcloud | MEDIUM | Hash de tokens, no plaintext |
| N+1 permisos en loop | qfieldcloud | HIGH | Hoist permission queries, cachear roles |
| Lost-update File.latest_version | qfieldcloud | HIGH | select_for_update() en upload concurrente |
| Org membership disclosure | qfieldcloud | MEDIUM | Requerir membresía para listar miembros |
| Job output expone secrets | qfieldcloud | MEDIUM | Scrubbing de output antes de persist |

---

## Patrones sólidos a emular

De ambos proyectos, los siguientes patrones han demostrado ser seguros y valen la pena replicar en VisitaCampo:

- **QField**: `src/core/qfieldcloud/layerobserver.*` — uso consistente de `qobject_cast`, `QPointer<>` para referencias débiles, reentrancy guard `isDeltaBeingApplied()`, disconnect-before-connect para señales.
- **QField**: `src/core/positioning/abstractgnssreceiver.h` y `positioning.h` — abstracción limpia con `std::unique_ptr`/`QSharedPointer` para recursos, `friend`-restricted virtual hooks, `Q_FLAGS` para bitmasks de capacidades.
- **qfieldcloud**: `dequeue.py` core state machine (`SELECT ... FOR UPDATE SKIP LOCKED` + `REPEATABLE READ`) — patrón de sincronización robusto para cola sin Redis.
- **qfieldcloud**: `permissions_utils.py` — estructura consistent `can_*`/`check_*` pairs, listas explícitas de roles, fácil de auditar.
- **qfieldcloud**: SQL-view migrations con `AlterSQL`/`ReverseAlterSQL` — migrations reversibles, no `RunSQL` crudo.

