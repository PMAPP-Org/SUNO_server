# Políticas de seguridad de desarrollo y operación

> **BORRADOR — pendiente de aprobación por la dirección.** Políticas temáticas (ISO/IEC 27001:2022 A.5.1) del proyecto VisitaCampo. No sustituyen la política general del SGSI de la organización, que debe aprobar la dirección y a la que estas quedan subordinadas. Donde una regla ya la hace cumplir el CI, se indica: la política describe la intención y el CI verifica que se cumple.

## 1. Roles y responsabilidades (A.5.2, A.5.3)

| Rol | Responsabilidad | Persona |
|---|---|---|
| Responsable de seguridad del proyecto | Mantener este documento, el registro de riesgos y las excepciones; coordinar incidentes | Por designar |
| Mantenedores (CODEOWNERS) | Revisar y aprobar PR; ninguno aprueba los suyos (excepción temporal de auto-integración: EXC-002 en [`excepciones.md`](excepciones.md)) | Victor-Pulido, gabriel0820gs-dev |
| Operador de producción | Despliegues, backups, monitoreo, acceso al VPS | Por designar |
| Custodio de llaves | Llave privada de backups (age) y llave de subida de Android | Por designar (dos personas: ninguna llave depende de una sola) |
| Responsable del tratamiento de datos | Avisos de privacidad, plazos de conservación, derechos de titulares | Cada organización cliente; enlace por designar |

## 2. Desarrollo seguro (A.8.25–A.8.29)

1. Todo cambio entra a `master` por PR, con la plantilla completa y el CI en verde ([`CONTRIBUTING.md`](../../CONTRIBUTING.md)).
2. Los requisitos de seguridad son restricciones de diseño (RD-01…RD-16) y cada decisión de arquitectura queda en un ADR (doc 08).
3. Los gates de seguridad no se desactivan. Un falso positivo se suprime por regla exacta y con justificación junto al código; una vulnerabilidad aceptada va a [`excepciones.md`](excepciones.md) con fecha de caducidad.
4. Nunca se versionan secretos. gitleaks revisa todo el historial en cada PR.
5. Las pruebas usan datos sintéticos y nunca datos de producción (A.8.33). El DAST corre contra un entorno efímero, nunca contra producción (A.8.34).
6. El código generado con asistentes de IA tiene el mismo tratamiento que cualquier otro: los mismos gates y revisión humana obligatoria antes de integrarlo (A.8.30, R-28).

## 3. Gestión de cambios (A.8.32)

1. **Aprobación por una persona distinta del autor**, exigida por la protección de rama (pendiente de Admin, issue #7).
2. Título del PR en Conventional Commits. De él salen la versión (SemVer), el tag y el CHANGELOG (release-please).
3. Un cambio que rompe compatibilidad (app instalada, datos o contrato de API) lleva `!` en el título y su plan de migración en el PR.
4. Despliegue solo de artefactos de una release con atestación de procedencia verificada (`gh attestation verify`).
5. **Cambio de emergencia**: se puede integrar con un solo revisor disponible, documentando el motivo en el PR, y se revisa a posteriori en un plazo de 2 días hábiles.

## 4. Gestión de vulnerabilidades técnicas (A.8.8)

Detección automática en cada PR y cada semana: pip-audit, osv-scanner, grype, Dependency Review, Semgrep, CodeQL y Dependabot. **Plazos máximos de corrección** desde que se detecta:

| Severidad (CVSS / la que asigne el escáner) | Plazo | Si no se puede corregir |
|---|---|---|
| Crítica (≥ 9.0) o explotada activamente | 7 días naturales | Mitigación temporal + excepción aprobada por el responsable de seguridad |
| Alta (7.0–8.9) | 30 días | Excepción con caducidad ≤ 90 días |
| Media (4.0–6.9) | 90 días | Excepción con caducidad ≤ 180 días |
| Baja | En el siguiente ciclo de mantenimiento | — |

Una vulnerabilidad que no es explotable en el contexto (p. ej. EXC-001) se documenta como excepción con su análisis, nunca se ignora en silencio.

## 5. Criptografía y gestión de llaves (A.8.24)

1. Algoritmos aprobados:
   - contraseñas con **Argon2id**;
   - firmas de tokens con **Ed25519/EdDSA**;
   - integridad con **SHA-256**;
   - BD local con **SQLCipher** (AES-256);
   - transporte con **TLS 1.2+** (preferido 1.3), sin excepciones (RD-01);
   - backups con **age** (X25519 + ChaCha20-Poly1305).
2. Inventario de secretos, ubicación y rotación en [`inventario-activos.md`](inventario-activos.md) §3.
3. **La llave privada de backups nunca está en el servidor.** Se genera en un equipo de confianza y se guarda en el gestor de secretos y en una copia offline, bajo dos custodios.
4. Ante la sospecha de que un secreto se ha comprometido: rotarlo de inmediato, revocar lo que firmó si aplica, registrarlo como incidente (§8) y revisar el acceso que lo expuso.

## 6. Control de acceso (A.5.15–A.5.18, A.8.2, A.8.5)

1. Mínimo privilegio. Los datos de cada organización solo los ve esa organización (`scoped_qs`, RD-11).
2. **MFA obligatorio** en GitHub para todos los colaboradores y en el Admin de Django (impuesto por el código; alta con `enroll_admin_totp`, solo desde el servidor).
3. El back-office (`/admin/`) y los paneles de monitoreo solo son accesibles desde orígenes autorizados o por túnel SSH.
4. Acceso SSH al VPS solo con llave, sin contraseña y sin `root` (aprovisionamiento pendiente).
5. **Revisión de accesos trimestral**: colaboradores de GitHub, usuarios con `is_staff`/`is_superuser`, llaves SSH autorizadas, tokens de R2 y de GitHub. Se registra la fecha y el resultado.
6. **Baja**: revocar todos los accesos el mismo día en que termina la relación (A.6.5).

## 7. Copias de seguridad y continuidad (A.8.13, A.5.30)

1. Backup diario cifrado de la BD y copia inmutable de los adjuntos en un bucket separado, con rotación de 30 días ([`infra/backup/README.md`](../../infra/backup/README.md)).
2. Objetivos propuestos, a aprobar: **RPO 24 h** (como máximo se pierde un día de datos ya sincronizados; lo no sincronizado sigue en los teléfonos) y **RTO 8 h** (VPS nuevo, restauración y DNS).
3. El simulacro de restauración corre en CI en cada PR y cada semana. Además, **un simulacro real trimestral** sobre un entorno de prueba, registrado con su duración (evidencia del RTO).
4. La alerta `BackupStale`/`BackupMissing` es de severidad crítica.

## 8. Gestión de incidentes (A.5.24–A.5.28, A.6.8)

**Qué es un incidente:** acceso no autorizado, fuga o pérdida de datos, caída del servicio de más de 1 h, compromiso de una llave, teléfono de campo perdido con sesión activa, o una vulnerabilidad crítica explotable en producción.

**Cómo se notifica:**
- Cualquier persona lo comunica de inmediato al responsable de seguridad.
- Desde fuera, por el canal de [`SECURITY.md`](../../SECURITY.md).
- Las alertas críticas y el issue `incidente` de `uptime.yml` son detección automática.

**Severidad y tiempos de respuesta**

| Severidad | Ejemplos | Primera respuesta |
|---|---|---|
| S1 crítica | Fuga de datos personales, llave comprometida, producción caída | 1 h |
| S2 alta | Degradación grave, vulnerabilidad crítica sin explotar | 4 h |
| S3 media | Fallo de backup, alerta de disco | 1 día hábil |

**Pasos:**
1. **Contener.** Revocar sesiones o llaves, aislar el servicio o restringir el acceso.
2. **Preservar evidencias.** `AuditEvent`, logs y estado del sistema antes de tocarlo (A.5.28).
3. **Erradicar y recuperar**, con los runbooks de [`docs/operacion/`](../operacion/README.md).
4. **Notificar.** Si hay datos personales afectados, avisar a la organización cliente responsable y a la SIC en los plazos y la forma que esta establezca. Se registra qué se notificó, a quién y cuándo.
5. **Post-mortem** en un máximo de 5 días hábiles: causa raíz, cronología y acciones, con un issue por cada acción. Riesgos nuevos o reevaluados al registro.

## 9. Registro y monitoreo (A.8.15–A.8.17)

1. Se registran los eventos de autenticación y de seguridad (`AuditEvent`, axes) sin secretos ni IP en claro en la auditoría; retención en [`retencion.md`](retencion.md).
2. Monitoreo interno con alertas y vigilancia externa (ADR-014). Toda alerta crítica tiene un destinatario configurado (`ALERT_EMAIL_TO`).
3. Relojes sincronizados por NTP en el servidor (aprovisionamiento pendiente).

## 10. Revisión

Estas políticas se revisan **una vez al año**, tras cada incidente S1/S2 y ante cambios de arquitectura. Cada cambio pasa por PR como el código.
