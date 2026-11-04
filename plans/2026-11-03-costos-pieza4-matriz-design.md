# Módulo de costos y presupuesto — Pieza 4: Matriz de homologación y ubicación (diseño)

Fecha: 2026-11-03 · Estado: aprobado en brainstorming, pendiente de revisión escrita.

## Contexto

Cuarta pieza del módulo de costos (ver la división en
`2026-10-30-costos-etapa1-cronograma-design.md`). Ya están el cronograma desde MS Project
y el avance desde campo (v0.11.0) y los presupuestos con versión contractual y vigente
(v0.12.0). Esta pieza relaciona los ítems del presupuesto con las tareas del cronograma a
través de la **ubicación**, y convierte las cantidades registradas en campo en avance de
las tareas. La pieza 5 (programado vs ejecutado y valor ganado) consume lo que deja esta.

Se entrega en dos etapas:

- **4a — servidor** (este documento): niveles y árbol de ubicación, ubicación de las
  tareas, matriz planeada con importación desde Excel, avance por cantidades, páginas web
  y API para la app.
- **4b — app móvil**: registro de cantidades por ítem dentro de la visita, con
  sincronización sin conexión. Tendrá su propio plan; aquí solo se fija el contrato de la
  API que usará.

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Llaves contextualizadoras | Son de **ubicación**, personalizables por proyecto y anidadas en niveles que van precisando el lugar (p. ej. Tramo → PR → Costado). Se modelan como un árbol de nodos. |
| Relación ítem ↔ tarea | La define el plan (matriz ítem × tarea → cantidad planeada); la ubicación la aporta la tarea. En campo el agente elige el ítem dentro de su tarea y puede precisar la ubicación hacia niveles más finos. |
| Cantidades planeadas | Se planean por ítem y tarea (es decir, por ubicación). Es opcional por ítem. |
| % de avance | En tareas con plan, el % sale de las cantidades ponderadas por costo y reemplaza al % manual. Las tareas sin plan siguen con el % manual. |
| Consistencia con el presupuesto | No se exige que la matriz sume la cantidad presupuestada: se muestra presupuestado / planeado / diferencia y se resaltan faltantes y excesos. |
| Carga | Plantilla Excel con vista previa y diferencias, más edición web puntual. La ubicación de las tareas se lee de campos personalizados de Project cuando existen, o se asigna en la plantilla. |
| Visibilidad en campo | El agente ve ítems sin dinero: código, descripción, unidad, cantidad planeada y ejecutada. Sin precios ni totales. |
| Registro en campo | Incremental (lo ejecutado en esa visita), dentro de la visita. Cuenta solo cuando la visita está validada. Las correcciones son registros nuevos (negativos), no ediciones. |
| Estructura de la ubicación | Árbol de nodos por proyecto (no etiquetas independientes ni texto libre). Etiquetas transversales (p. ej. "Fachada") quedan para después. |

## 1. Modelo de datos

Modelos nuevos en `core/models/location.py` y `core/models/quantity.py`, todos
`OrgScopedModel` (ADR-011) salvo `VisitQuantity`, que es sincronizable.

### Ubicación

- **LocationLevel**: `project` (FK, PROTECT), `name` ("Tramo"), `depth` (1, 2, 3…),
  `source_field` (opcional: el campo personalizado de Project que lo alimenta, p. ej.
  `Text1`; ver §2). Único por (`project`, `depth`) y por (`project`, `name` sin distinguir
  mayúsculas).
- **LocationNode**: `project`, `level`, `parent` (nodo del nivel anterior; nulo en el
  nivel 1), `name` ("Tramo 2", hasta 120 caracteres), `code` (opcional), `retired_at`.
  Único por (`project`, `parent`, `name` normalizado). Nunca se borra; se retira.
  Regla: el nivel del nodo es el siguiente al de su padre (nivel 1 sin padre). Cruza
  tablas, así que no es un CHECK: la aplica el único servicio que crea nodos, con su test.
- **Task.location**: FK nula a `LocationNode`, del mismo proyecto. Solo tiene sentido en
  tareas hoja; en resúmenes se ignora.

### Matriz planeada

- **QuantityPlan**: `project`, `task` (FK a `Task`, PROTECT), `item` (FK a
  `BudgetItem`, PROTECT), `planned_quantity` (`numeric(18,4)`, ≥ 0), `updated_by`,
  `updated_at`. Único por (`task`, `item`). Solo tareas hoja e ítems de tipo `item`
  (validado en servicio). Como `Task` y `BudgetItem` son estables, sobrevive a nuevas
  versiones del cronograma y del presupuesto.
- **QuantityPlanImport**: `project`, `number` (solo las aplicadas), `status`
  (`pending` / `applied` / `discarded`), `file_name`, `source_file` (el `.xlsx`),
  `base_schedule` y `base_budgets` (lo vigente al preparar la vista previa, para
  detectar vistas previas obsoletas), `summary` (JSON: diferencias, totales por ítem,
  advertencias), `uploaded_by`, `uploaded_at`, `applied_at`. CHECK: `number` no nulo si y
  solo si está aplicada (como en presupuestos).

### Ejecución en campo

- **VisitQuantity** (`SyncedModel`, hija de `Visit`): `visit`, `item` (`BudgetItem`),
  `location` (`LocationNode`, nulo si la tarea no tiene ubicación), `quantity`
  (`numeric(18,4)`, ≠ 0; negativa solo como ajuste), `note`. Id generado por el cliente
  (idempotente). No se edita después de sincronizar.
- Cuenta para el avance solo si la visita está **validada** (la revisión vigente es
  `approved`, ver `projects/field_progress.py`). Una visita rechazada no aporta sus
  cantidades.
- **Visit.progress_pct**: la regla "con tarea ⇒ % obligatorio" pasa a "con tarea ⇒ % o
  al menos una `VisitQuantity`". Se ajusta el CHECK `visit` correspondiente; la parte de
  cantidades se valida en el servicio de sincronización porque cruza tablas.

### Avance (calculado, no almacenado)

- **Tarea hoja con plan**: % = Σ (cantidad ejecutada validada × precio unitario
  vigente) / Σ (cantidad planeada × precio unitario vigente), sobre los ítems de su plan.
  Se limita a 100 % para la barra y los indicadores; la cantidad real, con el exceso, se
  muestra aparte. Si el valor planeado es 0 (ítems sin precio o plan en 0), se usa el
  promedio simple de ejecutado/planeado por ítem; si tampoco hay cantidades planeadas, la
  tarea se trata como sin plan.
- **Tarea hoja sin plan**: el % de la última visita validada, como hoy.
- **Tareas resumen**: el rollup actual de `projects/progress.py`, ponderado por valor
  planeado cuando todas sus hojas lo tienen; si no, el criterio actual.
- Precio unitario vigente: el de la versión vigente del presupuesto al que pertenece el
  ítem. Un ítem retirado del vigente usa su último precio conocido.
- Las cantidades registradas sobre ítems que no están en el plan de la tarea se muestran,
  pero no suman al % hasta que el ítem se agregue al plan.

## 2. Carga e importación

### Niveles y su fuente

- Propietario y Administrador definen los niveles del proyecto: nombre, orden y,
  opcionalmente, el campo de Project que alimenta cada uno.
- Para elegir el campo, la página lista los campos personalizados de tarea encontrados en
  el XML de la última versión aplicada (`ExtendedAttributes` con su alias, p. ej.
  "Text1 (Tramo)").
- Cambiar los niveles no reubica tareas por sí solo: aplica en la siguiente importación
  del cronograma o de la matriz. Borrar un nivel solo se permite si ningún nodo lo usa.

### Ubicación desde Project

- `projects/mspdi.py` lee también los valores de `ExtendedAttribute` de cada tarea (por
  `FieldID` y alias), con el mismo parser seguro (`defusedxml`).
- Al importar el cronograma, para cada tarea hoja se toman los campos configurados y se
  arma la ruta: Tramo = "2", PR = "12+300" → nodo *Tramo 2 / PR 12+300*. Los nodos que
  faltan se crean; los existentes se reutilizan comparando el nombre sin distinguir
  mayúsculas ni espacios repetidos.
- La vista previa de la importación agrega:
  - tareas cuya ubicación **cambia**;
  - rutas **incompletas** (nivel más profundo con valor y un nivel padre vacío): la
    ubicación queda en el último nivel válido, con advertencia;
  - campos configurados que no vienen en el XML: advertencia, y se conserva la ubicación
    anterior;
  - tareas **retiradas** que tienen plan o cantidades ejecutadas (previsto en la etapa 1).
- Si ningún nivel tiene campo de Project, la ubicación se asigna en la plantilla de la
  matriz o en la web.

### Plantilla Excel de la matriz

- Se descarga desde el proyecto. Una fila por par (tarea hoja × ítem planeado), más filas
  de las tareas hoja sin plan para completar.
- Columnas: EDT, UniqueID y Tarea (referencia, no editables); una columna por nivel de
  ubicación (editable solo en los niveles sin campo de Project); Presupuesto, Código de
  ítem, Descripción y Unidad (Descripción y Unidad de referencia); Cantidad planeada.
- Al subirla se valida:
  - la tarea existe (por UniqueID; la EDT es solo de referencia) y es hoja, no retirada;
  - el ítem existe en la versión vigente del presupuesto indicado y es de tipo `item`;
  - la cantidad es ≥ 0, con el parser de números colombiano de `budgets/totals.py`;
  - no hay pares repetidos;
  - los valores de ubicación forman una ruta coherente con el árbol (se crean nodos
    nuevos si hace falta, mostrados en la vista previa);
  - máximo 20.000 filas de datos.
- Los errores se informan por fila y columna ("fila 214: el ítem 3.2.1 no existe en el
  presupuesto vigente") y no se aplica nada mientras haya errores.
- **Vista previa**: pares nuevos, modificados y eliminados; nodos y ubicaciones nuevas;
  totales por ítem *presupuestado / planeado / diferencia*, resaltando ítems con exceso o
  sin asignar completo. Luego aplicar o descartar, con el mismo bloqueo
  (`select_for_update nowait`) y la detección de vista previa obsoleta (cronograma o
  presupuesto aplicados después de prepararla).
- La plantilla reemplaza la matriz completa del proyecto: un par ausente se elimina,
  salvo que ya tenga cantidades ejecutadas; en ese caso queda con planeado 0 y la vista
  previa lo advierte.

### Edición web

- Desde la tarea: agregar o quitar ítems y cambiar la cantidad planeada, fila por fila;
  asignar la ubicación de la tarea cuando su nivel no viene de Project. Todo auditado.

### Versiones del presupuesto

- Al aplicar una nueva versión vigente, la vista previa del presupuesto advierte de
  **ítems eliminados** que están en la matriz o tienen ejecución. Sus filas se conservan y
  se marcan "ítem retirado del presupuesto".

## 3. Páginas web

### Proyecto → pestaña "Matriz" (Propietario, Administrador, Supervisor, Observador)

- **Vista por ítems**: una fila por ítem con presupuestado / planeado / ejecutado
  (validado) / % ejecutado / sin asignar. Cada fila se expande a sus tareas con su
  ubicación y cantidades. Filtros por presupuesto, capítulo, ubicación (cualquier nivel)
  y estado (incompleto, con exceso, retirado).
- **Vista por ubicación**: el árbol de ubicación con valor planeado vs ejecutado ($) y %
  por nodo, consolidado en cada nivel.
- Acciones (Propietario y Administrador): niveles de ubicación, descargar plantilla,
  importar, historial de importaciones de la matriz.

### Detalle de tarea (existente)

- Bloque nuevo *Cantidades planeadas*: ítem, unidad, planeado, ejecutado, % e historial
  de registros validados (fecha, agente, ubicación precisada, cantidad, enlace a la
  visita). Montos visibles solo para quienes ven presupuestos.
- El % de la tarea indica su fuente: "por cantidades" o "% manual".

### Cronograma y Gantt

- El % SUNO de las tareas con plan sale de las cantidades. Nada más cambia.

### Revisión de visitas (existente)

- Las visitas con cantidades muestran las filas registradas, resaltando las que superan
  el plan del ítem en esa tarea y los ítems fuera del plan. La revisión es la de hoy.

### Permisos

- Definir niveles, editar la matriz e importar: Propietario y Administrador (como los
  presupuestos).
- Ver la matriz con montos: quienes ven presupuestos (Propietario, Administrador,
  Supervisor, Observador).
- Agente de campo: solo los ítems de sus tareas sin precios, por la API (§4).
- Políticas nuevas `can_*`/`check_*` en el registro existente, con su test.

## 4. API para la app móvil (contrato para 4b)

### Bajada

- El snapshot de tareas que ya baja la app (`ScheduleTaskSync` con su hash de versión)
  agrega por tarea:
  - `location`: ruta del nodo (lista de `{id, level, name}`) y los nodos descendientes
    disponibles para precisar;
  - `planned_items`: `item_id`, código, descripción, unidad, cantidad planeada y
    cantidad ejecutada validada. **Sin precios ni montos.**
- Cambios en la matriz, el árbol, la ubicación de la tarea o una validación de visita
  cambian el hash, y la app vuelve a bajar.

### Subida

- `VisitQuantity` viaja por la outbox como hija de `Visit`, igual que las fotos.
  Idempotente por id del cliente.
- Validación en el servidor:
  - la visita tiene tarea;
  - el ítem existe en algún presupuesto del proyecto; si no está en el plan de la
    tarea, la nota es obligatoria y queda resaltado en la revisión;
  - la ubicación es el nodo de la tarea o un descendiente;
  - cantidad ≠ 0; negativa solo con nota (ajuste).
- Si entre la captura sin conexión y la sincronización el ítem se retiró o el plan
  cambió, el registro se acepta y se resalta en la revisión; nunca se pierde.

### Compatibilidad

- Las versiones actuales de la app siguen enviando `progress_pct` y funcionan. En una
  tarea con plan ese % se guarda pero no determina el avance, y la revisión lo indica:
  "% manual ignorado: la tarea se mide por cantidades".

## 5. Errores, límites y pruebas

- **Excel**: las mismas protecciones del presupuesto (`_guard`, límites de zip, rechazo
  de macros, `read_only` + `data_only`), 20.000 filas máximo.
- **Concurrencia**: una importación pendiente de matriz por proyecto; bloqueo al aplicar;
  vista previa obsoleta si cambió el cronograma o un presupuesto.
- **Integridad**: CHECK `planned_quantity >= 0` y `quantity <> 0`; único (task, item);
  `Task.location` del mismo proyecto y nodo en el nivel siguiente al de su padre (en servicio).
- **Retiros**: tareas, ítems o nodos retirados con plan o ejecución se muestran y se
  advierten; nunca se borran.
- **Auditoría**: importaciones aplicadas, ediciones de la matriz y cambios de niveles,
  nodos y ubicación de tareas pasan por `audit.record` dentro de la transacción.
- **Pruebas**:
  - unitarias: avance por cantidades (ponderación por costo, exceso limitado a 100 %,
    plan sin valor, sin plan → % manual), consolidación por nodo, armado de rutas desde
    Project, diff de la matriz;
  - importación: ciclo completo, vista previa obsoleta, bloqueo, archivos mal formados y
    adversarios;
  - API: snapshot sin precios, cambio de hash, validación de cantidades, app antigua
    enviando solo %;
  - políticas: registro y Agente de campo sin montos;
  - revisión visual de las páginas nuevas a 1440 px y 390 px.

## Fuera de alcance (4a)

- App móvil (4b).
- Valor planeado en el tiempo, curva S y valor ganado (pieza 5).
- Etiquetas transversales fuera del árbol de ubicación.
- Distribuir automáticamente cantidades del presupuesto entre tareas.

## Extensiones previstas

- **Pieza 5**: usa `QuantityPlan` (valor planeado por tarea, repartido en las fechas de
  la tarea) y `VisitQuantity` validadas con su fecha (valor ganado en el tiempo).
- **Etiquetas transversales**: tabla aparte (nodo o tarea × etiqueta) sin tocar el árbol.

## Decisiones tomadas al planear

Precisiones del plan `2026-11-03-costos-pieza4-matriz-plan.md`; no cambian lo aprobado.

- Las cantidades de campo viajan dentro de la visita (`Visit.quantities`, JSONB) en vez de
  una tabla sincronizable aparte: el sync ya resuelve la visita, y así heredan la regla
  "una visita validada no cambia". Cuentan solo con la visita validada; antes se pueden
  corregir como cualquier dato de la visita.
- La ubicación sale toda de Project o toda de SUNO: si un nivel tiene campo de Project,
  todos deben tenerlo.
- Hasta 6 niveles. Los códigos de esquema de Project con tabla de valores no se leen.
- Ver la matriz y editarla usan las políticas de presupuestos (`can_view_budgets`,
  `can_manage_budgets`): son los mismos roles.
- Descargar la plantilla y ver el historial de cargas lo puede hacer cualquiera que vea
  presupuestos (`can_view_budgets`): muestran los mismos datos que la página de ítems.
  Solo importar, aplicar y editar piden `can_manage_budgets`.
- La plantilla identifica el ítem por (presupuesto, código); con un solo presupuesto la
  columna Presupuesto puede ir vacía. Una fila con ítem y sin cantidad elimina el par.
- El % por cantidades usa el precio de la última versión aplicada que contiene el ítem.
- Consolidación por valor: los hitos y las tareas de peso 0 sin plan no cuentan; si un
  hijo con plan tiene valor 0 (ítems sin precio), el grupo se pondera por días, para que
  su avance no desaparezca.
- Una versión anterior de la app (sin cantidades) que edita la tarea o el % de una visita
  aún no validada le borra las cantidades: su `update` lleva tarea y % sin `quantities`, y
  eso deja la lista vacía. Las visitas validadas están protegidas (`visit_validated`). Por
  eso la etapa 4b debe exigir la versión de la app actualizada antes de usar cantidades
  en campo.
- Capítulos: el tipo de un ítem es el de la última versión aplicada que lo contiene. Una
  cantidad sobre un capítulo se rechaza con `item_not_in_project` (los capítulos no se
  miden), **salvo** que el par (tarea, ítem) esté en la matriz: una reimportación puede
  volver capítulo un ítem planeado (el 2.1 se parte en 2.1.1 y 2.1.2) y la fila del plan
  sobrevive; el campo ya lo tenía ofrecido, así que lo registrado se acepta y se resalta,
  nunca se pierde. Esas filas del plan no miden: `plan_lines` las deja fuera, y con eso
  el % por cantidades, `measured_by` y `planned_items` de las tareas de campo (que ya no
  las ofrecen) y el cronograma coinciden. La ficha de la tarea las conserva con un aviso, y
  la vista previa del presupuesto avisa cuando un ítem con filas en la matriz o cantidades
  ejecutadas pasa a ser capítulo.
