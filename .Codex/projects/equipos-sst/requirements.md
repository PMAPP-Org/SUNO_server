# Requisitos — Equipos, preoperacionales y soporte SST

Estado: requisitos base validados; quedan decisiones funcionales puntuales.

## Problema

SUNO necesita administrar los equipos usados en campo y permitir que el personal capture desde el celular inspecciones preoperacionales asociadas al equipo. Un usuario con función de revisión debe verificar la información, dejar trazabilidad de su decisión y trabajar sobre las novedades encontradas. La información debe servir como soporte para entregables SST.

## Requisitos confirmados

1. El personal de campo diligencia preoperacionales de equipos desde el celular.
2. La captura debe integrarse con el modelo offline-first existente de SUNO.
3. Cada preoperacional queda asociado, como mínimo, al equipo, usuario, fecha/hora y organización.
4. Un rol de revisión verifica la información enviada y gestiona el resultado.
5. La plataforma debe admitir roles diferenciados por sección, no solamente un rol global por organización.
6. Debe existir una matriz administrativa de usuarios contra secciones y permisos CRUD efectivos.
7. Debe existir una matriz de secciones disponibles para cada usuario.
8. Debe contemplarse un mecanismo denominado provisionalmente `keypass` asignable a usuarios; su propósito y comportamiento están pendientes de definición.
9. El módulo de equipos comprende inicialmente:
   - registro e identificación del equipo;
   - ficha técnica y documentos;
   - historial de operadores;
   - historial y programación de mantenimiento;
   - cronograma de uso;
   - preoperacionales móviles;
   - soporte para entregables SST.
10. La autorización por secciones se desarrollará antes del módulo de equipos.
11. Los cinco roles organizacionales existentes se conservarán inicialmente para compatibilidad, pero no serán la única fuente de autorización funcional.
12. Se adopta un RBAC híbrido con una asignación activa por usuario y sección.
13. El alcance por proyecto forma parte del MVP.

## Estado actual validado

- Existen cinco roles organizacionales: `OWNER`, `ADMIN`, `SUPERVISOR`, `FIELD_AGENT` y `VIEWER`.
- `Membership` permite una sola fila por usuario y organización; en la práctica, un usuario tiene un único rol global.
- `OWNER` y `ADMIN` tienen hoy las mismas capacidades de negocio.
- `SUPERVISOR` puede revisar registros, asignar trabajo, asignar responsables y exportar información.
- `FIELD_AGENT` puede capturar y modificar sus propios registros, pero no revisar registros ni ver presupuestos.
- `VIEWER` solo consulta, aunque puede exportar presupuestos.
- El acceso al Django Admin es una segunda capa independiente basada en `is_staff` y permisos de modelo.
- No existen roles por sección, matriz CRUD, matriz de secciones ni un mecanismo `keypass`.

## Actores preliminares

- **Personal de campo / operador:** consulta equipos habilitados y asignados; crea preoperacionales desde el celular; consulta el estado de sus propios registros.
- **Revisor:** consulta preoperacionales dentro de su alcance; aprueba, rechaza o solicita corrección; registra comentarios y novedades sin reescribir la evidencia original.
- **Gestor de equipos:** administra equipos, fichas técnicas, documentos, asignaciones de operadores, mantenimiento y programación de uso.
- **Administrador de accesos:** asigna roles por sección y consulta las matrices de permisos y disponibilidad.
- **Consulta / auditoría SST:** lectura y exportación de expedientes y soportes, sin modificación operativa.

## Principios de autorización propuestos

- Denegación por defecto: una sección no asignada no aparece en navegación ni acepta solicitudes por API.
- Separar el rol organizacional base del rol funcional por sección.
- Los permisos se evalúan en el servidor; ocultar menús no sustituye autorización.
- Las asignaciones de sección y sus cambios quedan auditados.
- El cliente móvil recibe únicamente secciones, acciones y datos autorizados para ese usuario.
- Las evidencias presentadas no se editan en sitio: las correcciones y revisiones generan historial.
- La matriz muestra permisos efectivos; no debe obligar a interpretar múltiples capas de permisos manualmente.

## Arquitectura de permisos por comparar

### A. Roles fijos codificados por sección

Rápido y simple, pero cada cambio de matriz requiere despliegue y no ofrece suficiente administración desde la plataforma.

### B. CRUD completamente configurable por usuario

Muy flexible, pero propenso a configuraciones inconsistentes, difícil de auditar y complejo de sincronizar de forma segura con el cliente móvil.

### C. Modelo híbrido — recomendado para validar

Las capacidades sensibles se definen en código y se agrupan en perfiles de rol por sección. La base de datos guarda la asignación `usuario + sección + rol + alcance`. La interfaz presenta las matrices de roles, CRUD y secciones efectivas. Permite administración sin aceptar combinaciones inseguras o incoherentes.

## Flujo preliminar del preoperacional

1. El gestor habilita el equipo y asigna operador, proyecto y periodo de uso.
2. El teléfono sincroniza equipos, asignaciones y versión vigente del formulario.
3. El operador identifica el equipo y diligencia el preoperacional sin conexión si es necesario.
4. El registro y sus evidencias se sincronizan conservando autoría, dispositivo y momento de captura.
5. El sistema lo deja pendiente de revisión.
6. El revisor aprueba, rechaza o solicita corrección y registra observaciones/novedades.
7. La decisión y toda actuación posterior quedan en un historial inmutable.
8. Los registros aprobados alimentan consultas y entregables SST.

## Decisiones abiertas

1. Definir exactamente qué significa `keypass`: passkey/WebAuthn, código de activación, PIN operativo, llave de acceso a secciones u otro concepto.
2. Precisar si un preoperacional rechazado se corrige como nueva versión, se reemplaza con un nuevo registro o genera una acción correctiva independiente.
3. Definir si el equipo se identifica por búsqueda, código interno, QR, NFC o varios mecanismos.
4. Definir los tipos iniciales de equipos y si incluyen equipos propios, alquilados y de contratistas.
5. Enumerar los soportes y entregables SST requeridos, sus formatos y periodicidad.
6. Definir el alcance del rol por sección para módulos existentes: proyectos, presupuestos, formularios, GIS, reportes, administración y equipos.
7. Definir si una persona puede tener más de un rol en la misma sección y si el alcance se limita por proyecto.
8. Definir reglas de vencimiento, alertas y bloqueo operativo por documentos o mantenimiento vencido.
9. Definir indicadores de éxito del MVP y restricciones de tiempo.

## Fuera de decisión por ahora

- No se implementará código hasta aprobar requisitos, arquitectura y fases.
- El modelo existente `Project.Resource` no se reutilizará como equipo: representa recursos importados de MS Project y puede reemplazarse en cada importación.
