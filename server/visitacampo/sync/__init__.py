"""Motor de sincronización: `POST /v1/sync/push` (ADR-003, Paso 8).

**Por qué un paquete y no una app de Django** (la nota del controlador decía
"crea la app … regístrala en `INSTALLED_APPS`"): este paquete **no define
ningún modelo**. `Visit` y `SyncOpLog` ya existen en `visitacampo.core`
(Paso 5) y este paso solo los escribe. La regla que el propio repositorio ya
tenía escrita —`visitacampo/authn/apps.py`: "por qué es una app de Django y
no un paquete a secas: tiene un modelo propio"; `visitacampo/serverinfo/api.py`:
"por qué tampoco es una app de Django: no tiene modelos"— clasifica esto como
paquete. Registrarlo en `INSTALLED_APPS` añadiría un `label` y un directorio
`migrations/` vacíos que solo pueden envejecer mal. La decisión se documenta
aquí y se reporta; si un paso posterior le añade un modelo propio, promoverlo
a app es un cambio de tres líneas.

Nada de esto lo exime del test de arquitectura del Paso 6: ese test escanea
`server/visitacampo/**` por ruta, no por `INSTALLED_APPS`, así que la
prohibición de tocar un manager sin `scoped_qs` aplica igual aquí (y se
cumple: ver `services.py`).

**Reparto de responsabilidades** (un archivo, una responsabilidad):

- `schemas.py`    — las formas del contrato (`PushRequest`/`PushResponse`).
- `payloads.py`   — validación estricta por operación (RD-03): tipos sellados.
- `changeseq.py`  — el lock por organización y la asignación de `change_seq`.
- `sql.py`        — las sentencias CAS de una sola pasada.
- `services.py`   — la transacción: lock → idempotencia → aplicar → log.
- `api.py`        — la vista: límites de lote, autenticación y traducción JSON.
"""
