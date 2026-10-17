# Contribuir a SUNO_server

Backend de VisitaCampo: API, panel de administración (Django Admin) e infraestructura. La app móvil está en [PMAPP-Org/SUNO_client](https://github.com/PMAPP-Org/SUNO_client), con sus propias reglas de contribución.

## Regla obligatoria: ningún paso del plan está completo sin su propio CI en verde

Este repositorio se construye siguiendo [`plans/visitacampo-construccion.md`](plans/visitacampo-construccion.md),
paso a paso. Desde el **Paso 3** (Pipelines de CI y gates de seguridad/calidad)
existe un gate de CI que cubre el backend (`server/`) y la infraestructura
(`infra/`). Los pasos del carril móvil se verifican en el CI de
[SUNO_client](https://github.com/PMAPP-Org/SUNO_client).

**Ningún paso posterior de este plan se considera completo si su propio job
de CI no pasa.** En concreto:

- Si un paso toca código en `server/`, debe dejar en verde
  [`.github/workflows/backend.yml`](.github/workflows/backend.yml):
  `ruff check`, `mypy`, `pytest`, `python manage.py check --deploy`,
  `python manage.py makemigrations --check --dry-run`.
- Si un paso toca `infra/.env.example` o cualquier variable de entorno
  nueva, `infra/scripts/check_envvars.sh` debe seguir pasando.
- "0 archivos que lintar/testear" es un **PASS**, no un fallo — así están
  escritos los workflows mientras un árbol de código sigue siendo un
  placeholder. Un paso no puede justificar saltarse su CI alegando que
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

- Ejecuta de verdad `ruff`/`mypy`/`pytest`/`bandit`/`pip-audit`
  cuando la herramienta está instalada **y** hay código que verificar; si
  falta la herramienta o el árbol de código correspondiente aún no existe,
  omite el check con un motivo explícito (no lo cuenta como fallo).
- Ejecuta **siempre**, sin excepción, `infra/scripts/check_envvars.sh`,
  porque no depende de un toolchain externo (solo bash/grep).
- Termina con un resumen `PASS=N SKIP=N FAIL=N` y sale con código distinto
  de cero si algo falló de verdad.

### Con remoto de GitHub configurado

Los mismos checks corren automáticamente en cada push/PR vía
[`.github/workflows/backend.yml`](.github/workflows/backend.yml), además de
los de seguridad, DAST, backup, monitoreo e infraestructura.

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
| `semgrep` (OWASP Top 10, secretos, Docker, Actions) | `security.yml` | A.8.28, A.8.29 |
| `pip-audit` y `osv-scanner` (CVEs en lockfiles; `osv-scanner` también semanal) | `backend.yml`, `security.yml` | A.8.8 |
| `gitleaks` sobre todo el historial | `security.yml` | A.8.4, A.8.12 |
| `check --deploy --fail-level WARNING` contra `config.settings.prod` | `backend.yml` | A.8.9, A.8.31 |
| `docker build` desde el lockfile con `--require-hashes` | `backend.yml` | A.8.9 |
| SBOM CycloneDX (artefacto del run) | `backend.yml` | A.8.8 |

**Falsos positivos**: se suprimen solo por identificador exacto y con la
justificación al lado —`# nosec B608` en el código para Bandit, huella exacta
en `.gitleaksignore` para gitleaks, `# nosemgrep: <regla>` para Semgrep—, nunca
desactivando una regla entera. Una vulnerabilidad que se **acepta** (no un
falso positivo) se registra en
[`docs/seguridad/excepciones.md`](docs/seguridad/excepciones.md) con fecha
de caducidad.

**Actions de GitHub**: se referencian por SHA de commit con la versión en un
comentario (`uses: actions/checkout@<sha> # v7.0.1`); Dependabot actualiza
ambos a la vez.

## Releases (tags y versiones automáticos)

El versionado lo lleva [release-please](https://github.com/googleapis/release-please)
(`.github/workflows/release.yml`) a partir de los commits de `master`, que
deben seguir [Conventional Commits](https://www.conventionalcommits.org/):
`feat:` sube la versión menor, `fix:` el parche y `feat!:`/`BREAKING CHANGE:`
la mayor (antes de 1.0, la menor). Los PR se integran con **Squash and
merge** y su título es el mensaje del commit; `pr-title.yml` lo exige.

1. Cada push a `master` actualiza el PR `chore: release X.Y.Z` con el
   CHANGELOG y la versión nueva en `version.txt` y `server/pyproject.toml`.
2. Mergear ese PR crea el tag `vX.Y.Z` y la GitHub Release.
3. La release publica la imagen `ghcr.io/<owner>/visitacampo-api:X.Y.Z` con
   SBOM y atestación de procedencia, y `deploy.yml` la despliega si el
   environment `production` está configurado
   ([`docs/operacion/despliegue.md`](docs/operacion/despliegue.md)). El APK
   lo publica la release de SUNO_client.

Configuración que hace un admin una sola vez: *Settings → Actions → Allow
GitHub Actions to create and approve pull requests*, el environment
`release` (restringido a `master`; opcionalmente con `RELEASE_PLEASE_TOKEN`)
y el environment `production` con los secretos de despliegue.

**Contrato de la API.** `server/openapi/v1.yaml` y `server/openapi/fixtures/`
son la fuente de verdad. La app usa una copia fijada en su `contract/`: un
cambio del contrato aquí lleva un PR en SUNO_client que actualice esa copia
([`server/openapi/README.md`](server/openapi/README.md)).

## Estilo de commits

Ver [`README.md`](README.md#convención-de-commits): mensajes descriptivos en
modo imperativo, y como mínimo un commit por paso completado del plan.
