# Costos, pieza 5: control con valor ganado

Fecha: 2026-11-11. Repo: `SUNO_server` (una PR). Sin cambios en la app ni en el contrato de
sincronización.

Antecedentes: cronograma con línea base (pieza 1), presupuestos contractual y vigente (pieza 3),
matriz ítem × tarea con cantidades planeadas (pieza 4a) y cantidades ejecutadas capturadas en
campo y validadas (pieza 4b).

## Contexto

Con lo que ya existe se puede medir, por proyecto, cuánto se había planeado ejecutar a una fecha
y cuánto se ejecutó de verdad, ambos en pesos del presupuesto vigente. Esta pieza agrega la
página "Control" del proyecto con el valor planeado (PV), el valor ganado (EV), la curva S, la
variación de cronograma (SV) y el índice de desempeño del cronograma (SPI).

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Costo real (AC) | No se mide en esta pieza: sin CPI ni EAC. Queda para una pieza posterior. |
| Periodos | Semanal y mensual; el usuario elige. Siempre con una fecha de corte (por defecto hoy) que se puede cambiar. |
| Reparto del PV | Según la **línea base** de MS Project, parejo por días laborables del calendario del proyecto. Sin línea base, las fechas actuales, con aviso. |
| Precios | Presupuesto **vigente** (precios y BAC). El total contractual se muestra solo como referencia. |
| Persistencia | Se calcula siempre al momento; no hay cortes cerrados ni tablas nuevas. Una validación tardía cambia los periodos pasados. |

## 1. Cálculo (`server/suno/control/earned_value.py`)

Servicio puro, sin acceso a la base, alimentado por un cargador con un número fijo de consultas
(plan de la matriz, precios del presupuesto vigente, cantidades de registros validados, tareas
con sus fechas, calendario del proyecto).

### 1.1 Valor planeado

- Por cada par tarea × ítem de la matriz: `cantidad planeada × precio unitario vigente`.
- Se reparte parejo entre los días laborables de `[baseline_start, baseline_finish]` de la tarea,
  con el calendario del proyecto (`projects/workdays.py`).
- Una tarea con cero días laborables (hito) carga todo su valor en `baseline_finish`.
- Sin línea base: se usan las fechas actuales de la tarea y la tarea va a los avisos.
- Un ítem que hoy es capítulo o no tiene precio vigente no suma y va a los avisos.

### 1.2 Valor ganado

- Cantidades de registros **validados** × precio unitario vigente, en la fecha del registro, hasta
  la fecha de corte inclusive. Los ajustes negativos restan.
- **Tope por par tarea × ítem**: el ganado acumulado del par no pasa de su valor planeado total.
  El exceso se informa aparte como "Sobreejecución" (cantidad y valor) y no entra al EV.
- Cantidades de ítems que no están en el plan de su tarea no entran al EV; se listan como
  "Ejecutado fuera del plan" (tarea, ítem, cantidad, valor).
- Tareas sin plan en la matriz (medidas por %) no tienen PV ni EV; se listan aparte con su último
  % validado.

### 1.3 Periodos

- Semana: lunes a domingo. Mes: calendario. Zona `America/Bogota` (`TIME_ZONE`).
- El último periodo termina en la fecha de corte.
- La serie va desde el primer inicio (de línea base, o actual si no hay) hasta el mayor entre la
  fecha de corte y el último fin del proyecto, para mostrar la curva planeada completa. El EV
  solo se calcula hasta la fecha de corte.

### 1.4 Indicadores

- `SV = EV − PV`; `SPI = EV / PV`, vacío si `PV = 0`.
- Estado: verde `SPI ≥ 0,95`; ámbar `0,85 ≤ SPI < 0,95`; rojo `SPI < 0,85`.
- BAC = suma del valor planeado total (todos los pares con precio vigente).

## 2. Página "Control" del proyecto

Ruta bajo el proyecto, junto a Cronograma, Presupuesto y Matriz; mismo permiso de lectura que la
Matriz. Sin permisos nuevos.

1. **Encabezado**: fecha de corte y selector Semana / Mes (por parámetros de la URL).
2. **Indicadores** a la fecha de corte: BAC vigente (con el total contractual como referencia),
   PV y EV con su % del BAC, SV y SPI con su color.
3. **Curva S**: PV y EV acumulados por periodo, en SVG generado en el servidor (como el gráfico de
   costos de equipos), más una tabla con los mismos números (accesibilidad y 390 px).
4. **Desglose** en pestañas: por capítulo del presupuesto, por tarea del cronograma y por
   ubicación (la de la tarea; las ubicaciones precisadas en campo no se usan en esta pieza). Cada
   fila: PV, EV, SV y SPI a la fecha de corte, ordenadas de peor a mejor SPI.
5. **Avisos**: tareas sin línea base, ítems sin precio vigente, sobreejecución, ejecutado fuera
   del plan, tareas medidas por %.
6. **Exportar a Excel** (`.xlsx`): hoja de periodos y hoja de desglose, con `safe_cell` contra
   inyección de fórmulas.

## 3. Rendimiento

- Número fijo de consultas, verificado con `assertNumQueries`.
- Reparto por días en memoria.
- Objetivo: menos de 1 s para un proyecto de ~5000 pares tarea × ítem, medido con una prueba de
  carga antes de integrar.

## 4. Pruebas

- Cálculo: reparto por días laborables, hitos, tareas sin línea base, tope y sobreejecución,
  ajustes negativos, ítems fuera del plan, ítems sin precio, periodos semanales y mensuales, corte
  a mitad de periodo, `SPI` con `PV = 0`.
- Vista: permisos, aislamiento por organización, fecha de corte, cambio de periodo, pestañas,
  proyecto sin matriz o sin presupuesto vigente (estado vacío con explicación).
- Excel: contenido y `safe_cell`.
- Revisión visual a 1440 px y 390 px.

## 5. Entrega

Una PR del servidor. Sin migraciones ni cambios de contrato. Se despliega al integrar; release
normal con release-please.

## Fuera de alcance

- Costo real, CPI, EAC y proyecciones.
- Cortes cerrados (fotos por periodo).
- Reparto por periodos de MS Project (*timephased*) o curvas no lineales.
- Valor ganado por ubicación precisada en campo.
- Control en la app móvil.

## Decisiones tomadas al planear

Precisiones del plan `2026-11-11-costos-pieza5-control-plan.md`; no cambian lo aprobado.

- URL: `corte` (por defecto hoy), `periodo` (`mes` por defecto o `semana`) y `desglose`
  (`capitulo` por defecto, `tarea`, `ubicacion`); un valor desconocido cae al de por defecto.
- El periodo que contiene el corte termina en él; si la serie sigue, el resto de ese periodo es
  una fila aparte "(tras el corte)" sin EV. La serie empieza en el menor entre el primer
  inicio, el corte y el primer registro contado. Tope de 1000 periodos.
- El EV de cada par se acota a [0, planeada]: un ajuste negativo resta, pero el par no baja de
  0. La sobreejecución es lo acumulado al corte menos lo planeado.
- Un hito, o una ventana sin días laborables, carga todo en su fin (como el % programado).
- Una tarea con plan y sin fechas (ni de línea base ni actuales) se avisa y no suma.
- Las tareas retiradas del cronograma no cuentan.
- Sin precio vigente: el ítem no es `item` en la versión vigente de su presupuesto; su
  ejecución tampoco entra.
- La fecha de un registro es su `captured_at` en hora de Colombia.
- Tareas medidas por %: hojas vigentes (ni resumen ni hito) sin filas en la matriz, con su
  último % validado.
- Desglose por capítulo de primer nivel y por la ubicación completa de la tarea, en filas
  planas; con varios presupuestos, ítems y capítulos llevan el nombre del presupuesto.
- Referencia contractual: suma del costo directo de la versión contractual de cada presupuesto
  (el BAC es costo directo).
- SPI a dos decimales; el color se decide sobre el valor redondeado. Desglose de peor a mejor
  SPI, sin SPI al final.
- Excel: pide `can_export_budgets` (los roles que leen presupuestos ya la tienen) y queda en la
  auditoría como `control.exported`.
- Datos de ejemplo para la revisión visual: `python manage.py seed_control_demo`.
