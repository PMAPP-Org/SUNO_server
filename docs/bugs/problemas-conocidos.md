# Problemas conocidos (SUNO_server)

Problemas abiertos al 2026-11-17, verificados contra el código, la CI y GitHub. Cada uno debe convertirse en un issue con la etiqueta `known-issue` (y su severidad); a partir de ahí **el issue es la fuente de verdad** y esta lista solo sirve de punto de partida. Severidades como en [`CONTRIBUTING.md`](../../CONTRIBUTING.md#bugs-y-parches): S1 crítica, S2 alta, S3 media, S4 baja.

## KI-01. La prueba de carga semanal del push falla desde el 2026-10-19
- **Issue:** [#85](https://github.com/PMAPP-Org/SUNO_server/issues/85)

- **Título propuesto:** `Prueba de carga: p95 de /v1/sync/push 2,33 s, por encima del objetivo de 1,5 s`
- **Severidad:** S3. Sin impacto visible con los usuarios actuales, pero es el riesgo S-05 de doc 08 (ráfaga de fin de jornada) y ADR-0010 fija 2 s como disparador de migración.
- **Descripción:** `load.yml` (lunes 07:35 UTC) simula 50 dispositivos de una organización con 100 operaciones por lote contra el stack de producción. No se aplicó ninguna operación con fallo, pero el p95 no cumple. Las 5 ejecuciones programadas fallaron: 3,79 s (2026-10-19), …, 2,33 s (2026-11-16). El lock por organización del push (ADR-0003, ADR-0035) serializa a los 50 dispositivos. Hay que decidir si se optimiza el push, se cambia el criterio para runners compartidos o se mide en el VPS real.
- **Evidencia:** `gh run list --workflow load.yml` (runs 39273297855 … 43917406983); log de 43917406983: `FALLO: p95 2.330 s ≥ 1.5 s (riesgo S-05)`; `infra/load/push_load.py`.

## KI-02. Cuatro pruebas de `test_load_script.py` fallan al correr la suite en local
- **Issue:** [#86](https://github.com/PMAPP-Org/SUNO_server/issues/86)

- **Título propuesto:** `test_load_script.py falla en el contenedor local: no encuentra infra/load/push_load.py`
- **Severidad:** S4.
- **Descripción:** al correr `pytest` en el contenedor de desarrollo, las 4 pruebas de `server/suno/sync/tests/test_load_script.py` fallan porque el contenedor no monta `/infra/load/push_load.py`. En la CI de GitHub pasan (está el repositorio completo). Cada pieza lo anota como «4 fallos conocidos del entorno», lo que acostumbra a ignorar rojos.
- **Evidencia:** `plans/suno-progreso.md` (etapa 1 y piezas 3 a 6c, «Verificación real»).

## KI-03. ~~El job de pruebas del backend supera los 75 minutos~~ — resuelto

- **Issue:** [#87](https://github.com/PMAPP-Org/SUNO_server/issues/87), cerrado.
- Resuelto en el PR #94 (BD de test migrada una vez por sesión y clonada por módulo); ver el [historial](historial.md), entrada 38.

## KI-04. La descripción de los movimientos generados usa el nombre vivo del ítem o material
- **Issue:** [#88](https://github.com/PMAPP-Org/SUNO_server/issues/88)

- **Título propuesto:** `Libro de costos: renombrar un ítem entre la firma y la aprobación cambia la descripción del movimiento`
- **Severidad:** S4. No cambia montos.
- **Descripción:** al aprobar un acta, `post_to_ledger` arma la descripción con `catalog.label_of(...)`, es decir, con el nombre del ítem en la versión vigente **al aprobar**, no el que salió en el PDF firmado. Si el ítem se renombra (nueva versión del presupuesto) entre la firma y la aprobación, la descripción del libro no coincide con el papel. Los montos sí se comparan («aprobado = firmado», ADR-0032). En 6c pasa igual con las compras (`material.name`) y los cortes. Relacionado: el PDF de un acta vieja muestra el contratado vigente de hoy (limitación anotada en 6b).
- **Evidencia:** `server/suno/contracts/certificates.py` (`post_to_ledger`); `server/suno/materials/purchases.py`; `plans/suno-progreso.md` (6b, «Limitación conocida»).

## KI-05. MinIO de desarrollo sin imagen fijada (issue #21)
- **Issue:** [#21](https://github.com/PMAPP-Org/SUNO_server/issues/21)

- **Título propuesto:** ya existe: [#21 «Dev: MinIO ya no publica imágenes; elegir S3 local sustituto»](https://github.com/PMAPP-Org/SUNO_server/issues/21). Añadirle la etiqueta `known-issue`.
- **Severidad:** S4 (solo desarrollo).
- **Descripción:** `minio/minio` y `minio/mc` usan `latest` sin digest, en contra de ADR-0019, porque MinIO dejó de publicar imágenes.
- **Evidencia:** `infra/docker-compose.override.dev.yml`; issue #21.

## KI-06. Configuración pendiente de un Admin de la organización (issue #7)
- **Issue:** [#7](https://github.com/PMAPP-Org/SUNO_server/issues/7)

- **Título propuesto:** ya existe: [#7 «Pendiente (requiere Admin): protección de rama, dependency graph y releases»](https://github.com/PMAPP-Org/SUNO_server/issues/7). Añadirle `known-issue`.
- **Severidad:** S3. Sin protección de rama y con auto-integración temporal (EXC-002), un commit puede llegar a `master` (y a producción) sin revisión; Scorecard marca `BranchProtection` y `CodeReview` como altos.
- **Evidencia:** issue #7; `.github/CODEOWNERS`; `docs/seguridad/excepciones.md` (EXC-002); alertas de Scorecard abiertas en code scanning.

## KI-07. Code scanning: alertas de Semgrep sobre CSRF que son falsos positivos
- **Issue:** [#89](https://github.com/PMAPP-Org/SUNO_server/issues/89)

- **Título propuesto:** `Code scanning: alertas django-no-csrf-token en plantillas que sí tienen {% csrf_token %}`
- **Severidad:** S4.
- **Descripción:** Semgrep marca formularios POST cuyo `{% csrf_token %}` va en la línea siguiente. En el repositorio se suprimen con `nosemgrep` en la línea del `<form>`, pero code scanning las sigue mostrando: 4 alertas descartadas a mano como falso positivo (51, 101, 102, 104) y 1 abierta (127, `contracts/amendment_form.html`, del 2026-11-15). Además quedan abiertas 3 `unsafe-add-mask-workflow-command`, 3 `dynamic-urllib-use-detected` y 44 notas de CodeQL (sobre todo de estilo en pruebas). Hay que revisar si conviene subir el SARIF ya filtrado o descartar con justificación.
- **Evidencia:** `gh api repos/PMAPP-Org/SUNO_server/code-scanning/alerts` (abiertas y descartadas); `server/suno/core/templates/suno/contracts/amendment_form.html`.

## KI-08. Hallazgos de revisión de C5/C6 sin verificar
- **Issue:** [#90](https://github.com/PMAPP-Org/SUNO_server/issues/90)

- **Título propuesto:** `Verificar hallazgos de la revisión de C5/C6 (#37): repetibles anidados, relevant compuesto, fechas de Excel, exportes con borrados`
- **Severidad:** S3 hasta verificarlos (uno era un 500 al importar).
- **Descripción:** `plans/suno-progreso.md` («Estado al 2026-10-29») lista cuatro hallazgos del PR #37 y no consta cuáles se corrigieron antes del merge. Verificarlos con una prueba cada uno; cerrar este issue o abrir uno por cada hallazgo vigente.
- **Evidencia:** `plans/suno-progreso.md`; PR #37 (`ae782ad`).

## KI-09. Sentry previsto pero no integrado
- **Issue:** [#91](https://github.com/PMAPP-Org/SUNO_server/issues/91)

- **Título propuesto:** `Errores de aplicación: Sentry (ADR-0010) no está integrado`
- **Severidad:** S4.
- **Descripción:** ADR-0010 y ADR-0014 cuentan con Sentry para errores de aplicación; no aparece en `requirements*` ni en `settings`. Hoy un 500 solo queda en los logs del contenedor.
- **Evidencia:** búsqueda de `sentry` en `server/` e `infra/` sin resultados.

## KI-10. La reversa de la migración 0014 no tiene prueba automática
- **Issue:** [#92](https://github.com/PMAPP-Org/SUNO_server/issues/92)

- **Título propuesto:** `Migraciones: la reversa de 0014_add_quantity_matrix no tiene prueba`
- **Severidad:** S4.
- **Descripción:** anotado como deuda en la pieza 4a. Las piezas posteriores comprueban la reversa a mano (0027–0030), pero no hay prueba automática.
- **Evidencia:** `plans/suno-progreso.md` (pieza 4a, «Deuda»).
