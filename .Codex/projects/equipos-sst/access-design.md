# Propuesta de arquitectura — autorización por secciones

Estado: arquitectura aprobada el 2026-11-05; desarrollada en el plan maestro del módulo.

## Objetivo

Permitir que cada usuario tenga un rol distinto en cada sección de SUNO, que la plataforma muestre sus permisos efectivos en matrices administrativas y que servidor, web y cliente móvil apliquen las mismas decisiones de autorización.

## Alternativas evaluadas

### 1. Ampliar los cinco roles globales

Consiste en crear más valores en `Membership.role`, por ejemplo `EQUIPMENT_REVIEWER` o `BUDGET_MANAGER`.

- Ventaja: cambio pequeño.
- Problema: combina responsabilidades no relacionadas. Una persona que revisa equipos podría terminar heredando permisos de visitas, proyectos o presupuestos.
- Resultado: descartada; no satisface “un rol por sección”.

### 2. Usar solamente grupos y permisos de Django

Consiste en modelar todo con `auth.Group` y permisos `add/view/change/delete`.

- Ventaja: el Django Admin ya entiende este esquema.
- Problemas: no representa bien revisión, aprobación, exportación, publicación, asignación ni alcance por proyecto; tampoco es el contrato natural de la API móvil/offline.
- Resultado: útil como adaptación del Admin, pero insuficiente como fuente de verdad.

### 3. RBAC de dominio híbrido

Mantiene el rol organizacional de `Membership`, añade una asignación de rol por sección y deriva las capacidades desde una matriz cerrada y auditable.

- Ventajas: mínimo privilegio, lectura sencilla de permisos, compatible con web/API/móvil y evita combinaciones arbitrarias inseguras.
- Coste: requiere migración, contexto de autorización ampliado y adaptación gradual de políticas existentes.
- Resultado: **recomendado**.

## Modelo recomendado

### Rol organizacional existente

`Membership(org, user, role)` se conserva durante la transición:

- acredita que la persona pertenece a la organización;
- mantiene compatibilidad con login, JWT, Admin y políticas actuales;
- sirve para decisiones transversales excepcionales, como `OWNER` de la organización.

### Catálogo de secciones

Identificadores definidos en código, estables y versionados:

| Código | Sección |
|---|---|
| `FIELD_RECORDS` | Registros y visitas de campo |
| `PROJECTS` | Proyectos y cronogramas |
| `BUDGETS` | Presupuestos |
| `FORMS` | Formularios |
| `EQUIPMENT` | Equipos, preoperacionales y mantenimiento |
| `GIS` | Mapas y capas GIS |
| `REPORTS` | Reportes y entregables |
| `USERS_ACCESS` | Usuarios, roles y accesos |
| `OPERATIONS` | Operación técnica, jobs y auditoría |

`HOME` no necesita asignación: todo miembro activo puede entrar al espacio general.

### Asignación por usuario

Modelo conceptual `SectionGrant`:

| Campo | Propósito |
|---|---|
| `id` | UUID |
| `org_id` | aislamiento por organización |
| `user_id` | usuario autorizado |
| `section` | sección de la plataforma |
| `role` | perfil dentro de la sección |
| `active` | revocación sin perder historial |
| `granted_by` | quién asignó el acceso |
| `granted_at` | fecha de asignación |
| `updated_at` | último cambio |

Restricción propuesta: una asignación activa por `organización + usuario + sección`. Esto materializa literalmente un rol por sección y evita la suma accidental de perfiles.

### Capacidades

CRUD no es suficiente. La matriz debe contemplar:

- `create`, `read`, `update`, `delete`;
- `review`, `approve`, `reject`;
- `assign`;
- `import`, `publish`;
- `export`;
- `schedule`;
- `manage_access`.

Las capacidades se definen en código por perfil y sección. No se guardan como casillas arbitrarias por usuario. La base de datos guarda el rol y la interfaz calcula y muestra los permisos efectivos.

## Perfiles base

Los nombres son comunes, pero cada sección define exactamente qué capacidades entrega cada uno:

- `MANAGER`: administra la sección y sus catálogos.
- `REVIEWER`: verifica y decide sobre información presentada.
- `CONTRIBUTOR`: crea y actualiza trabajo dentro de su alcance.
- `VIEWER`: consulta información autorizada.

Perfiles especializados cuando el dominio lo necesita:

- `EQUIPMENT_OPERATOR`: equipos asignados y preoperacionales propios.
- `SST_AUDITOR`: lectura y exportación de soportes SST.
- `ACCESS_ADMIN`: asignación de usuarios, secciones y roles.

## Matriz inicial de Equipos

| Capacidad | Gestor | Revisor | Operador | Auditor SST | Consulta |
|---|:---:|:---:|:---:|:---:|:---:|
| Ver equipos autorizados | Sí | Sí | Asignados | Sí | Sí |
| Crear/editar ficha técnica | Sí | No | No | No | No |
| Gestionar documentos | Sí | No | No | No | No |
| Asignar operadores | Sí | No | No | No | No |
| Programar uso | Sí | Consulta | Consulta propia | Consulta | Consulta |
| Programar mantenimiento | Sí | Consulta | No | Consulta | Consulta |
| Crear preoperacional | Opcional | No | Sí | No | No |
| Editar borrador propio | Sí | No | Sí | No | No |
| Alterar evidencia presentada | No | No | No | No | No |
| Revisar/aprobar/rechazar | No | Sí | No | No | No |
| Gestionar novedades | Sí | Sí | Consulta propia | Consulta | Consulta |
| Exportar expediente SST | Sí | Sí | No | Sí | Según política |

La evidencia presentada es inmutable. Una corrección crea una nueva versión o actuación vinculada; nunca sobrescribe el registro revisado.

## Matrices administrativas

### Usuarios por sección

Filas de usuarios y columnas de secciones. Cada celda muestra:

- sin acceso;
- rol asignado;
- estado activo/inactivo;
- alcance, cuando se incorpore alcance por proyecto.

### CRUD y capacidades efectivas

Al seleccionar un usuario se muestra una tabla de secciones contra capacidades. Es de solo lectura respecto a las casillas: el administrador cambia el rol, y la matriz explica el efecto. Así se evita construir perfiles incoherentes permiso por permiso.

## Evaluación en servidor

1. El login sigue exigiendo `Membership` activa.
2. `RequestContext` se amplía con asignaciones por sección resueltas desde la base de datos.
3. Las políticas preguntan por capacidades, no por comparaciones dispersas de roles.
4. Cada solicitud vuelve a leer los accesos efectivos, como hoy hace con los roles; revocar una sección surte efecto inmediatamente sin esperar el vencimiento del JWT.
5. Toda consulta mantiene el aislamiento `org_id` y los alcances específicos del recurso.
6. Si no existe asignación, la respuesta es denegación por defecto.

## Cliente móvil y operación offline

- El perfil de autenticación informa las secciones/capacidades vigentes para construir la navegación.
- El bootstrap entrega solamente equipos, asignaciones y formularios dentro del alcance.
- El servidor vuelve a autorizar cada push; un permiso almacenado localmente nunca autoriza por sí solo.
- Si se revoca el acceso mientras el teléfono está offline, la captura pendiente no se pierde: se conserva localmente, pero el servidor la rechaza o la envía a conciliación según una regla que se definirá en el contrato.
- Las modificaciones de acceso no se confiarán únicamente al contenido del JWT.

## Migración inicial propuesta

Para no quitar acceso durante el despliegue, una migración crea asignaciones equivalentes a las políticas actuales:

- `OWNER` y `ADMIN`: `MANAGER` en las secciones actuales y `ACCESS_ADMIN` en usuarios/accesos.
- `SUPERVISOR`: `REVIEWER` en registros de campo; capacidad de supervisión en proyectos; lectura/exportación en presupuestos.
- `FIELD_AGENT`: `CONTRIBUTOR` en registros de campo y acceso restringido en proyectos.
- `VIEWER`: `VIEWER` en proyectos, presupuestos, GIS y registros autorizados.

Después de comprobar paridad, las políticas se migran sección por sección. No se cambia todo en una sola entrega.

## Adaptación del Django Admin

El RBAC de dominio será la fuente de verdad. `is_staff` seguirá controlando si se puede entrar al Admin, pero los permisos de modelo se derivarán o se comprobarán contra las capacidades de sección. No se mantendrán dos matrices editables independientes.

El superusuario continuará siendo operador técnico de la plataforma, separado de los roles del cliente.

## Auditoría y seguridad

- Cada alta, cambio o revocación de acceso genera un `AuditEvent`.
- Nadie puede conceder una capacidad que no posee, salvo el operador técnico expresamente autorizado.
- Un usuario no puede elevar sus propios permisos.
- Debe existir siempre al menos un responsable de acceso activo por organización.
- Las operaciones entre organizaciones responden como recurso inexistente, siguiendo la política actual.
- Los cambios de rol deben poder reconstruirse históricamente.

## Decisiones

1. Modelo híbrido: **aprobado**.
2. Una sola asignación activa por usuario y sección: **aprobado**.
3. Alcance por proyecto dentro del MVP: **aprobado**.
4. Significado y función de `keypass`: pendiente.
5. Tratamiento de capturas offline realizadas justo después de una revocación: pendiente de cerrar en el contrato móvil.
