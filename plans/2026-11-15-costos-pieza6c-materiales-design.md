# Costos, pieza 6c: materiales (compras, bodegas y consumos)

Fecha: 2026-11-15. Repo: `SUNO_server` (una PR). La app no cambia en esta pieza: la captura móvil de
salidas sin conexión será una pieza posterior.

Antecedentes de esta pieza:

- piezas 3 a 5: presupuesto con cargos (3), matriz ítem × tarea (4a), cantidades validadas en campo
  (4b) y control con valor ganado (5);
- pieza 6a: libro de costos con CPI, EAC y TCPI;
- pieza 6b: subcontratos y destajos con actas.

La pieza 6 se divide en 6a, 6b, 6c (esta) y 6d (mano de obra por jornal y nómina).

## Contexto

Hoy un material solo se puede registrar en el libro como un costo genérico. Esta pieza agrega:

- un catálogo de materiales de la organización;
- bodegas por proyecto con su kárdex al costo promedio ponderado móvil;
- compras directas y a bodega;
- salidas, devoluciones, traslados y ajustes de inventario;
- un **corte de consumos** que pasa al libro lo que salió de bodega.

El cálculo del control no cambia: los materiales llegan al libro como costos aprobados y entran al
CPI y al EAC como cualquier otro costo.

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Entrada de materiales | **Compra directa** a un destino: es costo al aprobar la compra. **Compra a bodega**: suma al inventario; el costo se reconoce con las salidas. |
| Identificación | **Catálogo de la organización**: código, nombre, unidad y categoría. Lo comparten todos los proyectos. Se crea en pantalla o se importa de Excel. |
| Bodegas | **Una o varias por proyecto**. Traslados entre bodegas del mismo proyecto, al promedio y sin costo. Sin bodega central de la organización. |
| Captura de salidas | **Web** en esta pieza. La app móvil sin conexión queda como pieza posterior; cada movimiento admite un id generado por el cliente. |
| Aprobación | Las compras y los ajustes se aprueban uno por uno, con soporte y sin autoaprobación. Las salidas, devoluciones y traslados no se aprueban una por una. Llegan al libro por un **corte de consumos** periódico que aprueba otra persona. |
| Costeo | **Promedio ponderado móvil por material y bodega**. Valor con IVA incluido, como en 6a. El costo de cada salida queda fijo al registrarla. No se permite inventario negativo. |
| Diferencias de inventario | **Ajuste por conteo físico**, que aprueba otra persona. El faltante es costo aprobado al destino elegido. El sobrante entra al promedio y no es costo. |
| Arquitectura | Un **registro único de movimientos** (kárdex) con **saldos guardados** por bodega y material, que se actualizan en la misma transacción con bloqueo de fila. |
| Permisos | Sección **Costos** existente, con su matriz CRUD y `reverse`. |
| Tipo de costo | **Materiales** (`CostKind.MATERIALS`). |

## 1. Modelo de datos

Va en `core/models/material.py` y en una migración, la 0030.

### 1.1 Catálogo de la organización

- **`Material`**:
  - organización;
  - código: único por organización, sin distinguir mayúsculas;
  - nombre;
  - unidad: texto corto, por ejemplo `m3`, `kg` o `und`;
  - categoría: texto opcional;
  - activo.
- Un material inactivo no se ofrece en formularios nuevos, pero sigue en el kárdex y en los saldos.
- Un material con movimientos no se borra; solo se desactiva.

### 1.2 Por proyecto

**`Warehouse`** (bodega):

- proyecto, nombre (único por proyecto), activa;
- autoría.

Solo se desactiva si su saldo es cero en todos los materiales.

**`MaterialPurchase`** (compra, `CM-001`):

- Tipo: `direct` o `warehouse`.
- Bodega: solo cuando el tipo es `warehouse`.
- Proveedor: nombre y NIT, como texto.
- Documento: tipo y número, reutilizando `CostDocumentType`.
- Fecha y soporte (adjunto).
- Estado: `draft`, `approved`, `rejected` o `voided`.
- Autoría y decisión: creó, editó por última vez, aprobó o rechazó, y el motivo.
- Número consecutivo por proyecto, asignado al crear.

**`MaterialPurchaseLine`**:

- orden, material, cantidad (> 0), valor unitario y valor total (cantidad × unitario, redondeado a
  centavos), con IVA incluido;
- solo en compras directas, el destino.

**Destino.** Es el mismo esquema de 6a y se usa en todos los documentos que llevan uno:

- `scope`: `indirect`, `item`, `chapter` o `selection`;
- `chapter` (`BudgetItem` de tipo capítulo, cuando aplica);
- ítems (M2M, para `item` y `selection`);
- tarea (opcional).

**`MaterialIssue`** (salida, `SA-001`):

- bodega, fecha, quién recibe (texto) y destino;
- notas;
- autoría;
- `client_id` UUID opcional y único, para la futura captura móvil;
- corte al que pertenece (nulo hasta que entra en uno).

**`MaterialIssueLine`**:

- material, cantidad (> 0);
- costo unitario y valor, que quedan fijos al registrar.

**`MaterialReturn`** (devolución, `DV-001`):

- fecha, autoría y corte;
- líneas que apuntan cada una a una `MaterialIssueLine`, con su cantidad;
- costo unitario: el de la línea de origen;
- bodega y destino: los de la salida de origen.

**`MaterialTransfer`** (traslado, `TR-001`):

- bodega de origen y bodega de destino, distintas y del mismo proyecto;
- fecha y autoría;
- líneas de material, cantidad, costo unitario y valor.

**`InventoryAdjustment`** (ajuste, `AJ-001`):

- bodega, fecha y destino del faltante;
- estado: `draft`, `approved`, `rejected` o `voided`;
- autoría, decisión y soporte (el acta de conteo).

**`InventoryAdjustmentLine`**:

- material, cantidad esperada, cantidad contada y diferencia;
- costo unitario y valor, fijados al aprobar.

**`ConsumptionCut`** (corte, número consecutivo por proyecto):

- periodo `[desde, hasta]`;
- estado: `draft`, `approved`, `rejected` o `voided`;
- autoría, decisión y soporte firmado;
- totales por destino, guardados al aprobar.

### 1.3 Kárdex

**`StockMovement`**:

- proyecto, bodega, material y fecha;
- tipo: `purchase`, `issue`, `return`, `transfer_out`, `transfer_in`, `adjustment_in`,
  `adjustment_out`, `purchase_void` o `adjustment_void`;
- cantidad con signo;
- costo unitario y valor con signo;
- secuencia creciente por bodega y material, que da el orden del kárdex;
- el documento de origen (una FK nullable por tipo de documento, con un check que exige exactamente
  una);
- autoría.

**`StockBalance`**:

- bodega y material (únicos juntos);
- cantidad y valor.

Reglas del saldo:

- El promedio no se guarda: es valor / cantidad.
- Checks en la base de datos: cantidad ≥ 0, valor ≥ 0, y si la cantidad es 0, el valor es 0.
- Índices para el kárdex por bodega y material, por fecha, y para el corte por proyecto, fecha y
  corte nulo.

### 1.4 Libro de costos

- `CostEntry` gana tres FK de origen nullable, como hoy tiene `certificate`:
  - `material_purchase`;
  - `consumption_cut`;
  - `inventory_adjustment`.
- El libro no reversa costos con estos orígenes: se anula el documento de origen.
- El filtro y la etiqueta de origen del libro suman "Compra", "Corte" y "Ajuste".

## 2. Cálculo y flujos

### 2.1 Saldos y promedio

Toda operación que mueve inventario hace lo mismo, en una transacción:

1. Bloquea con `select_for_update` las filas de `StockBalance` que toca, en orden de
   (bodega, material) para evitar bloqueos cruzados.
2. Escribe el `StockMovement`.
3. Actualiza el saldo.

**Entrada** (compra, sobrante, devolución, traslado de entrada): suma la cantidad y el valor al
saldo.

**Salida** (salida, faltante, traslado de salida):

- Si la cantidad del saldo no alcanza, se rechaza con "Solo hay X {unidad} de {material} en
  {bodega}".
- Costo unitario = valor / cantidad del saldo.
- Valor = cantidad × unitario, redondeado a centavos.
- Si la salida deja la cantidad en 0, el valor es exactamente el valor restante. Así el saldo nunca
  queda con residuos.

### 2.2 Compra a bodega

- Al aprobar, cada línea es una entrada a la bodega por su valor.
- No genera costo en el libro.
- Se anula solo si, para cada línea, el saldo actual del material en esa bodega es al menos la
  cantidad comprada. Al anularla, retira la cantidad y el valor comprados (`purchase_void`).
- Anular requiere `reverse`.

### 2.3 Compra directa

- Al aprobar, cada línea crea un `CostEntry` aprobado con:
  - tipo Materiales y fecha de la compra;
  - el proveedor como tercero, y el documento y el soporte de la compra;
  - el destino de la línea;
  - creador y editor de la compra, y como aprobador quien aprueba.
- No toca el inventario.
- Anularla escribe los reversos en el libro con fecha de hoy y requiere `reverse`.

### 2.4 Salida, devolución y traslado

- Se registran sin aprobación. Mueven el inventario al guardarse.
- Una salida que no está en un corte se puede editar o borrar. Primero se deshacen sus movimientos
  (la cantidad vuelve al saldo con el valor que se llevó) y después se aplica de nuevo al promedio
  del momento.
- **Devolución.**
  - Por línea de salida, la cantidad devuelta acumulada nunca supera la cantidad que salió.
  - Entra al costo unitario de la línea de origen.
  - Una salida con devoluciones no se puede editar ni borrar.
- **Traslado.** Sale de la bodega de origen al promedio y entra a la de destino con el mismo valor.
- **Periodo cerrado.** No se registra, edita ni borra una salida, devolución o traslado con fecha
  menor o igual al fin del último corte aprobado. Tampoco con fecha futura.

### 2.5 Ajuste de inventario

- **Borrador.**
  - La pantalla trae la cantidad esperada (el saldo actual) de los materiales que se elijan y se anota
    lo contado.
  - La diferencia se recalcula con el botón "Recalcular".
- **Al aprobar.**
  - Se vuelven a leer los saldos.
  - Si una cantidad esperada cambió desde el borrador, la aprobación se rechaza con "El inventario
    cambió desde el conteo; recalcula el ajuste".
- **Faltante.**
  - Sale al promedio (`adjustment_out`).
  - Crea un `CostEntry` aprobado por el valor total de los faltantes, al destino del ajuste, con fecha
    del ajuste.
- **Sobrante.**
  - Entra al promedio vigente (`adjustment_in`).
  - Si el saldo está en cero, entra al costo unitario del último movimiento de entrada de ese material
    en la bodega. Si no hay ninguno, la línea exige un costo unitario.
- **Anular.**
  - Requiere `reverse`.
  - Solo se puede si el saldo alcanza para retirar los sobrantes.
  - Escribe `adjustment_void` y los reversos en el libro.

### 2.6 Corte de consumos

- **Periodo propuesto.**
  - Empieza el día siguiente al fin del último corte aprobado. Si no hay cortes, empieza en la primera
    salida sin corte.
  - Termina en una fecha que se elige, nunca en el futuro.
  - Solo puede haber un corte abierto (`draft` o `rejected`) por proyecto.
- **Contenido.**
  - Las salidas y devoluciones del proyecto con fecha en el periodo y sin corte.
  - Se agrupan por destino: `scope`, capítulo o conjunto de ítems, y tarea.
  - El valor de cada destino es lo que salió menos lo devuelto.
  - Los destinos con valor 0 se muestran, pero no generan costo.
- **Mientras está en borrador.**
  - La propuesta se recalcula al abrirlo.
  - Las salidas y devoluciones quedan asignadas al corte solo al aprobarlo.
- **Al aprobar.**
  - Requiere el reporte firmado (adjunto).
  - Recalcula. Si el total por destino o el número de salidas y devoluciones cambió desde la última
    vez que se guardó el borrador, se rechaza con "Los consumos del periodo cambiaron; revisa el
    corte y vuelve a cargar el reporte firmado". El borrador guarda un resumen de esos valores al
    guardarse o recalcularse, y el PDF imprime ese resumen. Es la misma regla de "lo aprobado es lo
    firmado" de 6b.
  - Un destino cuyo neto sea negativo genera un costo aprobado negativo. Pasa cuando en el periodo se
    devuelve material que salió en un corte anterior.
  - Asigna el corte a sus salidas y devoluciones.
  - Crea un `CostEntry` aprobado por cada destino con valor, con fecha del fin del periodo.
  - Cierra el periodo.
- **Anular.**
  - Requiere `reverse` y solo aplica al último corte aprobado.
  - Escribe los reversos con fecha de hoy, libera las salidas y devoluciones (corte nulo) y reabre el
    periodo.

### 2.7 Aprobaciones

- Sin autoaprobación, como en 6a y 6b: no aprueba quien creó el documento ni quien lo editó por
  última vez. Se aplica en el servicio y con checks en la base de datos.
- Aplica a compras, ajustes y cortes.
- Rechazar exige un motivo. Al editar un documento rechazado, vuelve a borrador.
- Cargar el soporte firmado no cuenta como edición. Editar quita el soporte firmado del corte y del
  ajuste.

## 3. Pantallas, documentos y permisos

Las pantallas son web, sin JavaScript, como en 6b: los formularios con líneas usan "Agregar líneas" y
"Recalcular".

### 3.1 Catálogo de materiales (organización)

- Lista con búsqueda por código o nombre y filtro por categoría y activo.
- Formulario.
- Importación desde Excel con vista previa:
  - columnas: código, nombre, unidad, categoría;
  - crea los códigos nuevos y actualiza nombre, unidad y categoría de los que existen;
  - nonce de un solo uso, como `CostImportToken`.

### 3.2 Pestaña "Materiales" del proyecto

1. **Inventario**
   - Saldo por bodega y material: cantidad, unidad, promedio y valor.
   - Filtros por bodega, categoría y texto.
   - Cada fila abre el **kárdex** del material en esa bodega: los movimientos en orden, con documento,
     entrada, salida, saldo corrido y promedio, y un filtro por fechas.
2. **Compras**: lista filtrable por tipo, estado y fecha, y el formulario de la compra.
3. **Salidas**: un formulario rápido y la lista filtrable por fecha, bodega, destino y corte.
   - Desde la salida se registra la devolución.
4. **Traslados**.
5. **Ajustes**: un formulario de conteo que trae lo esperado.
6. **Cortes**:
   - propuesta del periodo y detalle por destino, con el desglose por material;
   - PDF para firmar;
   - carga del reporte firmado y aprobación;
   - anulación.
7. **Bodegas**: crear, renombrar y desactivar.

Avisos de la pestaña:

- salidas sin cortar con más de 31 días;
- borradores de compra, ajuste o corte pendientes de aprobación.

### 3.3 Integración con Costos y Control

- La bandeja **"Por aprobar"** de Costos suma las compras, los ajustes y los cortes en borrador del
  proyecto.
- El **libro** muestra el origen ("Compra CM-003", "Corte 2", "Ajuste AJ-001") con enlace al
  documento.
- **Control**, vista "Por ítem":
  - los costos de materiales entran al costo real sin cambiar el cálculo;
  - se agrega como dato informativo el valor en bodega del proyecto, que no se cuenta como costo.

### 3.4 Documentos

- PDF del corte para firmar (reportlab, A4 horizontal):
  - destinos con su desglose por material;
  - totales;
  - firmas.
- Excel con `safe_cell`:
  - inventario;
  - kárdex de un material;
  - salidas de un periodo;
  - detalle de un corte.

### 3.5 Permisos

Todo usa la sección **COSTS** de la matriz fija de `core/access.py`.

| Acción | Capacidad |
|---|---|
| Ver inventario, kárdex y documentos | `read` |
| Registrar compras, salidas, devoluciones, traslados, ajustes y cortes | `create` / `update` |
| Borrar borradores y salidas sin corte | `delete` |
| Aprobar | `approve` |
| Rechazar | `reject` |
| Anular compras, ajustes y cortes | `reverse` |
| Gestionar bodegas | `update` |

El catálogo de materiales de la organización requiere `create` y `update` de COSTS con alcance de
**toda la organización**. Si el plan encuentra un permiso de catálogo de organización ya existente
que se ajuste mejor, lo reutiliza y lo deja anotado.

## 4. Pruebas, rendimiento y entrega

- **Promedio móvil:**
  - varias compras con precios distintos;
  - redondeo de cada salida a centavos;
  - sacar todo el inventario deja el saldo en exactamente 0 / 0;
  - traslado ida y vuelta;
  - devolución al costo de origen;
  - editar una salida;
  - anular una compra a bodega con y sin saldo suficiente.
- **Inventario negativo:** rechazado. Dos salidas concurrentes sobre el mismo saldo terminan con una
  rechazada (prueba con hilos y `TransactionTestCase`, como las de 6a).
- **Corte:**
  - agrupación por destino;
  - devoluciones restando;
  - periodo cerrado;
  - la regla de "lo aprobado es lo firmado";
  - anulación con reversos.
  - La suma de los costos del corte es igual al valor que salió menos el devuelto en el periodo.
- **Ajuste:** faltante y sobrante; inventario que cambió desde el conteo; sobrante con saldo cero.
- **Aislamiento y permisos:**
  - 404 con ids de otra organización, proyecto o bodega;
  - matriz de permisos por acción;
  - sin autoaprobación.
- **Carga:** 2.000 materiales, 5 bodegas y 50.000 movimientos. Inventario, kárdex y propuesta del
  corte deben responder en menos de 1 s, con un número fijo de consultas.
- **Demo:** `seed_control_demo` agrega materiales, una bodega, compras, salidas, una devolución, un
  traslado, un ajuste y un corte aprobado.
- **Revisión visual:** a 1440 y 390 px, sin texto cortado ni desbordes.
- **Verificaciones de siempre:** ruff, mypy, `makemigrations --check`, guardián de nombres, migración
  que baja a la 0029 y vuelve a subir.

## Fuera de alcance

- Captura de salidas en la app móvil: pieza posterior.
- Órdenes de compra, comprometido de compras y cuentas por pagar.
- Bodega central de la organización y traslados entre proyectos.
- Unidades de compra distintas de la unidad de inventario (conversiones).
- Insumos o APU en el presupuesto, y consumo teórico contra real por ítem.
- Lotes, seriales y vencimientos.
- Mano de obra por jornal y nómina: pieza 6d.
