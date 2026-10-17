# Desarrollo seguro: OWASP Top 10:2025 e ISO/IEC 27001:2022

Qué control cubre cada riesgo del [OWASP Top 10:2025](https://owasp.org/Top10/2025/), qué prueba **automatizada** lo verifica en cada PR y qué queda pendiente. Es la evidencia de los controles de desarrollo del Anexo A de ISO/IEC 27001:2022 (tabla al final).

Las decisiones de diseño (RD-01…RD-16, ADR-001…013) están en [`docs/08-arquitectura-propuesta-visitacampo.md`](../08-arquitectura-propuesta-visitacampo.md). Los workflows citados están en [`.github/workflows/`](../../.github/workflows/).

**Regla de mantenimiento**: un PR que añade, quita o desactiva un gate actualiza este documento en el mismo PR.

## Pipeline de seguridad y calidad

| Gate | Workflow | Cuándo | Bloquea |
|---|---|---|---|
| ruff, mypy, `dart analyze` | `backend.yml`, `mobile.yml` | PR, `master` | Sí |
| pytest contra PostgreSQL real + cobertura de ramas ≥ 90 % | `backend.yml` | PR, `master` | Sí |
| `flutter test` + cobertura de líneas ≥ 75 % | `mobile.yml` | PR, `master` | Sí |
| Sistema de diseño de la app: sin colores, estilos, medidas ni textos sueltos en pantallas | `mobile.yml` (`lint_ui_tokens.sh`) | PR, `master` | Sí |
| `check --deploy --fail-level WARNING` con `config.settings.prod` | `backend.yml` | PR, `master` | Sí |
| Bandit (SAST por patrones) | `backend.yml` | PR, `master` | Sí |
| CodeQL `security-and-quality` (Python + workflows) | `codeql.yml` | PR, `master`, semanal | Sí (alertas en Code scanning) |
| Semgrep: packs OWASP Top 10, Python, Django, security-audit, secrets, Dockerfile y GitHub Actions | `security.yml` | PR, `master`, semanal | Sí (alertas en Code scanning) |
| zizmor (seguridad de GitHub Actions) | `backend.yml` | PR, `master` | Sí |
| hadolint (Dockerfile) | `backend.yml` | PR, `master` | Sí |
| grype sobre el SBOM de la imagen: CVE altas con parche (paquetes Debian + Python) | `backend.yml` | PR, `master` | Sí |
| pip-audit + osv-scanner (todos los lockfiles) | `backend.yml`, `security.yml` | PR, `master`, semanal | Sí |
| Dependency Review: CVE ≥ moderada o licencia copyleft en dependencias nuevas | `dependency-review.yml` | PR | Sí |
| Lockfiles coherentes con sus fuentes | `backend.yml` | PR, `master` | Sí |
| gitleaks sobre todo el historial | `security.yml` | PR, `master`, semanal | Sí |
| Lint anti-bypass TLS (RD-01) | `mobile.yml` | PR, `master` | Sí |
| APK release firmado (no debug) y con `INTERNET` | `mobile.yml` | PR, `master` | Sí |
| DAST: cabeceras/HSTS/redirect HTTPS del stack real | `dast.yml` | PR, `master`, semanal | Sí |
| DAST: Schemathesis `not_a_server_error` + `ignored_auth` | `dast.yml` | PR, `master`, semanal | Sí |
| DAST: OWASP ZAP API scan (reglas FAIL en `infra/zap/api-scan-rules.tsv`) | `dast.yml` | PR, `master`, semanal | Sí |
| DAST: conformidad completa con el contrato | `dast.yml` | PR, `master`, semanal | No, hasta cerrar [#3](https://github.com/Victor-Pulido/Seguimiento_Proyectos/issues/3) |
| Título de PR en Conventional Commits | `pr-title.yml` | PR | Sí |
| Simulacro de restauración de backup (cifrado age, datos idénticos tras borrar la BD) | `backup.yml` | PR, `master`, semanal | Sí |
| Monitoreo: `promtool`/`amtool`, pruebas unitarias de alertas, stack + ApiDown de punta a punta | `monitoring.yml` | PR, `master` | Sí |
| Vigilancia externa de producción (abre/cierra issue `incidente`) | `uptime.yml` | cada 15 min | — (inactivo sin `PRODUCTION_URL`) |
| OpenSSF Scorecard | `scorecard.yml` | `master`, semanal | No (informa) |
| SBOM (syft, CycloneDX + SPDX) de la imagen y de la app; CycloneDX de los requirements | `backend.yml`, `mobile.yml` | PR, `master` (artefacto, 90 días) | — |
| SBOM adjuntos a cada release + atestaciones firmadas (procedencia SLSA y SBOM) de la imagen y del APK | `release.yml` | release | — |

## Por categoría

### A01:2025 Broken Access Control

- **Controles**: aislamiento por organización con `scoped_qs(ctx, Model)` y *deny-by-default* (RD-11, ADR-011); `RequestContext` inmutable resuelto una vez (ADR-013); recurso de otro tenant ⇒ `404`; FK compuesta que impide que un adjunto cruce organizaciones; nombres de archivo generados localmente (RD-06).
- **Pruebas automatizadas**: test de arquitectura por AST que falla si un módulo de producción toca un manager sin scoping (`core/tests/test_architecture.py`); tests de políticas y scoping; traversal con 15 familias de entrada hostil (`attachments/tests/test_validation.py`); Schemathesis `ignored_auth`; ZAP reglas 6, 7, 40046 en FAIL; CodeQL.
- **Pendiente**: DAST con dos organizaciones para probar IDOR entre tenants de punta a punta.

### A02:2025 Security Misconfiguration

- **Controles**: `prod.py` con HSTS, `SECURE_SSL_REDIRECT`, cookies seguras; Caddy sobrescribe `X-Forwarded-*` (RD-15); imagen con usuario no root (UID 10001) y código de solo lectura; Android sin backup ni tráfico en claro.
- **Pruebas automatizadas**: `check --deploy --fail-level WARNING` contra prod; `dast.yml` verifica HSTS, `nosniff`, ausencia de versión en `Server` y redirección HTTP→HTTPS en el stack real; hadolint; zizmor; ZAP 10035/10021/40040 en FAIL.
- **Resuelto**: `/admin/` solo desde `CADDY_ADMIN_ALLOWED_IPS` (404 fuera); `USE_X_FORWARDED_HOST=False` (RD-15); `SECRET_KEY` obligatoria y fuerte en prod. Verificado en `dast.yml` y `test_prod_settings.py`.
- **Pendiente**: MFA en el Admin.

### A03:2025 Software Supply Chain Failures

- **Controles**: lockfiles con hashes para todo el árbol Python (`--require-hashes` en CI y Docker) y `pubspec.lock` con `--enforce-lockfile`; imágenes base y de servicios fijadas por digest; actions fijadas por SHA; herramientas de CI descargadas con versión y SHA-256 verificados; Dependabot semanal; SBOM.
- **Pruebas automatizadas**: pip-audit, osv-scanner (también semanal), Dependency Review, grype sobre la imagen, `check_lockfiles.py`, zizmor (impostor commits, actions vulnerables), Scorecard.
- **Pendiente**: imágenes del override de desarrollo (`minio`, `mailhog`) en `:latest` (solo dev).

### A04:2025 Cryptographic Failures

- **Controles**: Argon2id (ADR-008); JWT EdDSA con algoritmo fijado y claims obligatorios; refresh `selector.verifier` con solo `sha256(verifier)` en BD y comparación en tiempo constante (RD-09); SQLCipher con clave en Keystore/Keychain (ADR-002); TLS estricto sin bypass (RD-01); IP de auditoría como HMAC.
- **Pruebas automatizadas**: lint RD-01 + test que levanta un servidor HTTPS autofirmado y exige el rechazo; tests de tokens; smoke test de cifrado SQLCipher; HSTS verificado en DAST.
- **Pendiente**: rotación de la clave de firma JWT con dos claves vivas (Paso 15); pinning SPKI con consentimiento (`TrustStore`, Paso 16).

### A05:2025 Injection

- **Controles**: SQL siempre parametrizado (solo identificadores con `quote_name` se interpolan); validación Pydantic v2 en el borde; formularios como DSL declarativo sin `eval` (RD-06, ADR-012).
- **Pruebas automatizadas**: Bandit (supresiones B608 revisadas, con justificación en el código); Semgrep `p/owasp-top-ten`; CodeQL (flujo de datos fuente→sumidero); ZAP con todas las reglas de inyección en FAIL; test por AST de que `forms/` no usa `eval`/`exec`/`pickle`; tests de expresiones hostiles en plantillas.

### A06:2025 Insecure Design

- **Controles**: modelado de amenazas derivado de una auditoría (doc 07 → RD-01…16) y ADRs con alternativas descartadas; contrato OpenAPI como fuente de verdad (RD-03); plan por pasos con criterios de salida.
- **Pruebas automatizadas**: Schemathesis contra el contrato (conformidad completa, informativa); tests de concurrencia (CAS, `ON CONFLICT`, advisory locks, kill -9 del worker).
- **Pendiente**: desviaciones de contrato en [#3](https://github.com/Victor-Pulido/Seguimiento_Proyectos/issues/3).

### A07:2025 Authentication Failures

- **Controles**: bloqueo tras 5 fallos por usuario+IP (django-axes); `401` genérico sin oráculo de enumeración; rotación de refresh con compare-and-set (RD-10); refresh atado al dispositivo; sesión cortada al perder la membresía.
- **Pruebas automatizadas**: `authn/tests/` (lockout, rotación, robo desde otro dispositivo, logout por dispositivo); Schemathesis `ignored_auth`.
- **Pendiente**: detección de reuso con revocación de familia (Paso 15); MFA en el back-office.

### A08:2025 Software or Data Integrity Failures

- **Controles**: releases con atestación de procedencia SLSA y SBOM firmadas con Sigstore (`release.yml`); APK firmado con llave de subida fuera del repo; adjuntos inmutables direccionados por contenido con checksum certificado por el object storage (RD-08, ADR-007); sync con CAS por versión.
- **Pruebas automatizadas**: job `android-release` (firma no debug); tests de presign/commit con checksum incorrecto; tests de idempotencia y conflicto del `SyncEngine`.
- **Verificación manual de un artefacto**: `gh attestation verify oci://ghcr.io/<owner>/visitacampo-api:<versión> --repo Victor-Pulido/Seguimiento_Proyectos`.
- **Pendiente**: protección de rama en `master` (revisión obligatoria + checks requeridos).

### A09:2025 Security Logging and Alerting Failures

- **Controles**: `AuditEvent` para login, denegación, conflicto de dispositivo, logout y refresh rechazado (`core/audit.py`); `AccessFailureLog` de axes para contraseñas incorrectas; redacción de secretos en logs y en `job.output` (RD-12); traza de solo lectura en el Admin.
- **Pruebas automatizadas**: `authn/tests/test_audit.py`; `jobs/tests/test_scrub.py` (incluida la regresión `authorization: Bearer`).
- **Alertas y retención**: monitoreo con alertas por correo y vigilancia externa (ADR-014, `infra/monitoring/`); purga automática según [`retencion.md`](retencion.md).
- **Pendiente**: Sentry para errores de aplicación.

### A10:2025 Mishandling of Exceptional Conditions

- **Controles**: manejadores de error para que ningún `/v1/*` devuelva el 500 desnudo de Django; rechazo de surrogates Unicode y floats no finitos; profundidad máxima en el scrubber; en el cliente, parseo a tipos sellados con `Ok/Err` y cuarentena en vez de crash (RD-02/RD-03).
- **Pruebas automatizadas**: Schemathesis `not_a_server_error` (bloqueante); ZAP 90022 en FAIL; tests de `api_errors`, payloads y parseo estricto del `SyncEngine`.

## Correspondencia con ISO/IEC 27001:2022 (Anexo A)

| Control | Evidencia en este repositorio |
|---|---|
| A.5.15 Control de acceso | `scoped_qs`, políticas puras, environment `release` para secretos de firma |
| A.5.32 Derechos de propiedad intelectual | Dependency Review con licencias copyleft denegadas |
| A.8.4 Acceso al código fuente | gitleaks; `.github/CODEOWNERS`; pendiente: protección de rama que lo haga obligatorio |
| A.8.8 Gestión de vulnerabilidades técnicas | pip-audit, osv-scanner, grype, Dependency Review, Dependabot (espera de 7 días), escaneos semanales; excepciones con caducidad en [`excepciones.md`](excepciones.md) |
| A.8.9 Gestión de la configuración | lockfiles con hashes, digests, SHA de actions, `check --deploy` |
| A.8.10 Eliminación de información | Periódico `security.purge_logs`, [`retencion.md`](retencion.md) |
| A.8.12 Prevención de fuga de datos | scrubber RD-12, IP como HMAC, sin backup Android, gitleaks |
| A.8.13 Copias de seguridad | `infra/backup/` (cifrado age, rotación) + simulacro de restauración `backup.yml` |
| A.8.15 Registro | `AuditEvent`, `AccessFailureLog`, logs JSON de Caddy |
| A.8.16 Actividades de monitorización | `infra/monitoring/` (ADR-014) + `uptime.yml`; alertas probadas en `monitoring.yml` |
| A.8.24 Uso de criptografía | Argon2id, EdDSA, SQLCipher, TLS estricto, firma de releases |
| A.8.25 Ciclo de vida de desarrollo seguro | este documento, CONTRIBUTING.md, gates de CI |
| A.8.26 Requisitos de seguridad de las aplicaciones | RD-01…RD-16 en doc 08 |
| A.8.27 Arquitectura y principios de ingeniería seguros | ADR-001…013 |
| A.8.28 Codificación segura | Bandit, Semgrep, CodeQL, zizmor, hadolint, ruff/mypy, `dart analyze`; plantilla de PR con checklist de seguridad |
| A.8.29 Pruebas de seguridad en desarrollo y aceptación | tests de seguridad, DAST (Schemathesis + ZAP), cobertura mínima |
| A.8.31 Separación de entornos | settings dev/prod; DAST con secretos efímeros; `.env` fuera de git |
| A.8.32 Gestión de cambios | PR + CI obligatorio, Conventional Commits, release-please con CHANGELOG; pendiente: revisión por una persona distinta del autor exigida por protección de rama |
