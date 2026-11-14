# Costos, pieza 6b: subcontratos y destajos

Fecha: 2026-11-14. Repo: `SUNO_server` (una PR). Sin cambios en la app ni en el contrato de
sincronización.

Antecedentes: presupuesto con cargos (pieza 3), matriz ítem × tarea (4a), cantidades validadas en
campo (4b), control con valor ganado (5) y libro de costos con CPI, EAC y TCPI (6a). La pieza 6
se dividió en 6a (libro), 6b (esta), 6c (materiales) y 6d (mano de obra por jornal y nómina).

## Contexto

Hoy un subcontrato o un destajo solo se puede registrar en el libro como un costo genérico. Esta
pieza agrega su captura especializada:

- el contrato con sus ítems, tareas, precios, cargos y descuentos;
- los otrosíes;
- las actas de avance por cantidades;
- el anticipo y la retención de garantía.

Un acta aprobada genera sus movimientos en el libro. El cálculo del control no cambia.

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Cantidades del acta | SUNO propone lo **validado en campo** en el periodo, menos lo ya pagado; se puede ajustar con justificación obligatoria. |
| Alcance de una línea | Un ítem del presupuesto **más las tareas que cubre** (vacío: todas las del ítem). Si dos contratos cubren el mismo ítem en la misma tarea, se avisa y se confirma. |
| Cargos | **Configurables por contrato** (nombre, %, base), como `BudgetCharge`: AIU con IVA sobre utilidad, IVA pleno o ninguno. |
| Descuentos | **Configurables por contrato** (retención en la fuente, ReteICA, ReteIVA, estampillas…), además del anticipo y la retención de garantía. Sin registro de pagos. |
| Destajos | El **mismo modelo** con tipo Subcontrato o Destajo. El tipo cambia los valores por defecto y el tipo de costo en el libro. |
| Permisos | La sección **Costos** existente, con su matriz CRUD. Nadie aprueba lo que preparó o editó por última vez. |
| Soporte | El **acta firmada** escaneada es obligatoria para aprobar. SUNO genera el PDF para firmar. |
| Costo en el libro | El bruto del acta (directo + cargos), con IVA. Anticipo, retención y descuentos no son costo. |

## 1. Modelo de datos (`core/models/contract.py`, una migración)

- **`Contract`:**
  - Campos: organización, proyecto, `kind` (`subcontract` | `piecework`), número (único por
    proyecto), objeto, contratista (texto), fechas de inicio y fin.
  - Estado: `draft`, `approved`, `settled` (liquidado) o `voided` (anulado).
  - Anticipo: % y valor calculado. Retención de garantía: %.
  - Autoría y decisión: creado, editado, aprobado o rechazado por, y motivo.
- **`ContractLine`:**
  - El ítem (`BudgetItem`, identidad estable) y las tareas que cubre (M2M; vacío: todas).
  - Unidad, cantidad contratada y precio unitario del contrato.
- **`ContractCharge` y `ContractDeduction`:** orden, nombre, % y base (`direct`, `subtotal` o
  `line:<orden>`). El cálculo reutiliza `budgets.totals`.
  - La base de los descuentos además admite `gross` (bruto del acta).
- **`ContractAmendment` (otrosí):**
  - Número, fecha, motivo, estado (`draft`, `approved`, `rejected`) y autoría.
  - Cambios: líneas nuevas, aumento de cantidad por línea, nueva fecha de fin.
  - Al aprobarse se aplica y queda en el historial. La cantidad contratada vigente = original +
    otrosíes aprobados.
- **`ContractCertificate` (acta):**
  - Número consecutivo por contrato, periodo `[desde, hasta]`, `is_settlement` (liquidación).
  - Estado: `draft`, `approved`, `rejected` o `voided`.
  - Autoría y decisión, y el soporte firmado (adjunto).
  - Totales guardados al aprobar: bruto directo, cargos, bruto, amortización, retención,
    descuentos, devolución de la retención y neto.
  - Enlace a sus movimientos del libro.
- **`CertificateLine`:** la línea del contrato, la cantidad propuesta (validada en campo), la
  cantidad del acta, la justificación (obligatoria si difieren) y el valor.
- **Libro (6a):** `CostEntry` gana `certificate` (FK opcional). Los movimientos con acta no se
  editan ni se reversan desde el libro.
- **Restricciones en la base y el servicio:**
  - El acumulado aprobado por línea no pasa la contratada vigente.
  - Los periodos de las actas de un contrato no se solapan.
  - Solo hay actas de un contrato aprobado.
  - Solo se anula la última acta aprobada.
  - Un contrato liquidado o anulado no admite actas ni otrosíes.

## 2. Pantallas: pestaña "Contratos" del proyecto

1. **Lista de contratos:**
   - Columnas: número, tipo, contratista, objeto, valor vigente, ejecutado acumulado y su %,
     anticipo por amortizar, retenido y estado.
   - Filtros: tipo y estado.
2. **Formulario de contrato:**
   - Encabezado.
   - Líneas, con buscador de ítems del presupuesto vigente y selector de tareas.
   - Cargos y descuentos, con plantillas: "AIU con IVA sobre utilidad", "IVA pleno", "Sin
     cargos" y "Retenciones típicas" (líneas en 0 % para llenar).
   - Un destajo arranca sin cargos, sin anticipo y sin garantía.
   - Si otra línea ya cubre el mismo ítem en la misma tarea, se avisa y se pide confirmar.
3. **Ficha del contrato:**
   - Pestañas Resumen, Líneas (contratado, ejecutado y saldo), Actas, Otrosíes e Historial.
   - Botones Nueva acta, Nuevo otrosí, Liquidar y Anular.
4. **Nueva acta:**
   - El periodo va por defecto del día siguiente a la última acta hasta hoy.
   - La cantidad propuesta por línea es lo validado en campo en el periodo, para el ítem y las
     tareas de la línea, menos lo ya pagado. Lo ya pagado incluye lo capturado antes y validado
     tarde, para no pagarlo dos veces.
   - Se ajusta con justificación.
   - Cálculo en vivo; guarda en borrador; descarga el PDF para firmar.
5. **Aprobación:**
   - Sección propia en la bandeja "Por aprobar" de Costos.
   - Muestra el acta, las diferencias contra lo validado y el soporte firmado.
   - Aprobar, o rechazar con motivo.
   - Los contratos y los otrosíes se aprueban igual.
6. **Liquidación:**
   - Acta final (`is_settlement`), que puede ir con cantidades en cero.
   - Descuenta el anticipo que falte y devuelve la retención acumulada.
   - El contrato pasa a liquidado.
7. **Anulación:**
   - De la última acta aprobada, con motivo; genera los reversos en el libro.
   - De un contrato sin actas aprobadas.
8. **Excel:** la lista de contratos con saldos, y el detalle de un contrato con sus actas.
   Con `safe_cell` y auditado.

## 3. Cálculo

### 3.1 Acta

1. Bruto directo = Σ (cantidad del acta × precio del contrato) por línea.
2. Cargos: las líneas del contrato sobre sus bases (`budgets.totals`).
3. **Bruto** = bruto directo + cargos. Es el costo que va al libro.
4. Amortización del anticipo = bruto × (anticipo ÷ valor vigente del contrato), sin pasar del
   saldo por amortizar. En la liquidación es todo el saldo.
5. Retención de garantía = bruto × su %. En la liquidación se devuelve todo lo retenido.
6. Descuentos: cada línea sobre su base.
7. **Neto** = bruto − amortización − retención − descuentos (+ devolución en la liquidación).

Todo en `Decimal` con redondeo comercial a centavos por línea, como el presupuesto. El valor
vigente del contrato es el bruto directo de sus cantidades vigentes más sus cargos.

### 3.2 Paso al libro (al aprobar)

- Un `CostEntry` aprobado por cada línea del acta con valor:
  - **Fecha de causación:** el fin del periodo del acta.
  - **Tipo de costo:** Subcontrato, o Mano de obra si es destajo.
  - **Asignación:** el ítem de la línea, y la tarea si la línea cubre una sola.
  - **Valor:** el bruto directo de la línea + su parte de los cargos (en proporción al bruto
    directo). El ajuste de redondeo va a la línea de mayor valor, así la suma es exactamente el
    bruto.
  - **Soporte:** el acta firmada.
  - **Aprobación:** quien aprobó el acta.
- **Anulación:** los reversos de todos sus movimientos, fechados el día de la anulación.

### 3.3 Acumulados por línea

- **Contratado vigente:** original + otrosíes aprobados.
- **Pagado:** Σ de las actas aprobadas.
- **Saldo:** contratado vigente − pagado.
- **Validado en campo sin pagar:** lo validado para el ítem y las tareas de la línea, menos lo
  pagado.

## 4. Integración, permisos y avisos

- **Libro (6a):**
  - Filtro de origen: manual, acta, automático. Las líneas de acta dicen "Acta 3 · Contrato
    SC-012" y enlazan a ella.
  - Un costo manual de tipo Subcontrato o Mano de obra sobre un ítem con contrato vigente muestra
    un aviso, sin bloquear.
- **Control (5 y 6a):**
  - Sin cambios de cálculo.
  - El desglose por ítem gana "comprometido en contratos" (contratado vigente), que es
    informativo.
  - Aviso cuando lo contratado de un ítem supera su presupuesto vigente.
- **Permisos:** la sección Costos (`core/access.py`).

  | Acción | Capacidad |
  |---|---|
  | Crear y editar borradores de contratos, otrosíes y actas | `create`, `update` |
  | Aprobar o rechazar | `approve`, `reject` |
  | Anular actas o contratos y liquidar | `reverse` |
  | Leer | `read` |
  | Exportar | `export` |

  Nadie aprueba ni rechaza lo que creó o editó por última vez. El Colaborador edita solo sus
  borradores.
- **Auditoría:** `contract.*`, `amendment.*` y `certificate.*` (created, updated, approved,
  rejected, voided, settled, exported), dentro de su transacción.
- **PDF del acta:**
  - Se genera con `reportlab`, como `backoffice/record_pdf.py`.
  - Contenido: proyecto y contrato; tabla por línea con contratado, anterior, esta acta,
    acumulado y saldo, en cantidad y valor; cargos, amortización, retención, descuentos y neto;
    firmas.
- **Avisos de la pestaña:**
  - actas en borrador hace más de 15 días;
  - validado sin pagar por línea;
  - contratos vencidos sin liquidar;
  - anticipo sin amortizar en contratos al 100 %;
  - ítems con dos contratos en la misma tarea.

## 5. Pruebas, rendimiento y entrega

- **Cálculo:**
  - AIU con IVA sobre utilidad, IVA pleno y sin cargos.
  - La amortización que no pasa del saldo.
  - La liquidación, el redondeo y el reparto de cargos que suma exacto.
- **Propuesta desde campo:** el periodo, las tareas de la línea, lo ya pagado y la validación
  tardía.
- **Topes:** por otrosí y periodos que se solapan.
- **Ciclo:** borrador → aprobado o rechazado; anulación con reversos en el libro; liquidación.
- **Permisos:** la matriz completa, la autoaprobación, el aislamiento por organización y el 404
  o 403 (RD-11).
- **Documentos:** el PDF y el Excel.
- **Rendimiento:** número fijo de consultas en la lista y la ficha; prueba de carga con 200
  contratos, 2000 líneas y 5000 actas.
- **Revisión visual:** a 1440 y 390 px.
- **Entrega:** una PR del servidor con una migración aditiva. Se despliega al integrar; release
  normal.

## Fuera de alcance

- Pagos y tesorería (giros, saldo por pagar).
- Catálogo de contratistas, pólizas y garantías del contrato.
- Firma electrónica.
- Actas desde la app móvil.
- Reajustes de precios por índices.
- Materiales (6c), tareo y nómina (6d).

## Decisiones tomadas al planear

Precisiones del plan `2026-11-14-costos-pieza6b-contratos-plan.md`; no cambian lo aprobado. Las
marcadas con ⚑ conviene revisarlas.

- Código: paquete `suno/contracts` (cálculo puro en `calc.py`, propuesta en `proposal.py`,
  servicios en `services.py` y `certificates.py`, consultas en `queries.py`, PDF, Excel y
  vistas) bajo `plataforma/proyectos/<id>/contratos/`; modelos en `core/models/contract.py`.
- Migración `0029_contracts`, aditiva: nueve tablas, `cost_entry.certificate` (nula) y el tipo
  de documento "Acta de avance". Sin `RunPython`.
- Permisos: sin políticas nuevas. Las de Costos por proyecto y `can_update_cost`/
  `can_delete_cost` (estructurales) sobre contrato, otrosí y acta, que llevan `project`. Preparar
  la acta de liquidación pide `reverse`; aprobarla, `approve`.
- ⚑ Rechazar un contrato lo deja en borrador con el motivo a la vista (el contrato no tiene
  estado "rechazado"); al editarlo se limpia. Otrosíes y actas sí se rechazan y solo su autor
  los corrige; al guardarlos vuelven a borrador.
- Se borran (capacidad `delete`; el Colaborador, lo suyo) el contrato en borrador, el otrosí
  abierto y el acta abierta, con su evento `*.deleted`. Anular un contrato: aprobado, sin actas
  aprobadas y sin otrosíes ni actas abiertos.
- Lo vigente se guarda: `ContractLine.current_quantity` y `Contract.current_value`, que el
  servicio recalcula al guardar el borrador y al aprobar un otrosí. Una línea nueva de un otrosí
  es una `ContractLine` con `amendment` y cuenta solo con el otrosí aprobado.
- ⚑ Otrosí: aumentos positivos de cantidad, líneas nuevas y nueva fecha de fin; ni precios ni
  cargos. Uno abierto a la vez por contrato, con número consecutivo.
- ⚑ Anticipo: su valor se fija al aprobar el contrato (valor vigente × %) y un otrosí no lo
  cambia. Cada acta amortiza bruto × anticipo ÷ valor vigente (al aprobar el acta), sin pasar del
  saldo.
- ⚑ Bases de los descuentos: `direct` (bruto directo), `gross` (bruto) y `line:<n>`, la línea
  `n` de los cargos (ReteIVA sobre el IVA). `subtotal` no aplica a los descuentos (sería el
  bruto) y se rechaza. Porcentajes de los descuentos entre 0 y 100.
- ⚑ Plantillas: "AIU con IVA sobre utilidad" (A, I y U en 0 % para llenar; IVA 19 %), "IVA
  pleno" (19 % sobre el directo), "Sin cargos"; "Retenciones típicas" en 0 %: retención en la
  fuente y ReteICA sobre el directo, ReteIVA sobre la primera línea de cargos que empiece por
  "IVA" (o el bruto) y estampillas sobre el bruto. Un subcontrato nuevo arranca con AIU,
  retenciones y anticipo y garantía en 0 %; un destajo, sin cargos ni descuentos.
- ⚑ Cantidad propuesta: lo validado (revisión vigente aprobada) para el ítem y las tareas de la
  línea, capturado en Colombia hasta el fin del periodo, menos lo pagado en actas aprobadas de la
  línea, mínimo 0. Lo capturado dentro del periodo se muestra aparte. No se recorta al saldo: la
  aprobación exige el tope y el formulario avisa que hace falta un otrosí.
- ⚑ Una sola acta abierta (borrador o rechazada) por contrato. Su número se asigna al crearla
  (para el PDF); borrar el borrador lo libera. Cada acta lleva una línea por línea vigente.
- Periodo: desde ≤ hasta ≤ hoy, desde 2000, sin cruce con las actas no anuladas del contrato
  (en el servicio con el contrato bloqueado; no se usa `btree_gist`). Por defecto, desde el día
  siguiente al fin de la última acta no anulada (o el inicio del contrato) hasta hoy.
- ⚑ Acta firmada: PDF, JPG, PNG o WebP hasta 25 MB, en el almacén de adjuntos. Subirla no es
  editar (no cambia quién editó, así puede aprobar quien solo la escaneó) y queda como
  `certificate.updated`. Editar el borrador la quita.
- ⚑ Liquidación: amortiza todo el saldo del anticipo, no retiene y devuelve lo retenido; el neto
  puede ser negativo. Al aprobarla, el contrato queda liquidado; anularla (si es la última) lo
  vuelve a aprobado.
- Libro: un movimiento por línea con valor, tipo de documento "Acta de avance", número
  "SC-012 · Acta 3", descripción "Acta 3 · Contrato SC-012 · <ítem>", tercero el contratista,
  capítulo de origen del ítem en la versión vigente y la tarea si la línea cubre una sola.
  Registró y modificó: quien preparó y editó el acta; aprobó: quien aprobó; soporte: el acta
  firmada. Anular: reversos con fecha de hoy, el motivo y el mismo `certificate`. El reverso
  desde el libro rechaza los movimientos de acta.
- ⚑ Sin JavaScript (como la 6a): líneas, cargos y descuentos en filas con "Agregar filas"; ítem
  con buscador; tareas por números de esquema separados por comas (vacío: todas); el "cálculo en
  vivo" es un botón "Recalcular" que calcula en el servidor sin guardar; el periodo del acta se
  elige antes de las cantidades.
- Traslape: mismo ítem y tareas que se cruzan (vacío = todas), contra las líneas vigentes de
  contratos en borrador o aprobados y dentro del mismo contrato; se confirma con una casilla.
- Comprometido en contratos (Control, "Por ítem", con permiso de costos): aprobados, cantidad
  vigente × precio; liquidados, lo pagado × precio; directo, comparable con el BAC del ítem, y
  aviso si lo supera.
- Libro: origen Todos, Manual, Actas (`origen=actas`) y Solo automáticos. Un costo manual de
  Subcontrato o Mano de obra en un ítem con contrato aprobado avisa al guardarse y en su ficha.
- Avisos de la pestaña: actas en borrador creadas hace más de 15 días; validado sin pagar
  (contratos aprobados, hasta hoy); aprobados vencidos; anticipo por amortizar con lo ejecutado
  ≥ 100 % del vigente; traslapes.
- Ficha: Resumen, Líneas, Actas, Otrosíes e Historial (los eventos del contrato y los de sus
  otrosíes y actas, que llevan `metadata.contract`).
- Excel: `contratos.xlsx` (la lista) y `contrato-<número>.xlsx` (Contrato, Líneas, Actas,
  Otrosíes), con `contract.exported`; el PDF del acta, con `certificate.exported`. Ambos piden
  `export`.
- Números sugeridos `SC-001` y `DT-001`; únicos por proyecto sin distinguir mayúsculas. Hasta 200
  líneas, 20 cargos y 20 descuentos por contrato.
- Rendimiento: la lista son 2 consultas y la ficha 8 (más el catálogo). Prueba de carga: 200
  contratos, 2000 líneas, 5000 actas aprobadas con 50 000 líneas; lista y ficha en menos de 1 s
  (2 s con `CI=true`).
- `seed_control_demo`: por los servicios, el residente (Revisor de Costos) prepara y el
  administrador aprueba: SC-001 con un otrosí, dos actas, una anulada y una en borrador firmada;
  el destajo DT-001; SC-002 liquidado; SC-003 en borrador. Las dos actas manuales de movimiento
  de tierras de la 6a salen del ejemplo (las escribe ahora SC-001).
