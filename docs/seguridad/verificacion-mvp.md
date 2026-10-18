# Verificación del MVP (Paso 25)

Gate de cierre de la Fase 1: la suite de seguridad, la prueba de carga y los criterios de aceptación medibles de doc 08 §8. El plan pide un **informe**: qué está verificado, cómo y dónde se re-ejecuta, y qué queda abierto. Un criterio no cumplido no se da por cerrado en silencio: queda abajo como riesgo abierto, con su plan.

Estado a 2026-10-17. Leyenda: **CI** = automatizado, corre en cada PR o en un workflow programado; **Manual** = requiere dispositivo o entorno que esta máquina y los runners no tienen.

## Mapa hallazgo → test

Los 21 hallazgos de la revisión de QField/QFieldCloud (H-01…H-21) tienen al menos un test automatizado, salvo H-09/H-19 (no aplicables: `static_cast` de C++). El mapa está en [`hallazgos-tests.toml`](hallazgos-tests.toml) y lo comprueban dos gates:

- SUNO_server, Backend CI: `infra/scripts/check_findings_map.py --repo server`.
- SUNO_client, Mobile CI: `tool/check_findings_map.py --repo client`, sobre el mismo mapa descargado de este repositorio.

Si se renombra o borra el test que cubre un hallazgo, la CI falla en vez de dejarlo descubierto.

## Suite de seguridad (doc 08 §8 Fase 1)

| # | Ítem | Estado | Dónde |
|---|---|---|---|
| 1 | Aislamiento entre organizaciones (404, no 403) | CI | `sync/tests/test_pull_isolation_budget.py` (pull y bootstrap, **nuevo**), `sync/tests/test_push.py`, `attachments/tests/test_presign_commit.py`, `backoffice/tests/test_admin_scoping.py` (Admin por HTTP), `core/tests/test_policies.py` |
| 2 | Presupuesto de queries | CI | Push: `sync/tests/test_query_budget.py` (coste marginal de 1 query por operación). Pull y exportación del Paso 24: queries constantes con 1 y 50/30 filas (**nuevo**) |
| 3 | `kill -9` al worker | CI | `jobs/tests/test_kill9.py`: proceso real, muerte sin `finally`, recuperación por el reaper. En el stack: DAST comprueba que el worker procesa la cola |
| 4 | Traversal de nombres de archivo | CI | `attachments/tests/test_blobstore.py::TraversalTests`: relativo, encadenado, absolutas POSIX y Windows, UNC, URL-encoded, byte nulo, separadores, mayúsculas, longitud |
| 5 | Spoof de `X-Forwarded-Proto` | CI (stack) | DAST: un `X-Forwarded-Proto: https` sobre HTTP plano sigue redirigiendo (Caddy sobrescribe la cabecera). `core/tests/test_prod_settings.py`: no se confía en `X-Forwarded-Host` |
| 6 | Checksum de adjunto no coincidente | CI | `attachments/tests/test_presign_commit.py` |
| 7 | Reuso de refresh token | CI | `authn/tests/test_rotation.py` (revoca la familia); cliente: `auth_controller_test.dart` (re-login limpio, outbox intacta) |
| 8 | TLS del cliente contra un certificado real | CI (parcial) | `trust_store_test.dart` contra servidores HTTPS reales con certificados de openssl: rechazo sin pin, pin actual/siguiente, pin caducado, cero peticiones antes de verificar, cambio bloqueado. **Abierto**: probarlo contra el certificado de Let's Encrypt del dominio oficial cuando exista (no hay staging todavía) |

## Prueba de carga (tareas 9-11)

| # | Tarea | Estado |
|---|---|---|
| 9 | 50 dispositivos de una organización vacían su outbox a la vez | Workflow `Load test` (`.github/workflows/load.yml`), manual y semanal, sobre el stack de producción con TLS; script `infra/load/push_load.py` (solo biblioteca estándar, TLS verificado con la CA de Caddy). El contrato del lote se prueba en cada PR (`sync/tests/test_load_script.py`) |
| 10 | p95 de push con 100 operaciones < 1,5 s | El workflow falla si p95 ≥ 1,5 s o si alguna operación no queda `applied`. **Pendiente**: primera ejecución y su resultado en esta tabla |
| 11 | Si el riesgo S-05 se materializa | Se documenta aquí y se abre el seguimiento del *escape hatch* de doc 08 §4.4 (cursor por snapshot o particionar el lock). No se implementa sin una medición que lo justifique |

Un runner compartido de GitHub mide con ruido y tiene menos recursos que el VPS de producción: un p95 holgado allí es una buena señal; uno ajustado se repite en el VPS antes de concluir nada.

## Criterios de aceptación medibles (doc 08 §8 Fase 1)

| # | Criterio | Estado |
|---|---|---|
| 12 | Jornada de 8 h sin red, ≥ 50 visitas con 10 fotos, sin degradación perceptible | **Manual**, en emulador o teléfono de gama baja. Pendiente |
| 13 | Drenaje de esa outbox en < 5 min con 4G decente | **Manual**. Pendiente |
| 14 | p95 de `/sync/push` con 100 operaciones < 1,5 s | Tarea 10 (workflow `Load test`) |
| 15 | Arranque en frío < 3 s en gama baja (2 GB RAM, Android 10) | **Manual**. Pendiente |
| 16 | Cero pérdida de datos: (a) app matada a mitad de guardado, (b) red cortada a mitad de push, (c) servidor caído durante la subida de un adjunto | (a) CI: atomicidad visita + outbox en una transacción (`database_test.dart`, H-04); (b) CI: idempotencia por `op_uuid` y reintento (`test_push.py`, `sync_engine_test.dart`); (c) CI: la cola de adjuntos reintenta con backoff y cierra el archivo (`attachment_upload_service_test.dart`, H-05). **Manual**: repetir los tres matando el proceso real en un dispositivo |
| 17 | `assertNumQueries` constante en push sin importar el lote | CI (ítem 2) |

## Riesgos abiertos

| Riesgo | Plan |
|---|---|
| Criterios 12, 13, 15 y la parte manual del 16 sin medir | Sesión en emulador Android de gama baja y en un teléfono real. Quien la haga anota aquí las cifras y el modelo de dispositivo |
| Prueba de carga sin primera ejecución | Lanzar `Load test` desde Actions → Run workflow y copiar el resumen a la tarea 10 |
| TLS contra un certificado real del dominio oficial | Al tener dominio: fijar los pines SPKI en el build (`VISITACAMPO_SPKI_PINS`, `VISITACAMPO_SPKI_PINS_EXPIRE`) y repetir el ítem 8 contra él |

El MVP no se declara cerrado mientras haya filas en esta tabla.
