# Registro de riesgos de seguridad de la información

> **BORRADOR — pendiente de aprobación por la dirección.** ISO/IEC 27001:2022 cláusulas 6.1.2 (evaluación) y 6.1.3 (tratamiento). Los responsables se asignan al aprobar el registro.

**Método**
- Probabilidad (P) e impacto (I) de 1 a 5; nivel = P × I.
- Escala del nivel: **1–6 bajo, 8–12 medio, 15–25 alto**.
- La valoración residual es la que queda con los controles ya implementados.
- Tratamientos posibles: mitigar, aceptar (con firma de la dirección), transferir o evitar.
- El registro se revisa al menos cada seis meses, tras cada incidente y ante cada cambio de arquitectura (ADR nuevo).

**Origen:** los riesgos de la primera tabla (numeración R-01…R-16, reservada para ellos) vienen de la auditoría de código de la arquitectura de referencia ([`docs/07`](../07-hallazgos-revision-codigo.md)), convertidos en las restricciones de diseño RD-01…RD-16 ([`docs/08`](../08-arquitectura-propuesta-visitacampo.md) §3). Se listan aparte porque **su tratamiento está implementado y probado en CI**. Los riesgos R-17 y siguientes son los que siguen abiertos.

## Riesgos de diseño ya tratados (RD-01…RD-16)

| ID | Riesgo | Inherente (P×I) | Tratamiento | Evidencia | Residual |
|---|---|---|---|---|---|
| R-01 | Interceptación del tráfico app↔servidor por aceptar certificados inválidos | 4×5 = 20 | Mitigar: TLS estricto sin bypass (RD-01) | Lint RD-01 en CI + test con servidor autofirmado | 1×5 = 5 |
| R-02 | Caída o corrupción de la app por datos de sync malformados | 3×3 = 9 | Mitigar: tipos sellados y parseo estricto (RD-02/03) | Tests de `SyncEngine`; Schemathesis `not_a_server_error` | 1×3 = 3 |
| R-03 | Pérdida de datos locales por escrituras concurrentes | 3×4 = 12 | Mitigar: un único escritor (RD-04) | Tests de BD local | 1×4 = 4 |
| R-04 | Ejecución de código o path traversal vía formularios o nombres de archivo | 3×5 = 15 | Evitar: formularios como datos, nombres generados (RD-06) | Test de AST sin `eval`; 15 familias de traversal | 1×5 = 5 |
| R-05 | Acceso a datos de otra organización | 3×5 = 15 | Mitigar: `scoped_qs` + deny-by-default (RD-11) | Test de arquitectura; Schemathesis `ignored_auth` | 1×5 = 5 |
| R-06 | Robo de sesiones por tokens legibles en BD | 3×4 = 12 | Mitigar: solo hashes, rotación con CAS (RD-09/10) | Tests de `authn` | 1×4 = 4 |
| R-08 | Pérdida de adjuntos por condiciones de carrera | 3×4 = 12 | Evitar: blobs inmutables por contenido (RD-08) | Tests de concurrencia de adjuntos | 1×4 = 4 |
| R-09 | Secretos expuestos en logs o salida de jobs | 3×4 = 12 | Mitigar: scrubber con allow-list (RD-12) | Tests de scrub (incluida la regresión `Bearer`) | 1×4 = 4 |
| R-10 | Spoofing de HTTPS detrás del proxy | 2×4 = 8 | Mitigar: Caddy sobrescribe cabeceras (RD-15) | DAST: `X-Forwarded-Proto` falso sigue redirigido | 1×4 = 4 |

*(RD-07, RD-13, RD-14 y RD-16 son de rendimiento, disponibilidad o consistencia y están cubiertos por los tests de concurrencia, de kill -9 del worker y de presupuesto de queries.)*

## Riesgos abiertos

| ID | Riesgo | P×I | Nivel | Tratamiento propuesto | Estado | Responsable |
|---|---|---|---|---|---|---|
| R-17 | **Cambios sin revisión independiente**: hoy cualquiera con permiso de escritura puede integrar en `master` | 4×4 = 16 | Alto | Mitigar: protección de rama con aprobación de otra persona y checks requeridos | Pendiente de Admin (issue #5) | Por designar |
| R-18 | **Pérdida o filtración de la llave privada de backups (age)**: sin ella los backups son irrecuperables; con ella filtrada son legibles | 2×5 = 10 | Medio | Mitigar: gestor de secretos + copia offline, nunca en el servidor ([`politicas.md`](politicas.md) §5) | Pendiente de generar y custodiar | Por designar |
| R-19 | **Pérdida de la llave de subida de Android**: no se pueden publicar actualizaciones de la app | 2×4 = 8 | Medio | Mitigar: custodia como R-18; Play App Signing | Pendiente | Por designar |
| R-20 | **Acceso no autorizado al back-office** | 3×5 = 15 → residual 1×5 = 5 | Bajo (residual) | Mitigar: restricción por origen + MFA obligatorio (TOTP, alta solo desde el servidor) | Implementado | Por designar |
| R-21 | **Caída prolongada por servidor único** (VPS) | 3×3 = 9 | Medio | Aceptar con mitigación: backups fuera del VPS, monitoreo y vigilancia externa; reevaluar con más clientes (ADR-010) | Requiere aceptación formal | Dirección |
| R-22 | **Repositorio público**: la arquitectura y los hallazgos de seguridad son visibles para cualquiera | 3×3 = 9 | Medio | Decidir: pasar a privado, o aceptar (no hay secretos; seguridad sin depender de la oscuridad) | Decisión pendiente | Dirección |
| R-23 | **Paquete malicioso en la cadena de suministro** | 2×5 = 10 | Medio | Mitigar: hashes, digests, espera de 7 días, Dependency Review, SBOM, osv-scanner semanal | Implementado | — |
| R-24 | **Reuso de refresh token robado** | 2×4 = 8 → residual 1×4 = 4 | Bajo (residual) | Mitigar: revocación de toda la familia ante reuso fuera de la ventana de gracia, con auditoría; rate limiting por token (Paso 15) | Implementado | — |
| R-25 | **Incumplimiento de la Ley 1581** (datos de ubicación y fotos sin aviso ni plazo definido) | 3×4 = 12 | Medio | Mitigar: aviso de privacidad en la app, autorización del titular, retención de visitas ([`tratamiento-datos-personales.md`](tratamiento-datos-personales.md)) | Pendiente | Responsable del tratamiento |
| R-26 | **Divergencia del contrato API** entre app y servidor | 3×2 = 6 | Bajo | Mitigar: issue #3; generar el cliente desde OpenAPI | Pendiente | Por designar |
| R-27 | **Teléfono de campo perdido o robado** | 4×3 = 12 | Medio | Mitigar: BD cifrada, tokens en Keystore, revocación de sesión; pendiente MDM y bloqueo obligatorio | Parcial | Por designar |
| R-28 | **Dependencia de un asistente de IA para programar**: cambios voluminosos difíciles de revisar | 3×3 = 9 | Medio | Mitigar: los mismos gates, PR pequeños y revisión humana obligatoria (R-17) | Parcial | Por designar |
| R-29 | **Vulnerabilidad aceptada en herramienta SAST** (EXC-001) | 1×2 = 2 | Bajo | Aceptar hasta 2026-12-31 ([`excepciones.md`](excepciones.md)) | Aceptado (borrador) | Dirección |
| R-30 | **Despliegue de código no autorizado en producción** (llave de despliegue filtrada, imagen manipulada o workflow alterado) | 2×5 = 10 | Medio | Mitigar: llave SSH dedicada solo en el environment `production` (restringido a `master`, con revisores), `known_hosts` fijado, verificación de la procedencia firmada de la imagen antes de desplegar, entradas validadas antes del comando remoto, rotación de la llave si se sospecha filtración | Implementado en código; falta crear el environment (issue #5) | Por designar |
