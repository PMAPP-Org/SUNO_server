# ADR-0020: Plataforma web propia con vistas Django y formularios sin JavaScript

- **Estado:** Aceptada (amplía [ADR-0004](0004-backend-django-ninja.md))
- **Fecha:** 2026-10-18

## Contexto

ADR-0004 usaba el Django Admin como back-office. La portada mandaba a todos al Admin, que exige personal con MFA y solo se abre desde IP permitidas; un usuario normal no tenía entrada propia. Luego llegaron cronograma, presupuestos, control y costos, que no caben en el Admin.

## Decisión

- `/plataforma/` es la aplicación web para miembros de una organización: sesión propia (`/plataforma/ingresar/`), cierre por POST, navegación por secciones según la matriz de acceso.
- Se construye con vistas y plantillas de Django renderizadas en el servidor, CSS propio y **formularios sin JavaScript** (agregar líneas y «Recalcular» son envíos del formulario). Gráficos en SVG generados en el servidor (Gantt, curva S).
- El Admin queda para administración técnica, con su ruta, MFA e IP permitidas.

## Consecuencias

- Sin frontend aparte, sin build de JS y con CSP estricta.
- Cada pieza hace una revisión visual a 1440 y 390 px (Playwright) y versiona la caché de su CSS (`?v=…`).
- Los formularios largos (contratos, materiales) son más pesados de usar que con JS; se aceptó a cambio de simplicidad.

## Referencias

- PR #28 (`88764b9`), `server/config/urls.py`.
- Specs de las piezas 6b y 6c («formularios sin JavaScript»); Gantt SVG propio en vez de Frappe Gantt (etapa 1, `plans/suno-progreso.md`).
