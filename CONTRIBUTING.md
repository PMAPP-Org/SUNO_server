# Contribuir a VisitaCampo

## Regla obligatoria: ningún paso del plan está completo sin su propio CI en verde

Este repositorio se construye siguiendo [`plans/visitacampo-construccion.md`](plans/visitacampo-construccion.md),
paso a paso. Desde el **Paso 3** (Pipelines de CI y gates de seguridad/calidad)
existe un gate de CI que cubre backend (`server/`) y mobile (`app/`), aunque
la mayor parte del código que lintan todavía no exista — se completa
incrementalmente en los pasos siguientes (Paso 4 en adelante para backend,
Paso 10 en adelante para mobile).

**Ningún paso posterior de este plan se considera completo si su propio job
de CI no pasa.** En concreto:

- Si un paso toca código en `server/`, debe dejar en verde
  [`.github/workflows/backend.yml`](.github/workflows/backend.yml):
  `ruff check`, `mypy`, `pytest`, `python manage.py check --deploy`,
  `python manage.py makemigrations --check --dry-run`.
- Si un paso toca código en `app/`, debe dejar en verde
  [`.github/workflows/mobile.yml`](.github/workflows/mobile.yml):
  `dart analyze`, `flutter test`, y el lint anti-TLS-bypass **RD-01**
  (ver más abajo — este último aplica siempre, no solo a `app/`).
- Si un paso toca `infra/.env.example` o cualquier variable de entorno
  nueva, `infra/scripts/check_envvars.sh` debe seguir pasando.
- "0 archivos que lintar/testear" es un **PASS**, no un fallo — así están
  escritos los workflows mientras `server/`/`app/` siguen siendo
  placeholders. Un paso no puede justificar saltarse su CI alegando que
  "todavía no hay nada que lintar": el CI ya maneja ese caso correctamente,
  así que si el job falla es una señal real.

Añadir código sin que el CI correspondiente pase, o desactivar/vaciar un job
para "que pase", se considera el paso incompleto, sin importar lo que diga
el resto del brief de ese paso.

## Cómo ejecutar el CI

### Sin `gh`/remoto de GitHub configurado (situación actual del entorno de desarrollo)

Los workflows de `.github/workflows/` se activan automáticamente en cuanto
el repositorio tenga un remoto en GitHub — no requieren ningún cambio
adicional para funcionar, GitHub Actions es simplemente el runner elegido
(ADR-010 del doc 08 ya asume GitHub Actions para construir la imagen →
GHCR). Mientras tanto, el mismo conjunto de checks se ejecuta localmente:

```bash
bash infra/scripts/ci_local.sh
```

Este script:

- Ejecuta de verdad `ruff`/`mypy`/`pytest`/`dart analyze`/`flutter test`
  cuando la herramienta está instalada **y** hay código que verificar; si
  falta la herramienta o el árbol de código correspondiente aún no existe,
  omite el check con un motivo explícito (no lo cuenta como fallo).
- Ejecuta **siempre**, sin excepción, el lint anti-TLS-bypass RD-01
  (`infra/scripts/lint_no_tls_bypass.sh`) y
  `infra/scripts/check_envvars.sh`, porque ninguno de los dos depende de un
  toolchain externo (solo bash/grep) — no hay excusa para omitirlos.
- Termina con un resumen `PASS=N SKIP=N FAIL=N` y sale con código distinto
  de cero si algo falló de verdad.

### Con remoto de GitHub configurado

Los mismos checks corren automáticamente en cada push/PR vía
[`.github/workflows/backend.yml`](.github/workflows/backend.yml) y
[`.github/workflows/mobile.yml`](.github/workflows/mobile.yml).

## El gate RD-01 (anti-bypass de TLS)

**Hallazgo CRITICAL** (`docs/07-hallazgos-revision-codigo.md`): la
referencia de arquitectura (QField) aceptaba certificados TLS inválidos de
forma incondicional vía `ignoreSslErrors()`. `docs/08-arquitectura-propuesta-visitacampo.md`
(ADR-009, RD-01) lo convierte en una prohibición de diseño y en esta regla
de CI explícita:

> Prohibido cualquier bypass de validación de certificado. En Dart,
> `badCertificateCallback` retorna siempre `false` salvo que exista un pin
> aprobado por el usuario para ese host exacto. No existe flag de build,
> `--insecure`, ni variable de entorno que lo cambie. **Regla de CI: la
> build falla si aparece `badCertificateCallback`, `allowBadCertificates` o
> similar fuera del módulo `TrustStore` auditado.**

`infra/scripts/lint_no_tls_bypass.sh` implementa esta regla: falla si
`badCertificateCallback`, `allowBadCertificates`, `ignoreSslErrors` o
`ignoreCertificateErrors` aparecen en cualquier archivo `.dart` fuera de una
ruta con un segmento de directorio `trust_store` o un archivo llamado
`trust_store.dart` (el módulo `TrustStore` auditado que se construye
funcionalmente en el **Paso 16** con pinning de SPKI y consentimiento
explícito del usuario). Este es el único mecanismo de confianza permitido
para servidores self-hosted con certificado no reconocido por una CA
pública — nunca un bypass incondicional.

Si el lint produce un falso positivo bloqueante, la estrategia de rollback
del Paso 3 aplica: se ajusta el regex/allow-list del script en un commit de
seguimiento — **nunca se elimina el gate** como solución al falso positivo.

## Estilo de commits

Ver [`README.md`](README.md#convención-de-commits): mensajes descriptivos en
modo imperativo, y como mínimo un commit por paso completado del plan.
