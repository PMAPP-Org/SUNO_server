"""Cola de trabajos sobre PostgreSQL: `SKIP LOCKED` + lease (Paso 19, ADR-006).

Paquete Python, **no una app de Django**: mismo criterio que `forms/` (Paso 20)
y `attachments/` (Paso 17). La única tabla que necesita —`job`— ya existe en
`core` desde el Paso 5, así que una app propia solo añadiría un `AppConfig` y
un directorio de migraciones vacío. El comando de gestión
(`manage.py run_worker`) vive en `visitacampo/management/commands/`, dentro de
la app `visitacampo` que ya está en `INSTALLED_APPS`: Django descubre los
comandos de **cualquier** app instalada, así que tampoco hace falta una app
para eso.

Mapa del paquete:

- `queue.py`    — las sentencias de la cola (encolar, tomar, heartbeat,
                  terminar, fallar, reaper). Aquí vive todo el SQL.
- `registry.py` — de `job.type` a la función que lo ejecuta.
- `periodic.py` — los tres periódicos reales del MVP y su recurrencia.
- `worker.py`   — el bucle del proceso worker.
- `services.py` — `GET /v1/jobs/{id}` (autorización + lectura).
- `schemas.py`  — el `Job` del contrato.
- `api.py`      — el router de Ninja.
"""
