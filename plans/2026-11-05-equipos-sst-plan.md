# Equipos, preoperacionales y soporte SST — plan maestro

**Estado:** Hito A implementado; Hito B en curso con EQ-01 a EQ-05 terminados; MNT-01 iniciado con registro y listado de uso en la rama `feat/section-access-rbac`.

**Objetivo:** incorporar a SUNO un módulo de equipos que cubra ficha técnica, documentos, operadores, uso, mantenimiento y preoperacionales móviles revisables, con trazabilidad y salidas para entregables SST. Antes se implementará autorización por sección y proyecto.

**Arquitectura aprobada:** RBAC híbrido. `Membership` conserva la pertenencia y el rol organizacional; `SectionGrant` asigna un rol funcional por sección y alcance de proyecto. Las capacidades son cerradas y versionadas en código. Servidor, web, Admin y cliente móvil aplican la misma fuente de verdad.

**Repositorios afectados:**

- `SUNO_server`: modelos, políticas, API, sincronización, revisión web, exportaciones, auditoría y jobs.
- `SUNO_client`: navegación autorizada, catálogo offline de equipos, captura y sincronización de preoperacionales.

El presente archivo es la fuente de verdad del plan global. Al iniciar el cliente se deberá crear un plan táctico equivalente en `SUNO_client`.

## 1. Alcance del MVP

### Autorización

- Un rol activo por usuario y sección.
- Alcance organizacional o por proyecto.
- Matriz usuario × sección × rol.
- Matriz de capacidades efectivas, incluyendo CRUD y acciones especiales.
- Navegación web y móvil derivada de las secciones disponibles.
- Auditoría de concesión, cambio y revocación.
- Migración sin pérdida de los permisos actuales.

### Equipos

- Catálogo e identificación única.
- Ficha técnica y estado operativo.
- Documentos y vigencias.
- Asociación con proyectos.
- Historial de operadores.
- Código QR, además de búsqueda manual, para identificar el equipo.

### Preoperacionales

- Formulario versionado por tipo de equipo.
- Captura móvil offline con equipo, operador, proyecto, fecha, ubicación y evidencias.
- Presentación inmutable y sincronización idempotente.
- Cola de revisión: aprobar, rechazar o solicitar corrección.
- Historial de decisiones, comentarios y novedades.

### Uso y mantenimiento

- Programación de uso por equipo, proyecto, operador y periodo.
- Detección de solapamientos e indisponibilidad.
- Lecturas de horómetro o kilometraje.
- Planes y órdenes de mantenimiento preventivo y correctivo.
- Historial de ejecución y soportes.
- Alertas por mantenimiento o documentos próximos a vencer.

### SST

- Expediente consultable por equipo.
- Consolidado por proyecto y periodo.
- Exportaciones de ficha, documentos, operadores, preoperacionales, novedades, uso y mantenimiento.
- Evidencias y decisiones de revisión trazables.

## 2. Fuera del MVP

- Telemetría automática desde fabricantes o dispositivos IoT.
- Inventario contable, depreciación o integración ERP.
- Compra de repuestos y gestión completa de almacén.
- Edición arbitraria de permisos individuales fuera de los perfiles aprobados.
- Sustitución total del Django Admin.
- `keypass` hasta definir si significa passkey, PIN, activación u otro mecanismo.

## 3. Modelo de autorización

### Secciones iniciales

`FIELD_RECORDS`, `PROJECTS`, `BUDGETS`, `FORMS`, `EQUIPMENT`, `GIS`, `REPORTS`, `USERS_ACCESS` y `OPERATIONS`.

### Capacidades

`create`, `read`, `update`, `delete`, `review`, `approve`, `reject`, `assign`, `import`, `publish`, `export`, `schedule` y `manage_access`.

### Modelos conceptuales

- `SectionGrant`: usuario, organización, sección, rol, estado y auditoría de concesión.
- `SectionProjectScope`: proyectos incluidos en el alcance de una asignación.
- Registro de auditoría para cada modificación.

Una asignación sin proyectos asociados tiene alcance organizacional únicamente cuando el perfil lo permita expresamente. No se interpretará un alcance vacío como acceso total de forma implícita.

### Compatibilidad

La migración inicial crea asignaciones equivalentes a las políticas actuales:

- `OWNER`/`ADMIN`: gestión de secciones existentes y administración de accesos.
- `SUPERVISOR`: revisión de campo, supervisión de proyectos y lectura/exportación de presupuestos.
- `FIELD_AGENT`: contribución de campo y proyectos asignados.
- `VIEWER`: consulta de proyectos, presupuestos, GIS y registros autorizados.

## 4. Modelo de equipos

Los nombres son conceptuales; cada plan táctico fijará campos, índices y constraints antes de implementar.

| Entidad | Responsabilidad |
|---|---|
| `EquipmentType` | categoría, formulario preoperacional vigente y documentos requeridos |
| `Equipment` | código, identificación, fabricante, modelo, serie/placa, propiedad, estado y especificaciones |
| `EquipmentDocument` | tipo, número, emisión, vencimiento, archivo y estado de validez |
| `EquipmentProject` | vinculación histórica del equipo con un proyecto |
| `EquipmentOperatorAssignment` | operador, equipo, proyecto, vigencia y responsable de asignación |
| `EquipmentUsageBooking` | reserva de uso, periodo, proyecto, actividad y operador |
| `EquipmentMeterReading` | horómetro/kilometraje, fecha, origen y evidencia |
| `MaintenancePlan` | frecuencia por tiempo o medidor, tolerancia y próxima intervención |
| `MaintenanceOrder` | mantenimiento programado/correctivo, estado, ejecución, proveedor y soportes |
| `PreoperationalInspection` | evidencia móvil presentada y versionada |
| `PreoperationalAttachment` | fotos, documentos y firmas asociadas |
| `PreoperationalReview` | historial inmutable de decisiones y comentarios |
| `EquipmentIssue` | novedad, severidad, responsable, estado y cierre |

`Project.Resource` no se reutilizará: es un dato reemplazable importado desde MS Project.

Los formularios preoperacionales reutilizarán `FormTemplateVersion` y su esquema declarativo; no se creará un segundo motor de formularios.

## 5. Flujo principal

1. El gestor registra el equipo y completa documentos y ficha técnica.
2. Vincula el equipo con un proyecto y asigna un operador durante un periodo.
3. El teléfono sincroniza únicamente equipos, asignaciones y formularios autorizados.
4. El operador identifica el equipo por QR o búsqueda y diligencia el preoperacional offline.
5. Al presentar, el registro queda inmutable y entra en la outbox.
6. El servidor valida autorización vigente, asignación, versión de formulario e idempotencia.
7. El registro queda pendiente de revisión.
8. El revisor aprueba, rechaza o solicita corrección sin alterar la evidencia original.
9. Las fallas generan novedades y pueden bloquear el equipo según severidad.
10. La información aprobada alimenta el expediente SST y los reportes.

## 6. Hitos y tareas

### Hito A — Fundación de autorización

Ruta crítica. Bloquea la exposición del módulo de equipos.

- [x] **ACC-01 — Catálogo de secciones, roles y capacidades.** Definir enums, matriz cerrada y pruebas unitarias de cada perfil.
- [x] **ACC-02 — Persistencia de asignaciones.** Crear `SectionGrant`, `SectionProjectScope`, constraints, índices y migración de datos.
- [x] **ACC-03 — Contexto y políticas.** Ampliar `RequestContext`, añadir primitivas `has_capability` y mantener una resolución acotada de consultas.
- [x] **ACC-04 — Migración compatible.** Generar grants equivalentes a los cinco roles actuales y probar paridad antes de activar el nuevo esquema.
- [x] **ACC-05 — Contrato de acceso.** Añadir endpoint de perfil/accesos efectivo y actualizar OpenAPI sin confiar en el JWT como única fuente.
- [x] **ACC-06 — Matrices web.** Construir administración de roles por sección/proyecto y vista de capacidades efectivas.
- [x] **ACC-07 — Navegación autorizada.** Ocultar secciones no asignadas y proteger también sus URLs.
- [x] **ACC-08 — Migración gradual de políticas.** Pasar visitas, proyectos, presupuestos, formularios, GIS, reportes y operaciones por separado.
- [x] **ACC-09 — Adaptación del Admin.** Mantener `is_staff` para entrada, pero comprobar capacidades de dominio en acciones y modelos sensibles.
- [x] **ACC-10 — Seguridad y auditoría.** Impedir autoelevación, concesión superior a la propia y pérdida del último administrador de acceso.

**Criterio de salida:** la matriz refleja los permisos efectivos, las políticas actuales conservan paridad y revocar una sección bloquea la siguiente solicitud autorizada.

### Hito B — Catálogo y ficha técnica de equipos

Depende de ACC-01 a ACC-07.

- [x] **EQ-01 — Diseño detallado y constraints.** Cerrar campos obligatorios, estados, propiedad y tipos iniciales.
- [x] **EQ-02 — Modelos y migración.** Implementar tipos, equipos, documentos, proyectos y asignaciones de operador.
- [x] **EQ-03 — Servicios y políticas.** Aplicar scoping, capacidades de Equipos y auditoría.
- [x] **EQ-04 — Interfaz web.** Listado, ficha, documentos, historial, filtros y estados.
- [x] **EQ-05 — Identificación QR.** Generar código estable sin incluir secretos y admitir búsqueda manual.
  - Payload `suno:eq:<uuid>` (solo el UUID; escanear no concede acceso, se exige sesión y alcance vigente).
  - Generador propio de la plataforma (`segno`, nivel Q, tinta/papel de marca): SVG/PNG descargables, etiqueta imprimible individual y hoja por lote.
  - `equipos/identificar/` resuelve QR escaneado (también lector USB/Bluetooth), código, serie o placa; sin filtrar existencia fuera del alcance.
- [ ] **EQ-06 — API y sincronización de lectura.** Entregar al móvil solo equipos y asignaciones dentro del alcance.
- [ ] **EQ-07 — Pruebas.** Organización, proyecto, vigencias, permisos y archivos.

**Criterio de salida:** el gestor administra un equipo completo y el operador solo recibe los equipos que puede usar.

### Hito C — Preoperacionales móviles y revisión

Depende del catálogo de equipos, del motor actual de formularios y del contrato móvil.

- [ ] **PRE-01 — Modelo de evidencia.** Definir inspección, adjuntos, revisiones, novedades y relación de corrección.
- [ ] **PRE-02 — Contrato OpenAPI.** Añadir bootstrap/pull/push, errores, idempotencia y conflictos.
- [ ] **PRE-03 — Sincronización servidor.** Implementar CAS, cursor, tombstones si aplican y validación del formulario.
- [ ] **PRE-04 — Captura móvil offline.** Equipo, QR, formulario, ubicación, fotos, firma y outbox.
- [ ] **PRE-05 — Reglas de presentación.** Mantener borradores locales y hacer inmutable la evidencia presentada.
- [ ] **PRE-06 — Bandeja de revisión.** Filtros por proyecto/equipo/estado y acciones aprobar, rechazar o solicitar corrección.
- [ ] **PRE-07 — Novedades.** Severidad, bloqueo preventivo y seguimiento hasta cierre.
- [ ] **PRE-08 — Pruebas end-to-end.** Captura sin red, reintento, duplicados, revocación, revisión y segregación de funciones.

**Criterio de salida:** un operador completa un preoperacional sin conexión, lo sincroniza y un revisor decide sobre él sin modificar la evidencia.

### Hito D — Uso, operadores y mantenimiento

Puede comenzar después de EQ-03; converge con preoperacionales en novedades y medidores.

- [ ] **MNT-01 — Cronograma de uso.** Reservas, conflictos, indisponibilidad y vistas calendario/lista.
  - [x] Registro y listado filtrable por fecha, proyecto, equipo, operador y estado.
  - [x] Validación de asignación vigente y conflictos de horario por equipo u operador.
  - [x] Lecturas inicial/final de horómetro o kilometraje y trazabilidad de creación.
  - [ ] Indisponibilidades, edición/cancelación y vista calendario.
- [x] **COS-01 — Costeo por hora e imputación al presupuesto.** (Añadida al ampliar el valor del módulo.)
  - `EquipmentRate`: valores por hora definidos a mano en la ficha del equipo (propiedad, operación, mantenimiento y operador); la tarifa total es su suma. Vigencia desde una fecha; inmutable (se cambia registrando otra posterior), única por equipo y fecha.
  - Cada uso guarda una instantánea (horas, tarifa, costo) con la tarifa vigente en su fecha; una tarifa posterior no reescribe el histórico. Los usos sin tarifa se señalan y se costean después con «Costear usos sin tarifa».
  - Imputación opcional a una partida (`BudgetItem`) del presupuesto del proyecto, por código.
  - Pantalla `equipos/costos/`: costo y horas por equipo, y por partida contra el total de la versión vigente del presupuesto; filtros por proyecto y fechas; opción de incluir programados (proyección). Cancelados nunca cuentan.
  - Permisos: ver tarifas y costos exige `export` de Equipos a nivel organización; registrar tarifas, `update` a nivel organización. Operadores y consulta no ven valores.
  - KPIs del equipo en su ficha, recalculados desde los registros de uso (completados o en curso): horas del mes y acumuladas, horas programadas, días con uso, utilización del mes (horas / días hábiles × 8 h), último uso y lectura de medidor, y —solo con permiso de costos— costo acumulado y del mes, costo real por hora y desglose por componente.
  - Pendiente: combustible por tanqueo, tarifa en espera (standby), jornada disponible configurable por equipo, KPIs comparativos entre equipos, exportación Excel y costo de mantenimiento (MNT-05).
- [ ] **MNT-02 — Historial de operadores.** Vigencias sin solapamientos inválidos y consulta por equipo/persona/proyecto.
- [ ] **MNT-03 — Lecturas.** Horómetro/kilometraje manual o derivado de inspecciones.
- [ ] **MNT-04 — Planes preventivos.** Intervalos por fecha o medidor y próxima fecha/lectura.
- [ ] **MNT-05 — Órdenes.** Preventivas y correctivas, estados, responsable, ejecución y soportes.
- [ ] **MNT-06 — Alertas.** Documentos y mantenimientos próximos/vencidos mediante jobs idempotentes.
- [ ] **MNT-07 — Bloqueos configurables.** Evitar asignar o usar equipos fuera de servicio según regla explícita.
- [ ] **MNT-08 — Pruebas.** Límites temporales, concurrencia, alertas y permisos.

**Criterio de salida:** SUNO puede reconstruir quién operó el equipo, cuándo estuvo programado y qué mantenimientos recibió.

### Hito E — Entregables SST y cierre

Depende de los hitos C y D.

- [ ] **SST-01 — Catálogo de entregables.** Confirmar contenido, formato, periodicidad y destinatarios.
- [ ] **SST-02 — Expediente por equipo.** Ficha, documentos, operadores, inspecciones, novedades y mantenimiento.
- [ ] **SST-03 — Consolidado por proyecto/periodo.** Indicadores y excepciones de cumplimiento.
- [ ] **SST-04 — Exportaciones.** PDF/Excel y paquete de evidencias cuando corresponda.
- [ ] **SST-05 — Trazabilidad.** Incluir versión de formulario, revisión, autoría y marcas de tiempo.
- [ ] **SST-06 — Validación de aceptación.** Casos reales, rendimiento, accesibilidad y revisión de seguridad.
- [ ] **SST-07 — Documentación y operación.** Manuales, respaldo, monitoreo y procedimiento de soporte.

**Criterio de salida:** un responsable SST genera un expediente reproducible y verificable sin recopilar manualmente la información dispersa.

## 7. Priorización

### Ruta crítica

`ACC-01 → ACC-05 → ACC-06/ACC-07 → EQ-01 → EQ-06 → PRE-01 → PRE-04 → PRE-06 → SST-02`.

### Alta prioridad

- Seguridad y auditoría de accesos.
- Catálogo y asignación de equipos.
- Captura offline y revisión.
- Documentos con vencimiento.
- Historial de operadores y mantenimiento.

### Prioridad media

- Calendario visual de uso.
- Alertas avanzadas y bloqueo configurable.
- Consolidado SST e indicadores.

### Posterior al MVP

- IoT/telemetría, ERP, repuestos, analítica predictiva y permisos personalizados fuera de perfiles.

## 8. Estrategia de verificación

- Pruebas puras de la matriz de capacidades y denegación por defecto.
- Pruebas de base de datos para unicidad, vigencias, solapamientos y scoping.
- Pruebas de políticas para todos los perfiles y otra organización.
- Pruebas de migración que comparen permisos antes/después.
- Pruebas de contrato OpenAPI y paridad cliente-servidor.
- Pruebas de sincronización: reintento, concurrencia, conflicto y revocación.
- Pruebas web de matrices, navegación, revisión y exportaciones.
- Pruebas móviles offline y end-to-end.
- Suite completa, Ruff, mypy, migraciones y controles de seguridad existentes.

Ningún hito se considera terminado si rompe los gates de CI del repositorio.

## 9. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Dos fuentes de permisos: RBAC y Django | RBAC como fuente de verdad; Admin solo como adaptador |
| Pérdida de acceso en la migración | Backfill equivalente y pruebas de paridad antes de activar |
| Acceso excesivo por alcance vacío | Denegación por defecto; alcance organizacional siempre explícito |
| Revocación mientras el móvil está offline | Reautorizar el push y conservar localmente el trabajo rechazado |
| Evidencia modificada después de revisión | Presentación inmutable e historial append-only |
| Formularios duplicados | Reutilizar `FormTemplateVersion` |
| Equipos confundidos con recursos de Project | Dominio `Equipment` independiente |
| Vencimientos o alertas duplicadas | Jobs idempotentes con claves estables |
| Exportaciones SST incompletas | Catálogo de entregables y casos de aceptación antes del Hito E |

## 10. Decisiones pendientes

Estas decisiones no bloquean ACC-01 a ACC-04, pero deben cerrarse antes de su hito relacionado:

1. Qué significa `keypass` y si forma parte de autenticación, activación o autorización.
2. Tipos iniciales de equipos y campos obligatorios de cada ficha.
3. Lista exacta de documentos y entregables SST.
4. Si un rechazo exige nueva inspección completa o permite una corrección vinculada.
5. Qué severidades de novedad bloquean automáticamente el equipo.
6. Ventanas de aviso para documentos y mantenimiento.
7. Regla de conciliación de capturas hechas offline después de revocar el acceso.
8. Recursos disponibles y calendario, antes de asignar fechas a los hitos.

## 11. Punto de inicio de construcción

Cuando se autorice comenzar, la primera entrega será exclusivamente **Hito A — Fundación de autorización**, empezando por ACC-01 a ACC-04. No se abrirá todavía el módulo de equipos a usuarios finales. Cada entrega deberá quedar en una rama propia, con migración reversible, pruebas y revisión antes de continuar.
