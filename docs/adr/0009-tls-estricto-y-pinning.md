# ADR-0009: TLS estricto siempre; pinning con consentimiento para servidores propios

- **Estado:** Aceptada
- **Fecha:** 2026-07-30

## Contexto

QField ignora los errores TLS en servidores que no son el suyo, lo que permite un MITM trivial (hallazgo CRITICAL de doc 07).

## Decisión

Prohibido cualquier bypass de certificado; un lint de CI falla si aparece fuera del módulo `TrustStore`. Pinning SPKI con dos pines y caducidad para la instancia oficial; TOFU explícito con fingerprint para servidores propios con certificado no confiable; bloqueo si el certificado cambia. Android sin tráfico en claro ni CAs de usuario; iOS ATS sin excepciones. En el servidor: HTTPS obligatorio, HSTS y `X-Forwarded-Proto` sobrescrito por Caddy (RD-15).

## Consecuencias

- Se cierra la clase de ataque CRITICAL.
- Rotar pines exige un procedimiento escrito; los pines reales dependen del dominio oficial.

## Referencias

- [Doc 08 §5, ADR-009](../08-arquitectura-propuesta-suno.md#5-architecture-decision-records).
- SUNO_client PR #8 (`TrustStore`) y `tool/lint_no_tls_bypass.sh`; `infra/Caddyfile`, `server/config/settings/prod.py`.
