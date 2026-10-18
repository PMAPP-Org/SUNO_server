# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/), versionado [SemVer](https://semver.org/lang/es/).

Cada release la genera automáticamente [release-please](https://github.com/googleapis/release-please) (`.github/workflows/release.yml`) a partir de los mensajes de commit en `master`, que siguen [Conventional Commits](https://www.conventionalcommits.org/es/v1.0.0/). No se edita a mano: lo que deba aparecer aquí se describe en el título del PR (`feat: …`, `fix: …`), que con *Squash and merge* es el mensaje del commit.

## [0.6.0](https://github.com/PMAPP-Org/SUNO_server/compare/v0.5.0...v0.6.0) (2026-10-18)


### Agregado

* **basemaps:** offline basemap packages per organization (Fase B) ([#27](https://github.com/PMAPP-Org/SUNO_server/issues/27)) ([5792ce7](https://github.com/PMAPP-Org/SUNO_server/commit/5792ce7fb41b2acde927c2c55e65e54f89ab53f3))


### Corregido

* **web:** separate public platform access from admin ([#28](https://github.com/PMAPP-Org/SUNO_server/issues/28)) ([88764b9](https://github.com/PMAPP-Org/SUNO_server/commit/88764b92c997ba97fc19ed76a704289f04708cd4))

## [0.5.0](https://github.com/PMAPP-Org/SUNO_server/compare/v0.4.0...v0.5.0) (2026-10-18)


### Agregado

* **authn:** rotate the JWT signing key with two live keys (ADR-008) ([#20](https://github.com/PMAPP-Org/SUNO_server/issues/20)) ([816df5b](https://github.com/PMAPP-Org/SUNO_server/commit/816df5b022ad32b3544a404ed203777bb0a23812))
* **privacy:** record the data subject's authorization (Ley 1581, art. 9) ([#25](https://github.com/PMAPP-Org/SUNO_server/issues/25)) ([40ff3c4](https://github.com/PMAPP-Org/SUNO_server/commit/40ff3c4960948e2162e3b8efeb50f7d8868faf84))
* **ui:** add SUNO website and branded administration ([#24](https://github.com/PMAPP-Org/SUNO_server/issues/24)) ([fbc6cab](https://github.com/PMAPP-Org/SUNO_server/commit/fbc6cab7f9fe64f6506b65117529a17e942837a5))


### Corregido

* **api:** contract conformance, two server defects, blocking DAST conformance and IDOR check ([#23](https://github.com/PMAPP-Org/SUNO_server/issues/23)) ([3a71dba](https://github.com/PMAPP-Org/SUNO_server/commit/3a71dba8fd637a6a73532fd36252e1d194a07db6))
* **ci:** deploy secrets for the reusable workflow; regenerate runtime lock ([#18](https://github.com/PMAPP-Org/SUNO_server/issues/18)) ([44b9278](https://github.com/PMAPP-Org/SUNO_server/commit/44b9278d9a9dc57740532c9f7723e4b3607619c8))


### Documentación

* **plan:** Ley 1581 mechanics done; notice text pending legal review ([#26](https://github.com/PMAPP-Org/SUNO_server/issues/26)) ([a1acb18](https://github.com/PMAPP-Org/SUNO_server/commit/a1acb185da8164a4f61bf8955179c7ca9f322d7c))

## [0.4.0](https://github.com/PMAPP-Org/SUNO_server/compare/v0.3.0...v0.4.0) (2026-10-18)


### Agregado

* **security:** findings map, isolation and query-budget tests, load test (Paso 25) ([#17](https://github.com/PMAPP-Org/SUNO_server/issues/17)) ([9202f95](https://github.com/PMAPP-Org/SUNO_server/commit/9202f9511b8e8ffa8249b90dd39920582cf568f3))


### Documentación

* **plan:** Paso 16 and 24 status; OWASP A04 with TrustStore ([#16](https://github.com/PMAPP-Org/SUNO_server/issues/16)) ([ceb937d](https://github.com/PMAPP-Org/SUNO_server/commit/ceb937d832559edde41fbb98cd998f9a7524337e))


### Dependencias y build

* **deps:** bump the python group in /server with 2 updates ([#15](https://github.com/PMAPP-Org/SUNO_server/issues/15)) ([f0f402a](https://github.com/PMAPP-Org/SUNO_server/commit/f0f402abf4c88e8a79eab14d2a8ceba7c020f6e3))
* **deps:** safe Python bumps with regenerated locks; defer major migrations ([#13](https://github.com/PMAPP-Org/SUNO_server/issues/13)) ([7a58781](https://github.com/PMAPP-Org/SUNO_server/commit/7a587811225bc6a71b6aee2c36ce7e489b8cafc1))

## [0.3.0](https://github.com/PMAPP-Org/SUNO_server/compare/v0.2.0...v0.3.0) (2026-10-17)


### ⚠ BREAKING CHANGES

* split the server out of the monorepo into SUNO_server

### Agregado

* **admin:** allow disabling admin OTP during development (EXC-003) ([#11](https://github.com/PMAPP-Org/SUNO_server/issues/11)) ([1a36235](https://github.com/PMAPP-Org/SUNO_server/commit/1a36235e6d10a4c35017df3c9442ef1ba1113ce9))
* **backoffice:** org-scoped Django Admin with review, export and data rights (Paso 24) ([#12](https://github.com/PMAPP-Org/SUNO_server/issues/12)) ([1141f9d](https://github.com/PMAPP-Org/SUNO_server/commit/1141f9d0473bc2eef0f9f41ce19a89309a326b77))
* offline visit capture with photos and signature (Paso 22) ([#21](https://github.com/PMAPP-Org/SUNO_server/issues/21)) ([8085204](https://github.com/PMAPP-Org/SUNO_server/commit/80852049d2fbe3ad1491ffc91a14dac635766ab8))


### Documentación

* **plan:** Paso 18 status (client upload queue, background on device pending) ([#9](https://github.com/PMAPP-Org/SUNO_server/issues/9)) ([fe2d91c](https://github.com/PMAPP-Org/SUNO_server/commit/fe2d91cf570a4d02db54e17978d978d14cc75d31))
* **plan:** remaining work verified against the code; Paso 23 status ([#10](https://github.com/PMAPP-Org/SUNO_server/issues/10)) ([ec16fb0](https://github.com/PMAPP-Org/SUNO_server/commit/ec16fb066a9a6d545b832272c8c7ce3b2c10ca4c))
* **sgsi:** point to the transferred issues ([#3](https://github.com/PMAPP-Org/SUNO_server/issues/3) → [#6](https://github.com/PMAPP-Org/SUNO_server/issues/6), [#5](https://github.com/PMAPP-Org/SUNO_server/issues/5) → [#7](https://github.com/PMAPP-Org/SUNO_server/issues/7)) ([#8](https://github.com/PMAPP-Org/SUNO_server/issues/8)) ([256b18b](https://github.com/PMAPP-Org/SUNO_server/commit/256b18b2eb647714bbf42f0fd5185479e8a631cd))


### Dependencias y build

* split the server out of the monorepo into SUNO_server ([190dc2f](https://github.com/PMAPP-Org/SUNO_server/commit/190dc2faddb467983ebace636fa58e6549e12ac3))

## [0.2.0](https://github.com/PMAPP-Org/Seguimiento_Proyectos/compare/v0.1.0...v0.2.0) (2026-10-17)


### Agregado

* **app:** design system, Spanish l10n and a gate against hardcoded UI ([649eedb](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/649eedb680315d5733e4db4696c42f476e01ecad))
* **app:** dynamic form renderer with server parity (Paso 21) ([123fe00](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/123fe00ee0bbeb2ff45ff49f91ca8cea40b8b640))
* **app:** dynamic form renderer with server parity (Paso 21) ([b337ac2](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/b337ac25610e899a135300152f4fb9ad6a28619f))
* **app:** sistema de diseño, textos en ARB y gate contra UI hardcodeada ([e0d6839](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/e0d6839516cde6a2e07a5522b7ca6ea97f0c4d83))
* **ci:** add Semgrep OWASP SAST, PR template, CODEOWNERS and CHANGELOG baseline ([657968d](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/657968de1695f364bd81c48a3fd2ef2556634988))
* **ci:** Semgrep OWASP, plantilla de PR, CODEOWNERS y CHANGELOG ([81085a2](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/81085a2e83ca13a0763f56b5793ffaad7e4a9fb9))
* endurecimiento de prod, backups cifrados con simulacro, retención y monitoreo del servidor ([0084c34](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/0084c343c54503c819ba3cce9d5b3bd1c35d88fd))
* **infra:** encrypted, rotated backups with a restore drill in CI ([cd9382c](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/cd9382c981b5e4fb6637ca1b59508c105b1fce45))
* **infra:** hardened VPS provisioning and automated deploy with rollback ([3405f27](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/3405f27e55b0a321ba83875b9bb92809cb0db004))
* **infra:** hardened VPS provisioning and automated deploy with rollback ([bf2a15e](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/bf2a15efdfac487eeb427e62af10b22e39ec495d))
* **infra:** server health monitoring with tested alerts and external uptime checks ([2b9da59](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/2b9da59f87f10ea684ac2480f70e839b6fc9a848))
* merge app design system ([#8](https://github.com/PMAPP-Org/Seguimiento_Proyectos/issues/8)) ([e0d6839](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/e0d6839516cde6a2e07a5522b7ca6ea97f0c4d83))
* merge auth audit and Android release ([#2](https://github.com/PMAPP-Org/Seguimiento_Proyectos/issues/2)) ([47212de](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/47212deb1753937d8afba44ec1ca7a2a4a48e3e7))
* merge auth hardening and admin MFA ([#11](https://github.com/PMAPP-Org/Seguimiento_Proyectos/issues/11)) ([219512b](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/219512b6c0e800b4dfdc7cf6b2530c83b7a8f43f))
* merge dynamic form renderer ([#13](https://github.com/PMAPP-Org/Seguimiento_Proyectos/issues/13)) ([123fe00](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/123fe00ee0bbeb2ff45ff49f91ca8cea40b8b640))
* merge production hardening and backups ([#6](https://github.com/PMAPP-Org/Seguimiento_Proyectos/issues/6)) ([0084c34](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/0084c343c54503c819ba3cce9d5b3bd1c35d88fd))
* merge provisioning and deployment ([#12](https://github.com/PMAPP-Org/Seguimiento_Proyectos/issues/12)) ([3405f27](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/3405f27e55b0a321ba83875b9bb92809cb0db004))
* merge repository governance and Semgrep ([#7](https://github.com/PMAPP-Org/Seguimiento_Proyectos/issues/7)) ([81085a2](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/81085a2e83ca13a0763f56b5793ffaad7e4a9fb9))
* **server:** enforce security-log retention and run the worker in compose ([d1ea48e](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/d1ea48ec53e7f325498649a76115362083191be5))
* **server:** mandatory MFA for Django Admin (TOTP + backup codes) ([4f85655](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/4f85655671f98237af51b7f9934422ac5c1b42de))
* **server:** Paso 15 (reuso de tokens, rate limiting) y MFA obligatorio en el Admin ([219512b](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/219512b6c0e800b4dfdc7cf6b2530c83b7a8f43f))
* **server:** refresh-token reuse detection, grace window and API rate limiting (Paso 15) ([661702b](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/661702b9d5b1c591c801a9c17e4689a7aed8a106))
* **server:** write an audit trail for authentication events ([14b3070](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/14b3070a187fb68cdf213d253a86ed80fedc220b))


### Corregido

* **app:** make the Android release build publishable and keep data on-device ([c0c1a41](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/c0c1a419607a295a62ad17aa977ea82079fc7849))
* **ci:** clear Semgrep OWASP findings and pin Semgrep with a hashed lock ([2256edd](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/2256edda6df1353daa10c4d45ebba49f82648502))
* **ci:** remove a stray ')' in the DAST login step and lint workflows with actionlint ([e757d82](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/e757d82acce6e2bd22e71e5f067b550bb2979bfa))
* **ci:** repair the worker check and compare restored data without pg_dump's restrict key ([2e58428](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/2e5842817c0c04fc7979bbd6d50f0679b662513d))
* **infra:** make the api healthcheck speak to gunicorn the way Caddy does ([b5cce2c](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/b5cce2c4421ca7868dfd5199e92ab48df6cf724a))
* **infra:** record the running image in infra/.env ([4b9994d](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/4b9994d5db34f132bb647bf946700706c4bad1e4))
* **infra:** run compose from infra/ so COMPOSE_FILE paths resolve; lint fixes ([710896d](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/710896d5a98d18c9acc748c5cbf27171c066e9d3))
* **infra:** stop sending HSTS twice and harden API response headers ([addface](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/addface0c6f488c026a728a32e8cd6f62357f5d8))
* **server:** harden production settings and lock /admin to allowed origins ([2d62da6](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/2d62da6359c9e81d6fecc934ad411c02bc0367df))
* **server:** make ruff, mypy and check --deploy pass on master ([d4d9fe2](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/d4d9fe2e9031b79c0c71dbe919615b7edb57241f))
* **server:** redact the credential after an Authorization scheme in logs ([c1dbb0e](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/c1dbb0e386d65e7f1c8c17386ca9b2bcce6e1588))


### Documentación

* document lockfile workflow and security gates in CONTRIBUTING ([df276dd](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/df276dd616572d351507ff4aef4ec195c8c513ab))
* map OWASP Top 10:2025 and ISO 27001 controls to automated gates ([82fb007](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/82fb007b2e492818220191c3bf035a19612fa9ca))
* merge ISMS package ([#9](https://github.com/PMAPP-Org/Seguimiento_Proyectos/issues/9)) ([6016be3](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/6016be33c91192706207e0292f3cc4784a622bc0))
* merge operations runbooks ([#10](https://github.com/PMAPP-Org/Seguimiento_Proyectos/issues/10)) ([1ded006](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/1ded006198cb3a28c3ae719896170f694f7cb7ce))
* **ops:** runbooks de operación, SECURITY.md y seguimiento al día ([1ded006](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/1ded006198cb3a28c3ae719896170f694f7cb7ce))
* **ops:** runbooks, SECURITY.md and progress tracking brought up to date ([8105b28](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/8105b2802cda1bb5eafbecacb99a4004c7582ed5))
* **security:** draft ISMS pack - SoA, risk register, policies, assets and personal data ([7ae282c](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/7ae282c1c39e567b45536e5b6d408881b5fdb3c5))
* **security:** paquete del SGSI — SoA, riesgos, políticas, activos y datos personales ([6016be3](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/6016be33c91192706207e0292f3cc4784a622bc0))


### Dependencias y build

* force LF for shell scripts, Dockerfile, Caddyfile and lockfiles ([f568f0c](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/f568f0cd8125c4416a87184034e43530dd07d031))
* run the API as a numeric non-root user over read-only code ([92a048a](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/92a048aaa1cbbde6f38e383a4aa9461e8468a020))
* **server:** hash-locked dependencies and digest-pinned base image ([355e7bc](https://github.com/PMAPP-Org/Seguimiento_Proyectos/commit/355e7bc5d854bfe1eb7cd64c781d04f4e77823ce))

## [0.1.0] (2026-08-07)

Línea base de versionado: el estado del repositorio en el commit `dd2d536`, anterior a la línea base de seguridad ISO/IEC 27001 y OWASP Top 10:2025. No tiene tag publicado. La primera release automática enumerará, a partir de aquí, todos los cambios posteriores.

### Agregado

- Backend Django 5 + Django Ninja: autenticación con JWT EdDSA, refresh tokens hasheados (`selector.verifier`) con rotación y rate limiting de login (django-axes); sync push/pull con cursor incremental y tombstones; adjuntos direccionados por contenido con presign/commit verificado; formularios declarativos con meta-schema versionado; cola de trabajos con lease, heartbeat y reaper (Paso 19, sin revisión previa).
- App Flutter: almacén local cifrado (Drift + SQLCipher) con un único escritor, motor de sync (outbox, CAS, cursor), autenticación cliente con TLS estricto, login, captura con GPS y una foto, y sincronización manual.
- Infraestructura: Docker Compose con Caddy y PostgreSQL, imagen del backend, contrato OpenAPI 3.1 y CI inicial.

Para el detalle previo a esta versión, ver `git log`; no se reconstruye retroactivamente como changelog.
