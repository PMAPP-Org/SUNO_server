# Costos, pieza 4b: cantidades ejecutadas en la app

Fecha: 2026-11-09. Repos: `SUNO_server` (PR pequeña) y `SUNO_client` (PR principal).
Antecedente: pieza 4a (`plans/2026-11-03-costos-pieza4-matriz-design.md`), que ya dejó en el
servidor la matriz, las cantidades del registro y la lista de tareas de campo con ítems
planeados.

## Contexto

Desde la 4a, un registro acepta `quantities` (ítem, ubicación, cantidad, nota) y la lista de
tareas de campo (`/v1/projects/tasks`) trae por tarea `measured_by`, `planned_items` y
`location_options`. El servidor rechaza `item_not_in_project` y `location_outside_task`; lo
demás lo acepta y lo resalta en la revisión. La app todavía no usa nada de esto: en las tareas
medidas por cantidades, el agente sigue reportando un %.

La 4b lleva la captura de cantidades a la app. El renombre `Visit` → `Record` ya está en
producción (servidor 0.15.0, app 0.12.0, esquema 2), así que todo se construye con los
nombres nuevos.

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Captura | En una tarea con plan, el % se reemplaza por la lista de ítems planeados con "Cantidad hoy" (incremental). Las tareas sin plan conservan el %. |
| Ítems no planeados | "Agregar ítem no planeado" busca en el catálogo de ítems medibles del **presupuesto vigente** del proyecto. Exige nota. |
| Negativos | Una cantidad negativa es un ajuste y exige nota. |
| Varias filas | Se permiten varias filas del mismo ítem (p. ej. dos ubicaciones). |
| Ubicación | Por defecto la de la tarea; se puede precisar con `location_options`. |
| Sobreejecución | Validado + hoy > planeado muestra aviso; no bloquea. |
| Fecha | La del registro. |
| Datos automáticos por fila | `client_id`, `captured_at`, `gps {lat, lon, accuracy_m}` (si hay permiso y señal; sin seguimiento continuo), `seen {planned, validated, location_id}` al capturar, `location_mode task|refined`. No se muestran al agente. |
| Rechazos | `item_not_in_project` y `location_outside_task` llevan el registro a "Para revisar", señalando la fila; se reasigna el ítem o la ubicación, o se borra la fila. |
| Contrato | Los campos nuevos son opcionales: sin cambio de `SCHEMA_VERSION`, sin forzar actualización. |
| Exportaciones | PDF del registro con tabla de cantidades; GeoJSON con las cantidades como propiedad. |

## 1. Servidor

### 1.1 Campos automáticos por fila

`RecordQuantityPayload` (push) acepta, todos opcionales:

| Campo | Tipo | Regla |
|---|---|---|
| `client_id` | UUID | Único dentro del registro; si se repite, `validation_error` con `reason: duplicate_client_id`. |
| `captured_at` | fecha-hora ISO 8601 con zona | Momento en que se digitó la cantidad. |
| `gps` | objeto o `null` | `lat` (−90..90), `lon` (−180..180), `accuracy_m` (≥ 0). |
| `seen` | objeto o `null` | `planned` y `validated` (números o `null`), `location_id` (UUID o `null`): lo que la app mostraba al capturar. |
| `location_mode` | `task` \| `refined` | `refined` si el agente precisó la ubicación. |

Se guardan tal cual dentro del JSON `quantities` del registro y vuelven en `RecordQuantity` (pull)
con los mismos nombres, opcionales. Una fila sin ellos (app 0.12.0, o captura anterior) sigue
siendo válida. Un registro validado no cambia sus cantidades: la comparación de
`validated_rejection` incluye los campos nuevos.

La revisión (`RecordAdmin.quantities_pretty`) muestra la hora de captura y el GPS por fila, y
resalta, solo como aviso: hora de captura en un día distinto a la fecha del registro, y
precisión GPS peor que 100 m.

### 1.2 Catálogo de ítems por proyecto

`FieldTaskList` agrega `project_items`: por proyecto con tareas en la lista, los ítems
medibles (no capítulos) del presupuesto **vigente**:

```yaml
project_items:
  - project_id: <uuid>
    items:
      - {item_id, code, description, unit}
```

Sin precios ni montos. Entra en la huella `version` de la lista, así que `changed: false` sigue
evitando bajarlo de nuevo. Si el proyecto no tiene presupuesto vigente, `items` va vacía.

### 1.3 Pruebas y contrato

- `openapi/v1.yaml` y fixtures actualizados; Schemathesis y paridad.
- Pruebas de payloads (campos válidos e inválidos, `client_id` repetido), de guardado y pull
  ida y vuelta, de `validated_rejection` con los campos nuevos, del catálogo (vigente, sin
  capítulos, sin precios, huella) y de la vista de revisión.

## 2. App

### 2.1 Base local (Drift 1 → 2)

- `ScheduleTasks` agrega `measuredBy` (`pct` | `quantities`).
- Tablas nuevas: `TaskPlannedItems` (tarea, ítem, código, descripción, unidad, planeado,
  validado), `TaskLocationOptions` (tarea, ubicación, etiqueta), `ProjectItems` (proyecto, ítem,
  código, descripción, unidad).
- `Records` agrega `quantities` (JSON, lista vacía por defecto), con la misma forma del
  contrato.
- Migración con prueba de esquema (`drift_dev schema steps` / verificador).

### 2.2 Captura

En `TaskProgressSection`, si la tarea elegida tiene `measuredBy == quantities`:

- Una fila por ítem planeado: código, descripción, unidad, planeado, validado, y el campo
  "Cantidad hoy" vacío. Solo se guardan las filas con cantidad distinta de 0.
- Por fila: "Ubicación" (la de la tarea o una de `location_options`) y "Nota".
- "Agregar ítem no planeado": buscador por código o descripción sobre `ProjectItems` del
  proyecto de la tarea; la fila agregada exige nota.
- "Agregar otra fila" de un ítem ya listado (otra ubicación).
- Validaciones al guardar: nota obligatoria si el ítem no está planeado o la cantidad es
  negativa; al menos una cantidad distinta de 0; hasta 4 decimales; |cantidad| < 10^14; nota
  ≤ 500; ≤ 200 filas.
- Avisos que no bloquean: validado + hoy > planeado; cantidad negativa marcada como ajuste.
- Los datos automáticos se toman al digitar la cantidad por primera vez en la fila
  (`captured_at`, `gps` con una lectura puntual si hay permiso, `seen`) y al elegir la
  ubicación (`location_mode`). `client_id` al crear la fila.
- En un registro validado las cantidades son de solo lectura.
- Si el agente cambia a otra tarea y ya hay filas capturadas, la app pide confirmar antes de
  descartarlas.

### 2.3 Sincronización

- Push: `quantities` con `progress_pct: null` en tareas por cantidades; las tareas por % siguen
  igual (`quantities: []`).
- Pull: guarda `quantities` en el registro y `project_items`, `planned_items`,
  `location_options` y `measured_by` desde la lista de tareas.

### 2.4 Rechazos y corrección

`item_not_in_project` y `location_outside_task` pasan el registro a "Para revisar" con un
mensaje en español que nombra la fila (código del ítem). La corrección abre la captura con la
fila señalada: reasignar ítem o ubicación, o borrar la fila; al guardar se reenvía.

### 2.5 Detalle y exportaciones

- Detalle del registro: tabla de cantidades (ítem, ubicación, cantidad con unidad, nota).
- PDF: la misma tabla. GeoJSON: propiedad `quantities` con `item_code`, `item_id`,
  `location_id`, `quantity`, `unit`, `note`.

### 2.6 Pruebas y verificación

- Unitarias de la lógica de captura (notas obligatorias, sobreejecución, distinto de 0,
  decimales), de la migración Drift, del mapeo de sync y de la paridad del contrato.
- Widgets: captura por cantidades, ítem no planeado, solo lectura, corrección de rechazo.
- Punta a punta en emulador contra el servidor: capturar cantidades, sincronizar, validar y ver
  lo validado actualizado en la tarea.
- Revisión visual a 390 px: textos cortados, tablas anchas, concordancia.

## 3. Entregas

1. **Servidor**: campos automáticos, catálogo, contrato, revisión. Se despliega solo al
   mezclar; es aditivo y compatible con la app 0.12.0.
2. **App**: copia el contrato del merge del servidor y construye lo de la sección 2.

Release normal de ambos (release-please), sin forzar actualización.

## Fuera de alcance

- Seguimiento GPS continuo.
- Fotos por fila de cantidad.
- Control y valor ganado (pieza 5).
- Captura de cantidades desde la web.
