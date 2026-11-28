# Problemas conocidos (SUNO_server)

Problemas abiertos al 2026-11-17 (KI-02, KI-04, KI-07 y KI-10 resueltos el 2026-11-24), verificados contra el código, la CI y GitHub. Cada uno debe convertirse en un issue con la etiqueta `known-issue` (y su severidad); a partir de ahí **el issue es la fuente de verdad** y esta lista solo sirve de punto de partida. Severidades como en [`CONTRIBUTING.md`](../../CONTRIBUTING.md#bugs-y-parches): S1 crítica, S2 alta, S3 media, S4 baja.

## KI-01. La prueba de carga semanal del push falla desde el 2026-10-19
- **Issue:** [#85](https://github.com/PMAPP-Org/SUNO_server/issues/85)

- **Título propuesto:** `Prueba de carga: p95 de /v1/sync/push 2,33 s, por encima del objetivo de 1,5 s`
- **Severidad:** S3. Sin impacto visible con los usuarios actuales, pero es el riesgo S-05 de doc 08 (ráfaga de fin de jornada) y ADR-0010 fija 2 s como disparador de migración.
- **Descripción:** `load.yml` (lunes 07:35 UTC) simula 50 dispositivos de una organización con 100 operaciones por lote contra el stack de producción. No se aplicó ninguna operación con fallo, pero el p95 no cumple. Las 5 ejecuciones programadas fallaron: 3,79 s (2026-10-19), …, 2,33 s (2026-11-16). El lock por organización del push (ADR-0003, ADR-0035) serializa a los 50 dispositivos. Hay que decidir si se optimiza el push, se cambia el criterio para runners compartidos o se mide en el VPS real.
- **Evidencia:** `gh run list --workflow load.yml` (runs 39273297855 … 43917406983); log de 43917406983: `FALLO: p95 2.330 s ≥ 1.5 s (riesgo S-05)`; `infra/load/push_load.py`.

## KI-02. ~~Cuatro pruebas de `test_load_script.py` fallan al correr la suite en local~~ — resuelto

- **Issue:** [#86](https://github.com/PMAPP-Org/SUNO_server/issues/86); se cierra con el PR de la rama `fix/deuda-menor`.
- `infra/scripts/test_docker.sh` monta `infra/load/` de solo lectura en `/infra/load` (`835a63a`); el script de carga no cambia. La suite local queda sin fallas. Ver el [historial](historial.md), entrada 42.

## KI-03. ~~El job de pruebas del backend supera los 75 minutos~~ — resuelto

- **Issue:** [#87](https://github.com/PMAPP-Org/SUNO_server/issues/87), cerrado.
- Resuelto en el PR #94 (BD de test migrada una vez por sesión y clonada por módulo); ver el [historial](historial.md), entrada 38.

## KI-04. ~~La descripción de los movimientos generados usa el nombre vivo del ítem o material~~ — resuelto

- **Issue:** [#88](https://github.com/PMAPP-Org/SUNO_server/issues/88); se cierra con el PR de la rama `fix/deuda-menor`.
- El libro copia la etiqueta guardada en el documento: `CertificateLine.label` y `MaterialPurchaseLine.material_label` (migración 0032), y la clave de cada destino en el resumen impreso de cortes y planillas de tareo (`ee0d239`). Los documentos anteriores usan el nombre vigente, como antes. Sigue abierta la otra limitación de 6b: el PDF de un acta vieja muestra el contratado vigente de hoy. Ver el [historial](historial.md), entrada 43.

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

## KI-07. ~~Code scanning: alertas de Semgrep sobre CSRF que son falsos positivos~~ — resuelto

- **Issue:** [#89](https://github.com/PMAPP-Org/SUNO_server/issues/89); se cierra con el PR de la rama `fix/deuda-menor`.
- Causa: la regla `django-no-csrf-token` es de modo genérico y su `...` entre el token y `</form>` abarca como mucho 10 líneas; en la misma línea del `<form>` tampoco lo reconoce. En los 17 formularios largos el token pasa a justo antes de `</form>` y se quitan todas las supresiones `nosemgrep` de esa regla (`c243f95`). `server/suno/core/tests/test_templates_csrf.py` lo vigila. Las alertas abiertas de CSRF se cierran solas con el próximo análisis de `master`; en GitHub no se descartó nada. Quedan abiertas otras alertas (las `var-in-href` suprimidas con `nosemgrep`, que code scanning muestra igual, y las de Actions, `urllib` y CodeQL): ver el reporte del PR. Ver el [historial](historial.md), entrada 44.

## KI-08. ~~Hallazgos de revisión de C5/C6 sin verificar~~ — resuelto

- **Issue:** [#90](https://github.com/PMAPP-Org/SUNO_server/issues/90).
- Verificado el 2026-11-23: los cuatro hallazgos ya estaban corregidos en `181759b`, dentro del PR #37 (squash `ae782ad`). Quedan pruebas de regresión `test_regression_90_*`; ver el [historial](historial.md), entrada 41.

## KI-09. Sentry previsto pero no integrado
- **Issue:** [#91](https://github.com/PMAPP-Org/SUNO_server/issues/91)

- **Título propuesto:** `Errores de aplicación: Sentry (ADR-0010) no está integrado`
- **Severidad:** S4.
- **Descripción:** ADR-0010 y ADR-0014 cuentan con Sentry para errores de aplicación; no aparece en `requirements*` ni en `settings`. Hoy un 500 solo queda en los logs del contenedor.
- **Evidencia:** búsqueda de `sentry` en `server/` e `infra/` sin resultados.

## KI-10. ~~La reversa de la migración 0014 no tiene prueba automática~~ — resuelto

- **Issue:** [#92](https://github.com/PMAPP-Org/SUNO_server/issues/92); se cierra con el PR de la rama `fix/deuda-menor`.
- `server/suno/core/tests/test_quantity_matrix_migration.py` (`5dcfae1`) siembra registros con los modelos históricos de 0014, migra a 0013 y vuelve. Deshacer desde las hojas del grafo tardaba ~5 min; la prueba usa una base aparte y vacía migrada solo hasta 0014 (~11 s) y no toca la plantilla de `dbsupport`.
