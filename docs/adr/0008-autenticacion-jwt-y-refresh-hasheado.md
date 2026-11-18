# ADR-0008: Argon2id, JWT corto y refresh hasheado por dispositivo

- **Estado:** Aceptada
- **Fecha:** 2026-07-30

## Contexto

Corrige los tokens en texto plano, las carreras al invalidar sesiones y la falta de límites de intentos de la referencia. El teléfono pasa semanas sin conexión, pero uno perdido debe poder revocarse.

## Decisión

Argon2id para contraseñas; access token JWT EdDSA de 15 minutos, sin persistir; refresh opaco `selector.verifier` con solo `sha256(verifier)` en la BD; rotación en cada uso con detección de reuso (revoca la familia del dispositivo) bajo `SELECT … FOR UPDATE`; un refresh por dispositivo; rate limiting y respuestas uniformes en el login.

## Consecuencias

- Un volcado de la BD no entrega sesiones; el back-office revoca por dispositivo.
- Rotación de la llave JWT con dos llaves vivas (`authn/keys.py`, PR #20), con runbook en [`../operacion/incidentes.md`](../operacion/incidentes.md).
- Además de `django-axes`, el API usa buckets propios en BD (`core/ratelimit.py`) para login, refresh y presign (Paso 15).

## Referencias

- [Doc 08 §5, ADR-008](../08-arquitectura-propuesta-suno.md#5-architecture-decision-records).
- `server/suno/authn/`. Pasos 7 y 15 (`b4a3334`, `ad245d8`), PR #20.
