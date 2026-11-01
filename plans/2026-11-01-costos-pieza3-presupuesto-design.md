# Módulo de costos y presupuesto — Pieza 3: Presupuesto (diseño)

Fecha: 2026-11-01 · Estado: aprobado en brainstorming, pendiente de revisión escrita.

## Contexto

Tercera pieza del módulo de costos (ver la división en
`2026-10-30-costos-etapa1-cronograma-design.md`). Las piezas 1 y 2 (cronograma desde
MS Project y avance desde campo) están en v0.11.0. Esta pieza agrega los presupuestos del
proyecto. La siguiente (pieza 4) homologa ítems del presupuesto con tareas del cronograma
usando llaves contextualizadoras; la pieza 5 mide programado vs ejecutado y valor ganado.

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Detalle del precio | Precio unitario directo por ítem. El modelo queda listo para agregar APU (insumos) después; no entra en esta pieza. |
| Formato de carga | El Excel propio del usuario (`.xlsx`) con mapeo de columnas, guardado por presupuesto y reutilizado en las versiones siguientes. Sin plantilla obligatoria. |
| Versiones | Presupuesto contractual fijo + presupuesto vigente. Comparación ítem a ítem: mayores y menores cantidades, cambios de precio, ítems no previstos y eliminados. |
| Presupuestos por proyecto | Varios (p. ej. por contrato o fuente de financiación), cada uno con su contractual y su vigente; el proyecto suma. |
| Indirectos e impuestos | Lista configurable de cargos por versión (nombre, %, base). Plantilla inicial "AIU + IVA sobre utilidad". |
| Visibilidad | Propietario y Administrador gestionan; Supervisor y Observador ven; el Agente de campo no ve presupuestos. |
| Almacenamiento de versiones | Ítem estable (`BudgetItem`) + valores por versión (`BudgetItemVersion`). |

## 1. Modelo de datos

Modelos en `core/models/budget.py`, todos `OrgScopedModel` (ADR-011).

- **Budget**: `project` (FK a `Project`, PROTECT), `name` (único por proyecto, sin
  distinguir mayúsculas), `code`, `description`, `column_mapping` (JSON, ver §2),
  `contractual_version` (FK a `BudgetVersion`, nula hasta la primera aplicada),
  `created_by`, `created_at`.
- **BudgetVersion**: `budget`, `number` (consecutivo por presupuesto; solo las
  aplicadas), `status` (`pending` / `applied` / `discarded`), `label` (texto libre,
  p. ej. "Otrosí 2"), `file_name`, `source_file` (el `.xlsx` original, binario),
  `uploaded_by`, `uploaded_at`, `applied_at`, `summary` (JSON con el resumen de cambios y
  advertencias), `direct_cost` y `total` (calculados al aplicar, `numeric(18,2)`).
- **BudgetCharge**: `version`, `order`, `name`, `pct` (`numeric(7,4)`), `base`
  (`direct` = costo directo; `line:<order>` = el valor de otra línea anterior;
  `subtotal` = costo directo + líneas anteriores), `amount` (calculado al aplicar).
- **BudgetItem**: `budget`, `code` (único por presupuesto). Identidad estable de una fila
  (capítulo o ítem) entre versiones; nunca se borra. La homologación (pieza 4) se cuelga
  de aquí.
- **BudgetItemVersion**: `version`, `item`, `kind` (`chapter` / `item`), `sort_order`,
  `description`, `unit`, `quantity` (`numeric(18,4)`), `unit_price` (`numeric(18,2)`),
  `total` (cantidad × precio, redondeado a centavos; en capítulos, la suma de sus hijos),
  `parent` (FK al `BudgetItem` del capítulo que lo contiene, nula en el primer nivel).
  Único por (`version`, `item`).

**Reglas**

- La primera versión aplicada queda como contractual automáticamente. Propietario o
  Administrador pueden marcar otra versión aplicada como contractual (acción explícita,
  auditada).
- La versión vigente es la última aplicada.
- Un ítem que no viene en la vigente aparece como "eliminado" en la comparación (cantidad
  vigente 0). Si vuelve en una versión posterior, reaparece con la misma identidad.
- Total de la versión = costo directo (suma de ítems) + líneas de cargos aplicadas en
  orden. Total del proyecto = suma de los totales vigentes (y contractuales) de sus
  presupuestos.
- Redondeo: cada `total` de ítem a centavos con `ROUND_HALF_UP`; los cargos se calculan
  sobre esos totales y también se redondean a centavos.

## 2. Carga desde Excel

**Flujo** en `/plataforma/proyectos/<id>/presupuestos/<id>/importar/`:

1. Subir el `.xlsx`. Si el presupuesto ya tiene mapeo guardado, se aplica directo y se va
   a la vista previa.
2. **Mapeo** (la primera vez o si el usuario pide cambiarlo): se muestran las primeras 30
   filas de la hoja; el usuario elige la hoja, la fila donde empiezan los datos y qué
   columna es código, descripción, unidad, cantidad y valor unitario (total y capítulo
   opcionales). SUNO sugiere las columnas por los encabezados ("Ítem", "Descripción",
   "Und", "Cant.", "Vr. unitario"…) y el usuario confirma. El mapeo se guarda en
   `Budget.column_mapping`:
   `{"sheet": str, "first_row": int, "columns": {"code": "A", "description": "B", "unit": "C", "quantity": "D", "unit_price": "E", "total": "F" | null}}`.
3. **Vista previa**: ítems nuevos (no previstos), eliminados, con mayor o menor cantidad,
   con cambio de precio; total de la versión antes y después de cargos; advertencias
   (filas ignoradas, ítems cuyo total del Excel no coincide con cantidad × precio). El
   usuario escribe la etiqueta y ajusta los cargos.
4. **Confirmar**: se aplica en una sola transacción con bloqueo del presupuesto (como la
   importación del cronograma). Una vista previa queda obsoleta si se aplicó otra versión
   después de subirla; se rechaza y se pide volver a subir.

**Interpretación de filas**

| Fila | Se interpreta como |
|---|---|
| Con código, cantidad y valor unitario | Ítem |
| Con código y descripción, sin cantidad ni valor unitario | Capítulo; el nivel sale del código (1, 1.1, 1.1.1) y, si el código no tiene puntos, del orden de filas |
| Sin código, o con texto tipo "Subtotal" / "Total" | Se ignora y se cuenta en la vista previa |
| Código repetido en el archivo | Se rechaza la importación nombrando el código |
| Cantidad o valor unitario no numéricos o negativos | Se rechaza nombrando la fila |
| Celdas combinadas o con fórmulas | Se lee el valor calculado (`openpyxl`, `data_only=True`, `read_only=True`) |

El padre de un ítem es el capítulo más cercano que lo contiene (por prefijo del código o,
si los códigos no son jerárquicos, el último capítulo encima de él).

**Cargos**: la primera versión parte de la plantilla "AIU + IVA sobre utilidad" con
porcentajes en 0 (Administración, Imprevistos y Utilidad sobre el costo directo; IVA
sobre la línea de Utilidad). Las siguientes copian los cargos de la versión anterior. Se
editan en la vista previa.

**Límites**: solo `.xlsx` (se rechaza `.xlsm` y `.xls`), 20 MB, 20.000 filas leídas.

## 3. Páginas, permisos y auditoría

Pestaña **Presupuestos** en la cabecera del proyecto (junto a Cronograma, Gantt,
Historial), visible solo para quien puede ver presupuestos.

- **Lista de presupuestos**: nombre, total contractual, total vigente, diferencia (valor y
  %), versión vigente; abajo el total del proyecto. "Nuevo presupuesto" para Propietario y
  Administrador.
- **Comparación** (página principal del presupuesto): tabla por capítulos plegable con
  código, descripción, unidad; cantidad, valor unitario y total contractual; cantidad,
  valor unitario y total vigente; diferencia en valor y %. Cada fila marcada como *Mayor
  cantidad*, *Menor cantidad*, *Cambio de precio*, *No previsto* o *Eliminado*. Filtros
  por capítulo, "solo con cambios" y búsqueda. Al pie, las líneas de cargos y el total,
  contractual contra vigente. Exportar a Excel.
- **Versión**: el contenido de cualquier versión aplicada, con sus cargos, etiqueta y
  descarga del Excel original.
- **Importar**: subida, mapeo y vista previa (§2).
- **Historial**: versiones con etiqueta, fecha, quién subió, total y resumen; marca la
  contractual; acción "Marcar como contractual".

| Rol | Permisos |
|---|---|
| Propietario, Administrador | Todo: crear presupuestos, importar, editar cargos, marcar contractual, exportar. |
| Supervisor, Observador | Ver y exportar. |
| Agente de campo | No ve la pestaña ni accede a las URL (404). |

Auditoría: creación de presupuesto, versión aplicada y descartada, cambio de contractual,
exportación y descarga del Excel original.

## Pruebas

| Área | Cobertura |
|---|---|
| Lectura del Excel | Archivos generados en la prueba con openpyxl: capítulos con niveles, códigos no jerárquicos, subtotales a ignorar, fórmulas, celdas combinadas, códigos repetidos, valores inválidos, `.xlsm`, límites. |
| Mapeo | Sugerencia de columnas por encabezado; mapeo guardado y reutilizado. |
| Versiones | v1 contractual; v2 con mayores y menores cantidades, cambio de precio, ítem nuevo y eliminado; un eliminado que vuelve; cambio de contractual; vista previa obsoleta; importaciones simultáneas rechazadas. |
| Totales | Cargos sobre bases distintas y en orden, redondeo a centavos, suma de capítulos, total del proyecto. |
| Permisos | Cada página por rol y organización; el agente de campo recibe 404. |

## Fuera de alcance

- APU (desglose del precio en insumos).
- Avance o ejecución del presupuesto (pieza 5).
- Homologación con el cronograma y llaves contextualizadoras (pieza 4).
- Monedas distintas por presupuesto (todo en la moneda de la organización).

## Extensiones previstas

- **Homologación (pieza 4)**: filas (`BudgetItem`, `Task`, cantidad) y llaves
  contextualizadoras por proyecto. Como `BudgetItem` y `Task` son estables, la matriz
  sobrevive a nuevas versiones de ambos; la vista previa de importación avisará de ítems
  eliminados que estén homologados.
- **APU**: tablas de insumos y su relación con `BudgetItemVersion`, sin cambiar la
  identidad de los ítems.

## Decisiones tomadas al planear

Precisiones del plan `2026-11-01-costos-pieza3-presupuesto-plan.md`; no cambian lo aprobado.

1. **Mapeo entre subida y vista previa.** La versión pendiente se crea al subir el archivo;
   si el presupuesto no tiene mapeo (o se pide cambiarlo) se pasa por la página de mapeo.
   El mapeo se guarda en el presupuesto solo cuando produce una lectura válida, y la vista
   previa guarda el mapeo con que se leyó: al confirmar se vuelve a leer el archivo con él.
2. **Columna "capítulo".** No se implementa: el capítulo se reconoce por la fila (código y
   descripción sin cantidad ni valor unitario). Queda como extensión `columns.chapter`.
3. **Niveles.** Capítulo con puntos: su padre es el capítulo prefijo más largo. Capítulo sin
   puntos: primer nivel. Ítem: el capítulo prefijo o, si no hay, el último capítulo encima.
4. **Filas a medias.** Con código y solo cantidad o solo valor unitario: se rechaza nombrando
   la fila. Con código sin descripción ni números: se ignora y se cuenta.
5. **Números en texto.** Se aceptan con separadores de miles y decimales ("1.234,56",
   "1,234.56"); cantidad a 4 decimales y valor unitario a centavos (`ROUND_HALF_UP`) al leer.
6. **Límites.** Además de 20 MB y 20.000 filas: 60 columnas (A–BH), ZIP de máximo 1.000
   entradas y 200 MB descomprimidos, y rechazo de macros aunque el archivo se llame `.xlsx`.
   Datos más allá de la fila 20.000 rechazan la importación (no se trunca).
7. **Vista previa.** Compara contra la versión vigente y solo cuenta ítems; añade el grupo
   "vuelven" (ítems que existieron antes y regresan con la misma identidad).
8. **Cargos.** Máximo 20 líneas numeradas en orden; solo se editan en una versión pendiente.
9. **Exportación.** El Excel de la comparación es completo (sin filtros), con cargos y totales.
