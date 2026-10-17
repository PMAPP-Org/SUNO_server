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
`ignoreCertificateErrors` aparecen en cualquier archivo `.dart` fuera de la
ruta canónica exacta `app/lib/core/security/trust_store.dart` (o cualquier
archivo bajo `app/lib/core/security/trust_store/`, si Paso 16 lo divide en
varios archivos) — el módulo `TrustStore` auditado que se construye
funcionalmente en el **Paso 16** con pinning de SPKI y consentimiento
explícito del usuario. El allow-list está anclado a ese prefijo desde la
raíz del repo: un directorio o archivo llamado `trust_store` en cualquier
otra ruta (p. ej. `app/lib/features/auth/trust_store/`) **no** cuenta como
excepción y sigue bloqueado por el gate. Este es el único mecanismo de
confianza permitido para servidores self-hosted con certificado no
reconocido por una CA pública — nunca un bypass incondicional.

Si el lint produce un falso positivo bloqueante, la estrategia de rollback
del Paso 3 aplica: se ajusta el regex/allow-list del script en un commit de
seguimiento — **nunca se elimina el gate** como solución al falso positivo.

## Dependencias y gates de seguridad (línea base ISO 27001)

**Python (`server/`)**: los `requirements*.txt` son la fuente que se edita;
lo que se instala en CI y en la imagen Docker son los `requirements*.lock`
(árbol completo con versiones exactas y hashes). Tras cambiar un `.txt` —a
mano o en un PR de Dependabot— se regenera su `.lock` con el comando de la
cabecera de ese mismo archivo (`uv pip compile --universal --generate-hashes
...`). `infra/scripts/check_lockfiles.py` deja el CI en rojo mientras no
coincidan. Entorno local:

```bash
python -m pip install --require-hashes -r server/requirements-dev.lock
```

La lista completa de gates, su correspondencia con OWASP Top 10:2025 e
ISO 27001 y lo que falta por cubrir está en
[`docs/seguridad/owasp-top10-2025.md`](docs/seguridad/owasp-top10-2025.md).

**Gates que corren en cada PR y en `master`** (además de lint/tests):

| Gate | Dónde | Control ISO 27001 |
|---|---|---|
| `bandit` (SAST) | `backend.yml` | A.8.28, A.8.29 |
| `pip-audit` y `osv-scanner` (CVEs en lockfiles; `osv-scanner` también semanal) | `backend.yml`, `security.yml` | A.8.8 |
| `gitleaks` sobre todo el historial | `security.yml` | A.8.4, A.8.12 |
| `check --deploy --fail-level WARNING` contra `config.settings.prod` | `backend.yml` | A.8.9, A.8.31 |
| `docker build` desde el lockfile con `--require-hashes` | `backend.yml` | A.8.9 |
| SBOM CycloneDX (artefacto del run) | `backend.yml` | A.8.8 |

**Falsos positivos**: se suprimen solo por identificador exacto y con la
justificación al lado —`# nosec B608` en el código para Bandit, huella exacta
en `.gitleaksignore` para gitleaks—, nunca desactivando una regla entera.

**Actions de GitHub**: se referencian por SHA de commit con la versión en un
comentario (`uses: actions/checkout@<sha> # v7.0.1`); Dependabot actualiza
ambos a la vez.

## Firma de releases Android

El APK/AAB de release se firma con la llave de subida definida en
`app/android/key.properties` (`storeFile`, `keyAlias`, `storePassword`,
`keyPassword`) y el `.jks` al que apunta. **Ninguno de los dos se versiona**
(`app/android/.gitignore`) y deben guardarse en un gestor de secretos con
copia de seguridad: perder la llave de subida obliga a un proceso de
recuperación con Google Play. Sin `key.properties`, `flutter build apk
--release` falla a propósito en vez de firmar con la clave de debug. El job
`android-release` de `mobile.yml` lo comprueba en cada PR con una llave
efímera.

## Releases (tags y versiones automáticos)

El versionado lo lleva [release-please](https://github.com/googleapis/release-please)
(`.github/workflows/release.yml`) a partir de los commits de `master`, que
deben seguir [Conventional Commits](https://www.conventionalcommits.org/):
`feat:` sube la versión menor, `fix:` el parche y `feat!:`/`BREAKING CHANGE:`
la mayor (antes de 1.0, la menor). Los PR se integran con **Squash and
merge** y su título es el mensaje del commit; `pr-title.yml` lo exige.

1. Cada push a `master` actualiza el PR `chore: release X.Y.Z` con el
   CHANGELOG y la versión nueva en `version.txt`, `app/pubspec.yaml`,
   `app/lib/core/constants.dart` y `server/pyproject.toml`.
2. Mergear ese PR crea el tag `vX.Y.Z` y la GitHub Release.
3. La release publica la imagen `ghcr.io/<owner>/visitacampo-api:X.Y.Z` con
   SBOM y atestación de procedencia, y —si existen los secretos— el APK
   firmado con su checksum y atestación.

Configuración que hace un admin una sola vez: *Settings → Actions → Allow
GitHub Actions to create and approve pull requests*, y el environment
`release` (restringido a `master`) con los secretos
`ANDROID_UPLOAD_KEYSTORE_B64` (el `.jks` en base64),
`ANDROID_UPLOAD_KEY_ALIAS`, `ANDROID_UPLOAD_KEYSTORE_PASSWORD`,
`ANDROID_UPLOAD_KEY_PASSWORD` y, opcionalmente, `RELEASE_PLEASE_TOKEN`.

## Estilo de commits

Ver [`README.md`](README.md#convención-de-commits): mensajes descriptivos en
modo imperativo, y como mínimo un commit por paso completado del plan.
