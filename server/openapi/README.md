# Contrato de API v1 — fuente de verdad

`v1.yaml` es el **contrato OpenAPI 3.1 de VisitaCampo** y la fuente de verdad de la superficie
de acoplamiento entre el backend (Django 5 + Django Ninja + Pydantic v2, ADR-004) y el cliente
móvil (Flutter, ADR-001). Se escribió **antes** que el código (doc 08 §8 Fase 0) y es la parte de
definición de **RD-03** ("validación con OpenAPI 3.1 + JSON Schema, en ambos extremos").

- Superficie: exactamente la enumerada en
  [doc 08 §6](../../docs/08-arquitectura-propuesta-visitacampo.md#6-contrato-de-api-v1--superficie-mínima)
  — 13 operaciones, ni una más.
- Semántica de sync: [ADR-003](../../docs/08-arquitectura-propuesta-visitacampo.md#adr-003--protocolo-de-sync-outbox--cas-por-versión-aplicación-síncrona-y-server-authoritative).
- Entidades y campos: [doc 08 §4.4](../../docs/08-arquitectura-propuesta-visitacampo.md#44-modelo-de-datos-servidor).

## Verificación

```bash
python -m pip install openapi-spec-validator
python -m openapi_spec_validator server/openapi/v1.yaml
# server/openapi/v1.yaml: OK
```

Este comando forma parte del conjunto de verificación del repositorio y se integra en CI en el
Paso 4 (esqueleto del backend). El validador detecta la versión (3.1) a partir del campo
`openapi`; para forzarla explícitamente:

```bash
python -c "import yaml;from openapi_spec_validator import validate;from openapi_spec_validator.validation import OpenAPIV31SpecValidator;validate(yaml.safe_load(open('server/openapi/v1.yaml',encoding='utf-8')),cls=OpenAPIV31SpecValidator);print('OK 3.1')"
```

## Decisión: estrategia de generación de código

**Decisión — servidor**: los esquemas **Pydantic v2 se escriben a mano** en
`server/` (Django Ninja `Schema`), tomando `v1.yaml` como fuente de verdad, y un **test de
contrato** en CI compara el OpenAPI que Ninja **exporta** con `v1.yaml` y falla ante cualquier
deriva.

**Decisión — cliente**: los modelos Dart (`freezed` + `json_serializable`) se **escriben a mano**
en `app/`, y un **test de contrato en Dart puro** (Paso 12) valida fixtures de request/response
contra los JSON Schemas extraídos de `v1.yaml`, sin emulador ni red.

En ambos extremos: **contrato → código a mano → deriva detectada automáticamente**. No hay
generación de código en el build.

### Por qué

| Alternativa | Veredicto |
|---|---|
| **A mano en ambos extremos + tests de contrato** (elegida) | Los tipos generados nunca son los tipos que se quieren usar: en el servidor los `Schema` de Ninja se acoplan a modelos Django, validadores y `RequestContext`; en el cliente los modelos deben ser `freezed` inmutables, con columnas Drift y campos locales (`outbox_state`, `synced_at`) que **no existen en el contrato**. Con generación, el 100% del código generado se envolvería a mano igualmente. El coste real (escribir ~25 clases una vez) es menor que el de mantener dos pipelines de generación en Windows + CI. La deriva —el único riesgo real de escribir a mano— se cubre con tests, que además prueban la **semántica** (idempotencia, CAS, cursor), cosa que un generador no hace. |
| `datamodel-code-generator` → Pydantic v2 en el servidor | Genera modelos planos, sin los validadores de dominio ni las restricciones condicionales (`create` exige campos que `update` no). Habría que subclasear o editar lo generado ⇒ la generación deja de ser reproducible. Descartada. |
| Ninja como fuente de verdad (exportar OpenAPI desde el código) | Invierte la dirección de RD-03: el contrato dejaría de existir antes que el código y el cliente no podría desarrollarse en paralelo. **Explícitamente descartada**: doc 08 §8 Fase 0 exige el contrato escrito primero. El OpenAPI exportado por Ninja se usa solo como **entrada del test de deriva**, nunca como artefacto publicado. |
| `openapi-generator` (`dart-dio` / `dart2`) en el cliente | Arrastra `dio` y su propio stack de serialización, choca con `freezed`, genera miles de líneas versionadas y traduce mal las uniones discriminadas (`EntityRecord`, `SyncOp`). Requiere Java en el entorno de build. Descartada. |

**Disparador de reversión** (cuándo reabrir esta decisión): si el test de deriva falla más de
tres veces por olvido humano, o si el número de esquemas supera ~60, se adopta
`datamodel-code-generator` para los DTO puros (los que no tocan el ORM) y se mantiene a mano el
resto.

### Tests de contrato que materializan la decisión

| Test | Dónde | Paso | Qué falla si hay deriva |
|---|---|---|---|
| OpenAPI exportado por Ninja ≡ `v1.yaml` (comparación normalizada de `paths` + `components.schemas`) | `server/` (pytest) | 4–9 | Un campo añadido/renombrado/eliminado en un `Schema` de Ninja que no esté en el contrato. |
| Fixtures JSON válidos contra los JSON Schemas de `v1.yaml` | `server/` (pytest) y `app/` (test Dart puro) | 9 / 12 | Un modelo Dart o Pydantic que serializa algo que el contrato rechaza. |
| Round-trip Dart: `fromJson(toJson(x)) == x` sobre fixtures del contrato | `app/` | 12 | Pérdida de campos en la serialización del cliente. |
| Sintaxis OpenAPI 3.1 (`openapi-spec-validator`) | CI | 1 (aquí) | Cualquier edición que rompa el contrato. |

Los fixtures compartidos por ambos extremos viven en `server/openapi/fixtures/` (se crean en el
Paso 9) para que servidor y cliente validen **exactamente los mismos bytes**.

## Reglas de edición del contrato

1. **El contrato se edita primero.** Ningún PR de servidor o cliente puede cambiar la superficie
   HTTP sin cambiar `v1.yaml` en el mismo PR.
2. **El cuerpo de error se define una sola vez** (`components/schemas/Error`) y se reutiliza vía
   `components/responses/*`. Nunca se declara un cuerpo de error en línea.
3. **Cambios compatibles** (campo opcional nuevo, código de error nuevo, endpoint nuevo dentro de
   la superficie acordada): se incrementa `info.version` (minor) y, si afecta al payload de sync,
   `schema_version`, manteniendo `min_supported_schema_version` para que los dispositivos
   antiguos sigan sincronizando.
4. **Cambios incompatibles** (campo requerido nuevo, campo eliminado, semántica alterada): exigen
   subir `min_supported_schema_version` — y por tanto forzar actualización de app— o crear `/v2/`.
   Un dispositivo puede pasar semanas offline (S-08): **romper el contrato es perder trabajo de
   campo**, no un inconveniente de despliegue.
5. `id`, `version`, `change_seq`, `deleted_at` y `org_id` son **server-authoritative** salvo el
   `id` de las entidades creadas en el dispositivo. El cliente nunca los envía en un payload de
   push (ver `VisitPayload` / `VisitAttachmentPayload`).

## Límites no expresables en el esquema

OpenAPI/JSON Schema no puede expresar restricciones de tamaño total del cuerpo. Se documentan
aquí y se aplican en el servidor (Paso 8):

| Límite | Valor | Dónde se expresa | Dónde se aplica |
|---|---|---|---|
| Operaciones por lote de push | 500 | `PushRequest.ops.maxItems` (verificable) | Servidor, `413 payload_too_large` |
| Bytes por lote de push (descomprimido) | 5 MB | Solo documentado + `ServerCapabilities.sync_push_max_bytes` | Servidor, `413 payload_too_large` |
| Tamaño por adjunto | configurable | `ServerCapabilities.attachment_max_bytes` | Presign + verificación en commit |
| MIME de adjuntos | allow-list | `components/schemas/Mime` | Presign, commit y *sniffing* de magic bytes |

El cliente lee estos límites de `GET /v1/server/info` en lugar de codificarlos, para poder
ajustarlos sin publicar una release.

## Supuestos rellenados en este contrato

Detalles no fijados literalmente por doc 08 §6/§4.4 y resueltos aquí. Cada uno es un punto de
revisión si algún paso posterior descubre una necesidad distinta:

- **`DeviceInfo`**: doc 08 §6 solo dice "`device_info`". Se concretó como
  `{device_id, platform, os_version, model, app_version, display_name}`; `device_id` es
  obligatorio porque el refresh y la outbox son **por dispositivo** (ADR-008) y `visit.device_id`
  existe en §4.4.
- **`op_type` (`create`/`update`/`delete`) en la operación de push**: §4.4 nombra
  `op_uuid`, `base_version`, `entity`, `payload`. El tipo de operación se hace explícito en lugar
  de inferirlo de `base_version == null` y de la presencia de `deleted_at` en el payload.
- **Entidades sincronizables**: `visit`, `visit_attachment`, `assignment` y
  `form_template_version` (las plantillas viajan por el pull, ADR-012; las asignaciones originan
  visitas, §4.4). Solo `visit` y `visit_attachment` son **escribibles** por el cliente vía push.
- **Campos de `assignment` y `form_template_version`**: §4.4 no los tabula. Se definió el mínimo
  necesario para el MVP; ampliable de forma compatible.
- **Códigos de error genéricos**: a los seis códigos de contrato (`conflict`, `cursor_expired`,
  `payload_too_large`, `schema_unsupported`, `rate_limited`, `attachment_checksum_mismatch`) se
  añadieron `invalid_credentials`, `unauthenticated`, `forbidden`, `not_found`,
  `validation_error` e `internal_error`, imprescindibles para describir 400/401/403/404/422/500.
- **Estados de `job`**: `pending`, `running`, `succeeded`, `failed`, `dead_letter`
  (ADR-006 nombra `pending` y `dead_letter`).
- **`cursor` como cadena opaca** en lugar de exponer `change_seq` crudo: permite migrar al
  *escape hatch* de cursor por snapshot (§4.4) sin romper el contrato.
- **`attachment_checksum_mismatch` se sirve con `422`**, reservando `409` para el conflicto de CAS.
