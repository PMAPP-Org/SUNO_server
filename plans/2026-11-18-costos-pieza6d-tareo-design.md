# Costos, pieza 6d: mano de obra por jornal (tareo) y conciliación de nómina

Fecha: 2026-11-18. Repo: `SUNO_server` (una PR). La app no cambia en esta pieza: la captura del tareo
en la app móvil será una pieza posterior.

Antecedentes de esta pieza:

- piezas 3 a 5: presupuesto (3), matriz ítem × tarea (4a), cantidades validadas en campo (4b) y control
  con valor ganado (5);
- pieza 6a: libro de costos con CPI, EAC y TCPI;
- pieza 6b: subcontratos y destajos con actas;
- pieza 6c: materiales con bodegas, kárdex y cortes de consumo (v0.20.0).

La pieza 6 termina con esta (6d).

## Contexto

Hoy la mano de obra por jornal solo entra al libro como un costo genérico de tipo "Mano de obra" o
"Nómina". Esta pieza agrega:

- un registro de trabajadores de la organización con su jornal básico y un factor prestacional, ambos
  con vigencia;
- planillas de tareo por proyecto y periodo, que al aprobarse publican el costo estimado en el libro;
- un reporte que concilia lo tareado con la nómina real importada desde Excel.

SUNO **no liquida nómina**: devengados, deducciones, seguridad social, PILA y nómina electrónica siguen
en el software de nómina de la organización.

## Decisiones tomadas

| # | Tema | Decisión |
|---|---|---|
| 1 | Alcance | Tareo y costo estimado (jornal × factor prestacional); sin liquidación legal de nómina. |
| 2 | Captura | Planilla web semanal por proyecto e importación desde Excel. La app, después. |
| 3 | Unidad | Tareo **nominal**: por trabajador con datos personales mínimos y acceso restringido. |
| 4 | Costo | Fracción de jornal × jornal básico vigente × factor prestacional vigente. Las horas extra se registran como fracción adicional de jornal. |
| 5 | Libro | Planilla por periodo con aprobación; al aprobar, un costo de Mano de obra por destino. |
| 6 | Nómina real | Solo reporte de conciliación: no ajusta el libro. |
| 7 | Permisos | Sección nueva **Mano de obra** (`LABOR`) en la matriz fija, separada de COSTS. |
| 8 | Arquitectura | App nueva `labor` con modelos propios, como `contracts` y `materials`. |

## 1. Modelo de datos

Todo en la app `labor`, migración `0031` (aditiva).

### 1.1 Organización

- **`Worker`** (trabajador).
  - `document_type` (CC, CE, PPT, PEP, pasaporte) y `document_number`: únicos por organización.
  - `full_name`, `position` (cargo, texto libre; la pantalla sugiere los cargos ya usados).
  - `hired_on`, `retired_on` (opcional; ≥ `hired_on`), `is_active`.
  - No se guardan dirección, teléfono, correo, cuenta bancaria, EPS ni fondo de pensión.
- **`WorkerWage`** (jornal básico).
  - `worker`, `valid_from`, `daily_wage` (> 0, a centavos).
  - Único por (`worker`, `valid_from`). Un cambio de jornal es una fila nueva; las filas no se editan
    si alguna planilla aprobada ya la usó.
  - El jornal vigente un día es el de mayor `valid_from` ≤ ese día.
- **`BenefitFactor`** (factor prestacional de la organización).
  - `valid_from` (único por organización), `factor` con 4 decimales, check 1 ≤ `factor` ≤ 3.
  - Cubre prestaciones sociales, seguridad social, parafiscales y dotación. Una nota libre explica su
    composición.

### 1.2 Por proyecto

- **`ProjectWorker`** (asignación).
  - `project`, `worker`, `starts_on`, `ends_on` (opcional; ≥ `starts_on`).
  - Un trabajador puede estar asignado a varios proyectos a la vez. No puede tener dos asignaciones
    que se cruzan en el mismo proyecto (servicio, con la fila del proyecto bloqueada).
- **`Timesheet`** (planilla).
  - Número correlativo por proyecto `TA-001`.
  - `starts_on` ≤ `ends_on`, máximo 31 días (check), nunca en el futuro.
  - Estado: `draft` → `approved` → `reversed`; `rejected` (con motivo) vuelve a `draft` al editarse.
  - Soporte firmado (adjunto), `created_by`, `last_edited_by`, `approved_by`, `approved_at`.
  - Resumen guardado (huella) para la regla de "lo aprobado es lo firmado" (§2.3).
  - Las planillas no anuladas de un proyecto no se cruzan en fechas (servicio, con la fila del
    proyecto bloqueada).
- **`TimesheetLine`** (línea).
  - `timesheet`, `worker`, `worked_on`, destino y `fraction`.
  - Destino como en 6a y 6c: `scope` (ítem, capítulo, selección o indirecto), ítems, capítulo y tarea
    opcional, validado con `check_assignment` contra la versión vigente del presupuesto.
  - `fraction`: 4 decimales, check 0 < `fraction` ≤ 1,5.
  - Al aprobar se congelan `daily_wage`, `factor` y `amount` (la línea redondeada a centavos). Check:
    en una línea aprobada los tres son > 0; en un borrador son nulos.
  - Única por (`timesheet`, `worker`, `worked_on`, destino).
  - `worked_on` dentro del periodo de la planilla y dentro de una asignación del trabajador al proyecto
    (servicio).

### 1.3 Conciliación

- **`PayrollImport`**: periodo (`starts_on`, `ends_on`), archivo original, quién lo importó y cuándo.
  Importación en dos pasos con vista previa firmada por un nonce (ADR-0036).
- **`PayrollImportLine`**: `document_type`, `document_number`, `worker` (si coincide), `total_paid`
  (> 0): el costo total del trabajador en el periodo según la nómina (devengado, aportes y
  provisiones).
- No toca el libro.

### 1.4 Libro de costos

- `CostEntry` gana la FK de origen `timesheet` (nullable) y entra en la restricción
  `cost_entry_single_origin` (ADR-0031).
- Tipo de costo: Mano de obra. Tipo de documento: nuevo "Planilla de tareo".
- El libro no reversa costos con este origen: se anula la planilla.
- El filtro y la etiqueta de origen del libro suman "Tareo".

## 2. Cálculo y flujos

### 2.1 Costo

- Costo de una línea = `fraction` × jornal vigente el día × factor vigente el día, redondeado a
  centavos (ROUND_HALF_UP).
- El costo de cada destino es la suma de sus líneas ya redondeadas, así que la planilla y el libro
  cuadran al peso.
- Si un día no hay jornal o factor vigente, la línea muestra el error y la planilla no se puede
  aprobar.
- En Control, la mano de obra entra al costo real (AC) y al CPI directo del ítem; lo indirecto, al CPI
  de administración, como en 6a.

### 2.2 Tope diario

- Un trabajador no suma más de 1,5 jornales en un mismo día **entre todas las planillas no anuladas de
  la organización**.
- Se valida al guardar y otra vez al aprobar. Bloqueos: fila del proyecto (`FOR NO KEY UPDATE`), la
  planilla y luego las filas de los trabajadores de la planilla en orden de id (ADR-0035). Así dos
  aprobaciones simultáneas en obras distintas no dejan a nadie por encima del tope.
- El error dice quién, qué día, cuánto suma y en qué otra planilla (número y proyecto) está el resto.

### 2.3 Aprobación

- Requiere `approve` de Mano de obra y el soporte firmado.
- Sin autoaprobación, como en 6a a 6c: no aprueba quien creó la planilla ni quien la editó por última
  vez (servicio y checks en la base de datos).
- **Lo aprobado es lo firmado.** El borrador guarda un resumen al guardarse (número de líneas, jornales
  y costo estimado por destino) y el PDF lo imprime. Al aprobar se recalcula; si cambió (por ejemplo,
  porque cambió un jornal o el factor), se rechaza con "La planilla cambió desde que se imprimió;
  revísala y vuelve a cargar el soporte firmado".
- Al aprobar: congela jornal, factor y valor de cada línea y crea un `CostEntry` aprobado por destino
  con valor, con fecha del fin del periodo y descripción "Tareo TA-001 · 3.2 Muros".
- Rechazar exige un motivo. Editar quita el soporte firmado. Cargar el soporte no cuenta como edición.

### 2.4 Anulación

- Requiere `reverse` de Mano de obra. Aplica a cualquier planilla aprobada.
- Escribe los reversos con fecha de hoy y deja la planilla en `reversed`. Para corregir se hace una
  planilla nueva con el mismo periodo.

### 2.5 Importación del tareo

- Excel con documento, fecha, destino (código del ítem, del capítulo o "indirecto"), tarea opcional y
  fracción.
- Dos pasos con vista previa firmada por nonce, como el catálogo de materiales de 6c. Cada fila con
  error dice cuál y por qué; no se aplica nada si hay errores.
- Agrega líneas a un borrador; una línea igual (trabajador, día, destino) se suma.

### 2.6 Conciliación

- Por trabajador del periodo importado:
  - tareado: jornales y costo estimado en todas las planillas aprobadas del periodo;
  - pagado: `total_paid`;
  - diferencia y porcentaje.
- Marca a los trabajadores pagados sin tareo y a los tareados sin pago. Las líneas de la nómina cuyo
  documento no está en el registro se listan aparte.
- Filtro por proyecto (prorratea lo pagado por la proporción de jornales tareados en ese proyecto) y
  exporte a Excel.

## 3. Pantallas, documentos y permisos

### 3.1 Trabajadores (organización)

- Lista con filtros por cargo, estado y texto; ficha con el historial de jornales y las asignaciones.
- Alta por formulario o importación desde Excel (documento, nombre, cargo, ingreso, jornal) en dos
  pasos con vista previa.
- El factor prestacional se gestiona en la misma pantalla, con su historial.
- Retirar a alguien lo inactiva. Solo se borra un trabajador sin tareo.

### 3.2 Pestaña "Tareo" del proyecto

- **Asignaciones:** quién trabaja en el proyecto y entre qué fechas.
- **Planillas:** lista con número, periodo, estado, jornales y costo.
- **Crear:** fechas (por defecto la semana anterior, lunes a domingo); trae a los trabajadores
  asignados en el periodo.
- **Cuadrícula semanal:** una fila por trabajador y destino, una columna por día y la fracción en cada
  celda.
  - "Repetir el día anterior" y "partir la fila" para repartir el día entre destinos.
  - Totales por día, por persona y por destino, con el costo estimado.
  - A 390 px pasa a una vista por día: se elige el día y se ve la lista de personas.
  - Funciona sin JavaScript obligatorio, como el resto de la plataforma (ADR-0020); el JavaScript solo
    agrega comodidades.

### 3.3 Conciliación (organización)

- Importar la nómina del periodo, ver la tabla de §2.6 y exportarla.

### 3.4 Documentos

- PDF de la planilla para firmar (maestro y residente): encabezado del proyecto, cuadrícula, totales y
  el resumen que se compara al aprobar. Texto del usuario siempre escapado.
- Excel de la planilla y de la conciliación.

### 3.5 Permisos

Sección nueva **LABOR** ("Mano de obra") en la matriz fija de `core/access.py`:

| Acción | Capacidad |
|---|---|
| Ver trabajadores, planillas y conciliación | `read` |
| Registrar asignaciones y planillas | `create` / `update` |
| Borrar planillas en borrador | `delete` |
| Aprobar | `approve` |
| Rechazar | `reject` |
| Anular | `reverse` |
| Exportar (PDF, Excel y la planilla firmada escaneada; revisado con el usuario el 2026-11-19) | `export` |

- El registro de trabajadores, sus jornales, el factor y la conciliación requieren `create` y `update`
  de LABOR con alcance de **toda la organización**.
- En el libro y en Control (sección COSTS) se ven los costos de Mano de obra por destino, sin nombres
  ni salarios. El enlace al origen solo abre la planilla a quien tiene LABOR `read`.

### 3.6 Datos personales (Ley 1581)

- Solo datos mínimos (§1.1).
- El documento se muestra completo en la ficha y enmascarado (`***4567`) en listas, planillas, PDF y
  exportes.
- Auditoría de altas, cambios de jornal, importaciones, exportes y consultas de la conciliación.
- Se actualizan `docs/seguridad/tratamiento-datos-personales.md`, `docs/seguridad/inventario-activos.md`
  y `docs/seguridad/retencion.md`: el tareo aprobado se conserva según la ley laboral; un trabajador
  sin tareo se puede borrar.

## 4. Pruebas, rendimiento y entrega

- **Cálculo:** redondeo por línea y suma por destino; vigencias de jornal y factor en el día exacto del
  cambio; falta de jornal o factor.
- **Tope diario:** entre planillas y proyectos; dos aprobaciones concurrentes con hilos y
  `TransactionTestCase`.
- **Aprobación:** lo aprobado es lo firmado (cambio de jornal o factor después del PDF); sin
  autoaprobación; rechazo y reedición; anulación con reversos; valores congelados.
- **Restricciones de la base de datos:** fracción, factor, periodo de 31 días, unicidades, origen único.
- **Importaciones:** tareo, trabajadores y nómina, con un Excel adversarial (fórmulas, fechas raras,
  documentos repetidos, filas vacías).
- **Aislamiento y permisos:** 404 con ids de otra organización o proyecto; matriz por acción; sin
  salarios ni nombres en COSTS; documento enmascarado.
- **Carga:** 60 trabajadores × 31 días con 3 destinos; la cuadrícula, el PDF y la conciliación de 500
  trabajadores responden en menos de 1 s, con un número fijo de consultas.
- **Demo:** `seed_control_demo` agrega trabajadores, factor, asignaciones, una planilla aprobada y una
  nómina importada.
- **Revisión visual:** a 1440 y 390 px, sin texto cortado ni desbordes.
- **Verificaciones de siempre:** ruff, mypy, `makemigrations --check`, guardián de nombres, migración que
  baja a la 0030 y vuelve a subir.
- **Cierre (regla de documentación):**
  - ADR "Tareo nominal con costo estimado; la nómina solo se concilia" y ADR "Sección Mano de obra
    separada de COSTS";
  - `docs/arquitectura/` al día (componentes, modelo de datos de costos);
  - issues `known-issue` de las limitaciones de §5.

## 5. Fuera de alcance

- Captura del tareo en la app móvil.
- Liquidación de nómina, horas extra y recargos por tipo, novedades (incapacidades, vacaciones,
  licencias) y nómina electrónica.
- Ajustar el libro con la nómina real.
- Datos de afiliación (EPS, pensión, ARL) y documentos del trabajador.

## Decisiones tomadas al planear

Revisadas y aceptadas por el usuario el 2026-11-18. El detalle está en la sección del mismo nombre de
`2026-11-18-costos-pieza6d-tareo-plan.md`; aquí el resumen (⚑ = las que el usuario revisó).

1. Una migración `0031_labor` (aditiva). ⚑ Su `RunPython` da **Mano de obra / Gestor / toda la
   organización** a las membresías `OWNER` y `ADMIN` existentes (como la `0028` con Costos);
   `LEGACY_GRANTS` hace lo mismo con las nuevas.
2. ⚑ Matriz de `LABOR`: Gestor todo; Revisor sin anular; Colaborador crear, leer, editar, borrar y
   exportar (solo lo suyo); **Consulta solo `read`** (sin exportar). Sin `import`.
3. ⚑ El registro (trabajadores, jornales, factor y conciliación) pide Mano de obra con alcance de
   **toda la organización**: `read` para ver; `create` y `update` para gestionar; `export` para el
   Excel de la conciliación.
4. Políticas del proyecto `can_*_labor` con su `check_*`; 404 a quien no lee Mano de obra en el
   proyecto, 403 a quien la lee sin la capacidad.
5. Paquete `server/suno/labor/` sin `apps.py`; modelos en `core/models/labor.py`; reutiliza el
   destino escrito y las piezas de bloqueo y reverso de la 6c.
6. ⚑ Trabajador: `is_active` y `retired_on` van juntos; retirar cierra las asignaciones;
   "reincorporar" corrige un retiro; documento guardado limpio; tipos CC, CE, PPT, PEP y PA.
7. ⚑ Un jornal o un factor usado por una planilla aprobada o anulada no se edita ni se borra; uno
   nuevo no puede regir sobre días ya congelados.
8. ⚑ Importación de trabajadores: un documento nuevo crea; uno existente actualiza nombre y cargo y,
   si el jornal cambió, agrega un jornal desde "Jornal desde" (sin ella, desde hoy).
9. Asignaciones dentro del vínculo y sin cruces en el mismo proyecto; editar o borrar no deja fuera
   días ya tareados.
10. ⚑ Destino de una fila como texto de la 6c y tarea por su número de esquema; unicidad con
    `destination_key` (SHA-256 del destino).
11. ⚑ Cuadrícula semanal que reemplaza las líneas de esa semana; vista por día que guarda solo ese
    día; `DATA_UPLOAD_MAX_NUMBER_FIELDS` = 4.000.
12. ⚑ "Repetir el día anterior" y "Partir la fila" son botones del formulario que vuelven a pintar
    sin guardar; el JavaScript solo da totales en vivo.
13. Estados borrador, aprobada, rechazada y anulada; la decisión en `decided_by`/`decided_at`.
14. ⚑ Huella: líneas, jornales, total, por destino y por trabajador, y si el costo está completo;
    "Recalcular el resumen" es una edición (quita el soporte firmado).
    **Revisado con el usuario el 2026-11-19:** la planilla tiene una versión que sube en cada
    edición; el PDF imprime un código corto (versión y huella), el soporte firmado se carga para
    la versión vista (si cambió, se rechaza) y la ficha muestra el código junto a "Aprobar". La
    versión es también el control de concurrencia de la cuadrícula (ADR-0032).
15. ⚑ Valores congelados: la base exige "los tres nulos o los tres > 0"; que el borrador no los
    tenga lo garantiza el servicio. Una línea en $0 no se aprueba.
16. Tope diario y orden de bloqueo: proyecto, planilla, trabajadores en orden de `pk` y, al aprobar,
    las filas del factor. Cuentan borradores, rechazadas y aprobadas de toda la organización.
17. Al aprobar, un `CostEntry` aprobado de Mano de obra por destino, con fecha del fin del periodo y
    la planilla firmada como soporte; sin nombres en el libro. **Revisado con el usuario el
    2026-11-19:** ese soporte solo lo descarga quien lee Mano de obra en el proyecto (para los demás,
    404 y sin enlace en el detalle ni en el exporte del libro; ADR-0040).
18. Anular cualquier planilla aprobada, con motivo y reversos de hoy.
19. ⚑ La bandeja "Por aprobar" de Costos no muestra planillas; la pestaña Tareo dice cuántas hay.
20. Libro: filtro y etiqueta "Tareo"; el enlace a la planilla solo para quien lee Mano de obra; un
    movimiento de tareo no se reversa desde el libro.
21. ⚑ De la nómina importada se guardan nombre, tamaño y SHA-256 del Excel, no el archivo.
22. ⚑ Conciliación por proyecto: lo pagado se prorratea por jornales; quien fue pagado sin tareo en
    ningún proyecto solo aparece en la vista de la organización.
23. Un solo token de importación (`LaborImportToken`) para las tres importaciones.
24. ⚑ PDF A4 horizontal, en bloques de hasta 16 días, con documento enmascarado, firmas y el resumen
    que se compara al aprobar.
25. ⚑ Auditoría sin nombres (ids, documento enmascarado y cantidades); cada consulta de la
    conciliación se audita.
26. Límites de longitud y de filas de las importaciones; listas paginadas de a 100.
27. "Trabajadores" en la barra lateral para quien lee el registro; pestaña "Tareo" para quien lee
    Mano de obra en el proyecto.
28. Nombres de archivo de los PDF, Excel y plantillas.
29. ⚑ Prueba de concurrencia del tope: dos guardados en obras distintas y un guardado contra una
    aprobación (los borradores ya cuentan para el tope).
30. Fechas de las pruebas fijas (hoy 2026-04-15; semana del 2 al 8 de marzo de 2026).
