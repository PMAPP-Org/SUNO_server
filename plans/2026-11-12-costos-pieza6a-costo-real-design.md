# Costos, pieza 6a: libro de costos y control de costo

Fecha: 2026-11-12. Repo: `SUNO_server` (una PR). Sin cambios en la app ni en el contrato de
sincronización.

Antecedentes: presupuestos contractual y vigente con líneas de AIU (pieza 3), matriz ítem × tarea
(pieza 4a), cantidades validadas en campo (pieza 4b) y control con valor ganado: PV, EV, SV, SPI
y curva S (pieza 5). Equipos ya costea sus usos por hora con tarifas históricas.

## Contexto

La pieza 5 mide el cronograma en pesos, pero no el costo: SUNO no conoce el costo real (AC) de
una obra. Hoy solo registra el costo de equipos propios, combustible, transporte y mantenimiento.
Materiales, mano de obra, nómina, subcontratos y otros gastos no existen.

El costo real se registrará en SUNO, pero cada tipo nace de documentos distintos. Se divide en
piezas:

- **6a (este documento):** libro de costos común a todos los tipos, con registro genérico
  (formulario y Excel) y aprobación; costo automático de equipos y transporte; costo real, CPI,
  EAC y TCPI en la página Control.
- **6b:** subcontratos y destajos (contrato, actas de avance, anticipo, retención de garantía);
  un acta aprobada genera su movimiento en el libro.
- **6c:** materiales (compras, entradas a almacén, salidas a obra a costo promedio).
- **6d:** mano de obra por jornal (tareo) y nómina de obra.

Cada pieza posterior reemplaza el registro genérico de un tipo por su captura especializada, sin
cambiar el cálculo de esta.

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Origen del costo real | Se registra en SUNO: libro propio más costos automáticos de lo que SUNO ya registra. |
| Indirectos | **Separados.** CPI directo (AC directo frente a EV), CPI de administración (AC indirecto frente a administración ganada) y CPI total. |
| EAC | Solo `EAC = AC + (BAC − EV) / CPI`, con VAC y TCPI. Sin rango optimista ni pesimista. |
| Asignación de costos directos | Un **ítem**, un **capítulo completo** o una **selección de ítems de un capítulo**; tarea opcional. Los indirectos van al proyecto, sin ítem. |
| Costo por grupo | Un capítulo o una selección se compara con el EV sumado de sus ítems; sus ítems muestran el índice del grupo, sin reparto. |
| Correcciones | Borrador → aprobado; lo aprobado no se edita ni se borra: se reversa. Solo cuenta lo aprobado. |
| Equipos y combustible | Equipos propios por horas × tarifa completa; combustible y mantenimiento no se suman (ya están en la tarifa). |
| Permisos | Sección nueva "Costos" en la matriz por sección existente (`core/access.py`), con capacidades CRUD explícitas por rol. |
| Autoaprobación | Prohibida: nadie aprueba sus propios costos. |
| IVA | Un solo valor, con IVA incluido (como los precios del presupuesto). |
| Soporte | Adjunto obligatorio para aprobar; el borrador puede guardarse sin él. |
| Persistencia del cálculo | Al momento, como la pieza 5. Sin cortes guardados. |

## 1. Modelo de datos (`core/models/cost.py`, una migración)

### 1.1 `CostEntry` (movimiento del libro)

| Campo | Contenido |
|---|---|
| `org`, `project` | Organización y proyecto (`OrgScopedModel`). |
| `incurred_on` | Fecha de causación; define el periodo del control. |
| `kind` | `materials`, `labor`, `payroll`, `subcontract`, `rented_equipment`, `other` (Materiales, Mano de obra, Nómina, Subcontrato, Equipo alquilado, Otros gastos). |
| `description` | Texto, hasta 300 caracteres. |
| `amount` | `Decimal(18, 2)`, con IVA incluido; distinto de cero (negativo solo en reversos). |
| `third_party` | Proveedor, contratista o trabajador (texto, hasta 200). |
| `document_type`, `document_number` | Factura, cuenta de cobro, nómina, recibo u otro; y su número. |
| `scope` | `indirect`, `item`, `chapter` o `selection`. |
| `chapter` | Ítem capítulo (`BudgetItem`) para `chapter` y `selection`. |
| `items` | Ítems (`BudgetItem`, M2M `CostEntryItem`) para `item` (uno) y `selection` (dos o más, del mismo capítulo). |
| `task` | Tarea del cronograma, opcional; nula en indirectos. |
| `status` | `draft`, `approved`, `rejected`. Un movimiento reversado sigue `approved` y se reconoce por su reverso. |
| `created_by`, `created_at`, `updated_by`, `updated_at` | Autoría. |
| `decided_by`, `decided_at`, `decision_note` | Quién aprobó o rechazó, cuándo y el motivo (obligatorio al rechazar). |
| `reverses` | FK al movimiento que anula (único: un movimiento se reversa una sola vez). |
| `attachment` | Soporte (módulo de adjuntos existente). |

Los ítems se guardan por su identidad estable (`BudgetItem`), no por versión, así una versión
nueva del presupuesto no rompe las asignaciones.

### 1.2 Reglas e invariantes

- Restricciones en la base: `amount ≠ 0`; si `status = approved` y no es reverso, hay soporte y
  `decided_by ≠ created_by`; `reverses` único.
- Validaciones de servicio (en `transaction.atomic`, con `scoped_qs`):
  - Los ítems y el capítulo pertenecen a presupuestos del mismo proyecto.
  - Una selección es de un solo capítulo.
  - La tarea es del proyecto.
  - Al registrar, la asignación debe ser `item` en la versión vigente de su presupuesto.
- **Reverso:** nace aprobado, con `amount = −original`, la fecha del día en que se reversa, la
  misma asignación, `decided_by` igual a quien reversa, y el motivo en `decision_note`. No pide
  soporte propio.
- Un rechazado vuelve a borrador cuando su autor lo corrige y lo reenvía.

### 1.3 Costos automáticos (no se copian al libro)

- **Equipos propios:** usos ejecutados (`EquipmentUsage`) del proyecto, a horas × tarifa vigente
  ese día (`equipment/costing.py`, el mismo cálculo de los informes de equipos). Con
  `budget_item`, son costo directo de ese ítem; sin él, directo sin asignar.
- **Transporte:** costos del proyecto en `transport`, como directo sin asignar.
- **No se suman:** el combustible (`FuelFill`) y el mantenimiento (`WorkOrderLine`), porque ya
  están en la tarifa horaria.

## 2. Libro de costos: pestaña "Costos" del proyecto

Va junto a Cronograma, Presupuesto, Matriz y Control.

1. **Lista:**
   - Filtros: estado, tipo, rango de fechas, capítulo, ítem, solo automáticos.
   - Columnas: fecha, tipo, descripción, asignación, tercero, documento, valor, estado, quién
     registró y quién aprobó.
   - Totales aprobados por tipo al pie.
   - Las líneas automáticas aparecen agrupadas por día y equipo (o por viaje), marcadas
     "automático" y de solo lectura.
2. **Formulario:**
   - Los campos de 1.1.
   - La asignación se elige en dos pasos: primero el tipo, luego el ítem, el capítulo o los
     ítems del capítulo, con buscador sobre el presupuesto vigente.
   - El soporte se adjunta en el mismo formulario.
   - Guarda en borrador.
3. **Bandeja "Por aprobar":**
   - Borradores de otros usuarios, con el soporte a la vista.
   - Botones Aprobar y Rechazar; rechazar pide motivo.
4. **Rechazados:** el autor ve los suyos con el motivo, los corrige y los reenvía.
5. **Reversar:**
   - Desde un movimiento aprobado, pide un motivo.
   - Opcionalmente abre un borrador nuevo precargado con los mismos datos.
6. **Importar desde Excel** (`.xlsx`):
   - Plantilla descargable con las columnas fecha, tipo, código de ítem o capítulo, tarea
     (opcional), descripción, valor, tercero, tipo de documento y número.
   - Un código de capítulo asigna el capítulo completo; las selecciones de ítems solo se hacen
     en el formulario.
   - Vista previa con errores por fila.
   - Carga todo o nada; todo entra en borrador.
   - Los soportes se adjuntan después, en cada movimiento.
7. **Exportar a Excel:** el libro filtrado, con `safe_cell`, auditado.

## 3. Cálculo (extiende `control/`)

### 3.1 Costo real (AC)

- AC a una fecha = movimientos aprobados con `incurred_on` hasta la fecha + costos automáticos
  hasta la fecha.
- Un reverso cae en su propia fecha: los periodos pasados no cambian.
- El AC no se acota (el EV sí).
- Se separa en tres bolsas:

| Bolsa | Contenido | Se compara con |
|---|---|---|
| Directo asignado | Movimientos `item`, `chapter`, `selection`; equipos con ítem. | EV de esos ítems. |
| Directo sin asignar | Equipos sin ítem y transporte. | Solo entra en el total directo, con aviso. |
| Indirecto | Movimientos `indirect`. | Administración ganada. |

### 3.2 Indicadores a la fecha de corte

| Indicador | Cálculo |
|---|---|
| AC directo | Directo asignado + directo sin asignar. |
| CV, CPI | `CV = EV − AC directo`; `CPI = EV / AC directo`, vacío si `AC directo = 0`. |
| EAC, VAC | `EAC = AC directo + (BAC − EV) / CPI`; `VAC = BAC − EAC`. Vacíos si no hay CPI. |
| TCPI | `TCPI = (BAC − EV) / (BAC − AC directo)`, vacío si el divisor es ≤ 0 (el presupuesto ya se agotó; se avisa). |
| Administración ganada | Por presupuesto: EV del presupuesto × (monto de la línea Administración ÷ costo directo) de su versión vigente. |
| CPI de administración | Administración ganada / AC indirecto. |
| CPI total | (EV + administración ganada) / (AC directo + AC indirecto). |

- La línea de Administración es la `BudgetCharge` cuyo nombre, sin mayúsculas ni tildes, empieza
  por "administracion".
- Un presupuesto sin esa línea no aporta administración ganada y se avisa. Si ninguno la tiene,
  el CPI de administración queda vacío.
- Los CPI van a dos decimales. El color se decide sobre el valor redondeado, con los umbrales
  del SPI: verde `≥ 0,95`, ámbar `≥ 0,85`, rojo por debajo.

### 3.3 Desglose

| Desglose | Cálculo |
|---|---|
| Por capítulo | AC del capítulo (movimientos de ítems, capítulo o selección suyos; equipos con ítem) frente a su EV. |
| Por ítem | Índice propio si todo su costo es de ítem; si recibe costo de un capítulo o selección, muestra el índice del grupo, marcado ("índice del grupo: Estructura"). |
| Por tarea | Solo los movimientos con tarea, frente al EV de la tarea; se avisa cuánto costo directo no indicó tarea. |
| Por ubicación | Sin costo en esta pieza. |

### 3.4 Periodos

- El AC acumulado se suma por los mismos periodos de la pieza 5 (semana o mes, partidos en el
  corte).
- Los periodos posteriores al corte no llevan AC.

### 3.5 Avisos nuevos

- Costo directo sin asignar: total y por origen.
- Presupuestos sin línea de Administración.
- Costos asignados a ítems que ya no son `item` en la versión vigente: cuentan en su capítulo de
  origen y se listan.
- Borradores pendientes de aprobación hasta el corte (cantidad y valor): no cuentan.
- Presupuesto agotado (TCPI sin valor).

## 4. Página Control

1. **Indicadores:** una segunda fila, debajo de la del cronograma.
   - AC directo, con su % del BAC.
   - CV y CPI, con color.
   - EAC y VAC; en rojo si `EAC > BAC`.
   - TCPI, con la nota "rendimiento necesario para terminar en presupuesto".
   - Un bloque con el CPI de administración y el CPI total, o "sin línea de Administración".
2. **Curva S:**
   - Tercera serie: AC acumulado hasta el corte.
   - La leyenda y la tabla de la curva suman su columna.
   - Se conserva lo resuelto a 390 px.
3. **Desglose:**
   - Columnas nuevas: AC, CV y CPI.
   - Selector "Ordenar por: cronograma (SPI) / costo (CPI)", por defecto SPI; se pasa por URL
     con el parámetro `orden`, que acepta `spi` o `cpi`.
4. **Avisos:** los de 3.5.
5. **Excel:**
   - Columnas de AC en las hojas de periodos y desglose.
   - Hoja nueva "Indicadores".
6. **Estado vacío:** sin costos aprobados al corte, la fila de costo dice "Sin costos aprobados
   al corte", con enlace al libro.
7. **Permisos:** la fila de costo, la serie AC y sus columnas solo se muestran a quien puede leer
   la sección Costos. Quien no puede ve el control de cronograma como hoy.

## 5. Permisos (sección "Costos" en `core/access.py`)

| Capacidad | Gestor | Revisor | Colaborador | Consulta |
|---|:-:|:-:|:-:|:-:|
| `create`: crear borrador (formulario) | ✓ | ✓ | ✓ | – |
| `import`: importar desde Excel (en borrador) | ✓ | – | ✓ | – |
| `read`: leer libro y control de costo | ✓ | ✓ | ✓ | ✓ |
| `update`: actualizar un borrador | todos | todos | solo propios | – |
| `delete`: borrar un borrador | todos | todos | solo propios | – |
| `approve` y `reject` | ✓ | ✓ | – | – |
| `reverse`: reversar un aprobado | ✓ | – | – | – |
| `export`: exportar a Excel | ✓ | ✓ | ✓ | ✓ |

- `reverse` es una capacidad nueva de `Capability`.
- Lo aprobado nunca se actualiza ni se borra.
- Nadie aprueba ni rechaza lo que registró.
- Propietario y Administrador tienen rol Gestor en todas las secciones, como hoy.
- El agente de campo no tiene acceso.
- El alcance (organización o proyectos elegidos) funciona como en las demás secciones.
- Auditoría, en la misma transacción que la acción: `cost.created`, `cost.updated`,
  `cost.deleted`, `cost.approved`, `cost.rejected`, `cost.reversed`, `cost.imported`,
  `cost.exported`.

## 6. Rendimiento

- Número fijo de consultas, verificado con `assertNumQueries`: unas tres más que la pieza 5
  (movimientos, asignaciones y usos de equipos con sus tarifas).
- Objetivo: menos de 1 s con 5000 pares tarea × ítem y 20 000 movimientos, medido con una prueba
  de carga antes de integrar.

## 7. Pruebas

- **Modelo y servicio:**
  - Restricciones de la base.
  - Asignaciones válidas e inválidas (otro proyecto, selección de dos capítulos, capítulo como
    ítem, tarea ajena).
  - Ciclo borrador → aprobado o rechazado → reenviado.
  - Soporte obligatorio para aprobar.
  - Prohibición de autoaprobación.
  - Reverso en su fecha y una sola vez.
- **Permisos:** la matriz completa por rol y alcance (incluido "solo propios" del Colaborador),
  aislamiento por organización, 404 y 403 según RD-11.
- **Cálculo:**
  - Las tres bolsas.
  - Grupos de capítulo y selección.
  - Ítems retirados de la versión vigente.
  - Administración ganada con varios presupuestos y sin línea.
  - CPI con AC = 0; EAC sin CPI; TCPI con presupuesto agotado.
  - Reverso que no cambia periodos pasados.
  - Equipos con y sin ítem, y combustible que no se suma.
- **Importación:** plantilla, errores por fila, todo o nada, todo en borrador.
- **Control:** fila de costo visible u oculta según permisos, orden por CPI, Excel, estado vacío.
- **Revisión visual:** 1440 y 390 px con datos de ejemplo (`seed_control_demo` ampliado con
  costos).

## 8. Entrega

- Una PR del servidor, con una migración aditiva: tablas nuevas, sin tocar datos existentes.
- Sin cambios en la app ni en el contrato de sincronización.
- Se despliega al integrar; release normal con release-please.

## Fuera de alcance

- Subcontratos y destajos con actas (6b), almacén de materiales (6c), tareo y nómina (6d).
- Catálogo de terceros.
- Reparto de indirectos a ítems.
- Equipos alquilados como costo automático (se registran como movimiento de tipo "Equipo
  alquilado").
- EAC alternativos (optimista, pesimista).
- Costo por ubicación.
- Costo en la app móvil.
- Matriz de permisos configurable por organización.
