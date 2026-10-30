# Módulo de costos y presupuesto — Etapa 1: Cronograma (diseño)

Fecha: 2026-10-30 · Estado: aprobado en brainstorming, pendiente de revisión escrita.

## Contexto

El módulo de gestión de costos y presupuesto se divide en cinco piezas, cada una con su
propio diseño, plan y PR:

1. **Cronograma** (este documento): proyecto, importación de MS Project, EDT, etapas,
   avance, atrasos, holguras y ruta crítica, responsables, avance desde campo.
2. Análisis y Gantt (las pantallas de análisis de este documento cubren buena parte; lo
   que quede se define después).
3. Presupuesto: capítulos e ítems, carga desde Excel.
4. Homologación (ítem × tarea → cantidad) y llaves contextualizadoras.
5. Control: programado vs ejecutado y valor ganado.

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Formato de entrada | MSPDI XML (Project → "Guardar como XML"). `.mpp` vía MPXJ queda para después; el lector se aísla para poder añadir otro. |
| Identidad de tareas entre versiones | `UniqueID` de Project. |
| Versiones | **Sin fotografía por tarea.** Cada importación actualiza las tareas en sitio y deja un registro (`ScheduleImport`) con el XML original y un resumen de cambios. |
| Ejecutores | Recursos del Project, como datos (no son usuarios). |
| Responsables | Usuarios SUNO asignados en SUNO a una tarea o rama de la EDT; sobreviven a las importaciones. |
| Avance | Dos columnas por tarea: **% SUNO** (reportado en campo y aprobado) y **% Project** (del XML). Sin selector de fuente: los indicadores se calculan con ambas; si falta el dato, vale 0. |
| Ruta crítica y holguras | Las da el Project (`Critical`, `TotalSlack`, `FreeSlack`). **Sin motor CPM propio.** |
| Línea base | La del Project (Baseline 0). SUNO avisa si una importación la cambia. |
| Días de atraso | Días hábiles según el calendario del Project. |
| Etapas | Tareas resumen de nivel 1 de la EDT. |
| Avance desde campo | La visita se vincula opcionalmente a una tarea y lleva un % acumulado; cuenta cuando la visita está validada. |
| Roles | Los de la organización (ADR-011), sin equipos por proyecto. |
| Interfaz | Páginas propias en `/plataforma/proyectos/` (plantillas Django), no el admin. |

## 1. Modelo de datos

App nueva `visitacampo/projects`. Todos los modelos heredan de `OrgScopedModel`.

- **Project**: `name`, `code`, `description`, `calendar` (JSON: días laborales de la
  semana y excepciones; se reemplaza en cada importación), `status_date` (la `StatusDate`
  de la última importación, opcional), marcas de creación.
- **ScheduleImport**: `project`, `number` (consecutivo por proyecto, único),
  `source_file` (XML original), `uploaded_by`, `uploaded_at`, `status_date`, `summary`
  (JSON: `added`, `retired`, `reactivated`, `modified`, `baseline_changed`, advertencias).
- **Task**: `project`, `uid` (UniqueID; único por proyecto), `wbs`, `outline_level`,
  `parent` (FK a sí misma), `name`, `is_summary`, `is_milestone`, `start`, `finish`,
  `actual_finish`, `duration_days`, `baseline_start`, `baseline_finish`,
  `project_pct`, `project_physical_pct`, `total_slack_days`, `free_slack_days`,
  `is_critical`, `stage` (FK a la tarea de nivel 1 que la contiene; en las de nivel 1,
  ella misma), `retired_in` (FK a `ScheduleImport`, nula si está vigente).
  Los campos del XML se sobrescriben en cada importación.
- **TaskDependency**: `predecessor`, `successor`, `type` (FS, SS, FF, SF), `lag_days`.
  Se reemplazan en cada importación.
- **Resource**: `project`, `uid`, `name`. **TaskResource**: `task`, `resource`. Se
  reemplazan en cada importación.
- **TaskResponsible**: `task`, `user` (AppUser de la misma organización), `assigned_by`,
  `assigned_at`. Única por (`task`, `user`). Asignar en una tarea resumen cubre toda su
  rama.
- **Visit** (existe): dos campos opcionales nuevos, `task` (FK a `Task`) y
  `progress_pct` (decimal 0–100). Cambian el contrato de sincronización (`VisitRecord`),
  así que se versionan con el resto del contrato.

**% SUNO de una tarea hoja**: `progress_pct` de la visita **validada** más reciente
vinculada a la tarea (la vigente de `VisitReview` es `approved`). Sin dato → 0.

**Tareas retiradas**: no se borran. Conservan responsables y visitas, dejan de mostrarse
en el cronograma vigente y se reactivan si el `UniqueID` vuelve a aparecer.

## 2. Importación

1. El usuario sube el XML en `/plataforma/proyectos/<id>/importar/`.
2. Un trabajo de la cola existente (`jobs`) lo lee y calcula la vista previa: nuevas,
   retiradas, reactivadas, modificadas, cambio de línea base y advertencias (tareas
   retiradas con responsables o con avance de campo).
3. El usuario confirma y se aplica en **una sola transacción**: actualizar o crear tareas,
   marcar retiradas, recalcular `stage` y `parent`, reemplazar dependencias, recursos y
   calendario, crear el `ScheduleImport`.

**Lector** (`projects/mspdi.py`): usa `defusedxml`, devuelve estructuras Python simples
(tareas, dependencias, recursos, asignaciones, calendario, `StatusDate`) y no toca la base
de datos. Un lector `.mpp` futuro devolverá las mismas estructuras.

**Errores y límites**

| Caso | Comportamiento |
|---|---|
| No es MSPDI válido, o faltan `UniqueID` o nombre en alguna tarea | Se rechaza con mensaje claro. |
| Archivo > 50 MB | Se rechaza. |
| Sin línea base | Se importa, con aviso "sin línea base: no se calcularán atrasos". |
| Tareas sin fechas | Se importan y se marcan. |
| Sin calendario | Se usa lunes a viernes sin festivos. |
| Dos importaciones simultáneas del mismo proyecto | Bloqueo por proyecto (`select_for_update`); la segunda se rechaza. |

## 3. Cálculos de atraso

Módulo puro `projects/progress.py`: recibe tareas, calendario, fecha de corte y avances;
devuelve indicadores. No se guardan; se calculan al consultar.

- **Calendario**: `working_days(desde, hasta)` según días laborales y excepciones.
- **Fecha de corte**: `status_date` del proyecto; si no hay, hoy. El usuario puede
  elegir otra en pantalla.
- **% programado** = días hábiles desde el inicio de la línea base hasta la fecha de
  corte ÷ duración de la línea base en días hábiles, acotado a [0, 100]. Hitos (duración
  0): 0 antes de su fecha, 100 desde ella.
- **Atraso %** = % programado − % real. Se calcula dos veces: con % SUNO y con % Project.
  Positivo = atrasada.
- **Fin proyectado**: si la tarea está al 100 %, su fin real (`actual_finish`, o `finish`
  si no viene); si no, `finish` del Project, y como mínimo la fecha de corte cuando esta
  ya pasó el fin de la línea base.
- **Atraso en días** = días hábiles entre el fin de la línea base y el fin proyectado. Se
  calcula con ambas fuentes porque el 100 % puede venir de cualquiera.
- **Resúmenes y etapas**: % programado y % real se consolidan desde las hijas,
  ponderados por duración de la línea base; atraso % con esos consolidados; atraso en
  días = el mayor de sus hijas. El proyecto se consolida desde sus etapas.
- **Tareas sin línea base**: indicadores "—" y fuera de los consolidados.

## 4. Pantallas (`/plataforma/proyectos/`)

Plantillas Django con el login de `/plataforma/`, filtradas por organización y rol.

- **Lista de proyectos**: nombre, fecha de corte, % programado, % SUNO, % Project, atraso
  en días, número de la importación vigente.
- **Cronograma**: tabla jerárquica por EDT, plegable, con las etapas resaltadas.
  Columnas: EDT, nombre, ejecutores, responsables, inicio, fin, inicio y fin de línea
  base, % programado, % SUNO, % Project, atraso % (SUNO / Project), atraso en días
  (SUNO / Project), holgura total, holgura libre, crítica. Filtros: etapa, responsable,
  solo críticas, solo atrasadas. Selector de fecha de corte. Exportación a Excel
  reusando `backoffice/export.py`.
- **Gantt**: barras programadas y de línea base, dependencias, ruta crítica en rojo,
  línea de fecha de corte. Frappe Gantt desde CDN con versión fija.
- **Tarea**: datos, visitas vinculadas con su % y estado de revisión, asignación de
  responsables.
- **Importar**: subida, vista previa y confirmación.
- **Historial**: importaciones con su resumen y descarga del XML original.

**Permisos**

| Rol | Permisos |
|---|---|
| OWNER, ADMIN | Todo: crear proyectos, importar, asignar responsables. |
| SUPERVISOR | Ver y asignar responsables; valida avances al revisar visitas (admin, como hoy). |
| FIELD_AGENT | Ver solo los proyectos donde es responsable de alguna tarea. |
| VIEWER | Ver. |

## 5. App móvil y API

- `GET /v1/projects/tasks/`: tareas vigentes donde el usuario es responsable (directo o
  por rama), con EDT, nombre, proyecto y último % validado. Sincronización incremental con
  `change_seq`, como capas y formularios.
- El push de visitas acepta `task_id` y `progress_pct`. El servidor valida organización,
  que el usuario sea responsable de la tarea y el rango 0–100. Un % menor que el último
  validado se acepta y se marca para el supervisor.
- Flutter: tabla Drift `tasks`; en el formulario de visita, selector opcional "Tarea del
  cronograma" con búsqueda por EDT o nombre y campo "% avance acumulado" con el último
  validado como referencia. Funciona sin conexión y sube con el outbox.
- Validar la visita valida su %; rechazarla lo deja fuera.

## Pruebas

| Área | Cobertura |
|---|---|
| Lector MSPDI | XML de ejemplo exportados de Project: pequeño, con calendario y festivos, sin línea base, malformado. |
| Cálculos | Días hábiles, % programado, atrasos, fin proyectado, consolidación, hitos, sin línea base. |
| Importación | v1 → v2 con tareas nuevas, retiradas y reactivadas; responsables y visitas sobreviven; rechazo de concurrentes. |
| Permisos | Cada pantalla y endpoint por rol y por organización. |
| App | Pruebas de widget del selector y de la sincronización; un paso nuevo en el E2E: visita vinculada a una tarea llega con su %. |

## Fuera de alcance de esta etapa

- Lectura de `.mpp` (MPXJ).
- Motor CPM propio.
- Fotografía de tareas por versión.
- Reporte de cantidades ejecutadas desde campo (llega con presupuesto y homologación).

## Extensiones previstas

El modelo se deja listo para la pieza 4 sin rehacerlo:

- **Matriz de homologación**: filas (ítem de presupuesto, `Task`, cantidad). Cuelgan de
  `Task`, que es estable por `UniqueID` y nunca se borra, así que sobreviven a las
  importaciones.
- **Llaves contextualizadoras**: `ContextKey` (llaves definidas por proyecto, p. ej.
  "Ruta", "PR", "Módulo", "Piso") y `TaskKeyValue` (0..n valores por tarea).
- La vista previa de importación avisará también de tareas retiradas con homologación o
  llaves.
- La consolidación del % SUNO podrá ponderar por costo cuando exista el presupuesto.
