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
