# Declaración de Aplicabilidad (SoA) — ISO/IEC 27001:2022, Anexo A

> **BORRADOR — pendiente de aprobación por la dirección.** Preparado desde el repositorio el 2026-10-17. Las decisiones de aplicabilidad de los controles organizacionales, de personas y físicos las toma la organización; aquí se proponen y se marcan como tales.

**Alcance propuesto del SGSI:** desarrollo, operación y soporte del sistema VisitaCampo, que comprende la app móvil Flutter, el backend Django, la infraestructura en VPS con Docker Compose y Caddy, y el almacenamiento S3/R2 de adjuntos y backups. También el ciclo de vida del código en GitHub y los datos de visitas de campo de las organizaciones cliente.

**Leyenda de estado**

| Estado | Significado |
|---|---|
| ✅ | Implementado, con evidencia verificable en el repositorio o en el CI |
| 🟡 | Parcial: hay controles técnicos, pero falta una parte |
| ⬜ | Pendiente: aplica y no está implementado |
| 🏢 | Organizacional: aplica, pero se cumple fuera del repositorio (procesos, personas, contratos) y lo debe documentar la organización |
| ➖ | No aplica (con justificación) |

**Resumen:** de 93 controles, 25 implementados, 34 parciales, 2 pendientes, 29 organizacionales y 3 que no aplican. El código y el CI cubren bien la familia 8 (tecnológicos). Lo que más falta para certificar está en las familias 5 a 7, que son procesos y personas.

## 5. Controles organizacionales

| Control | Aplica | Estado | Justificación / evidencia |
|---|---|---|---|
| 5.1 Políticas de seguridad de la información | Sí | 🟡 | Políticas temáticas de desarrollo y operación en [`politicas.md`](politicas.md) (borrador). Falta la política general aprobada por la dirección |
| 5.2 Roles y responsabilidades | Sí | ⬜ | Roles definidos en [`politicas.md`](politicas.md) §1, sin personas asignadas. Designación pendiente |
| 5.3 Segregación de funciones | Sí | 🟡 | CODEOWNERS y "nadie aprueba su propio PR" (plantilla de PR). Falta que la protección de rama lo exija (issue #5) |
| 5.4 Responsabilidades de la dirección | Sí | 🏢 | Aprobación de políticas, recursos y revisión del SGSI |
| 5.5 Contacto con las autoridades | Sí | 🏢 | SIC (protección de datos, Colombia) y colCERT. Directorio a cargo de la organización |
| 5.6 Contacto con grupos de interés especial | Sí | 🏢 | Suscripción a avisos de seguridad de Django, Flutter y Python (GitHub Security Advisories vía Dependabot) |
| 5.7 Inteligencia de amenazas | Sí | 🟡 | Automático para dependencias: osv-scanner y pip-audit semanales, Dependabot y Scorecard. Falta un proceso de revisión periódica |
| 5.8 Seguridad en la gestión de proyectos | Sí | ✅ | Requisitos de seguridad como restricciones de diseño (RD-01…16, doc 08); checklist de seguridad en cada PR |
| 5.9 Inventario de información y activos | Sí | 🟡 | [`inventario-activos.md`](inventario-activos.md) (borrador). Falta asignar dueños |
| 5.10 Uso aceptable de la información | Sí | 🏢 | Política de uso aceptable para el personal |
| 5.11 Devolución de activos | Sí | 🏢 | Al terminar una relación laboral: dispositivos, accesos y llaves |
| 5.12 Clasificación de la información | Sí | 🟡 | Clasificación propuesta en [`inventario-activos.md`](inventario-activos.md). Falta aprobarla |
| 5.13 Etiquetado de la información | Sí | 🏢 | Según el esquema de 5.12 |
| 5.14 Transferencia de información | Sí | ✅ | TLS estricto en todo el tráfico (RD-01, ADR-009); subida de adjuntos por URL prefirmada con checksum verificado (ADR-007); backups cifrados con age |
| 5.15 Control de acceso | Sí | ✅ | Aislamiento por organización con `scoped_qs` y test de arquitectura (RD-11); roles por membresía; `/admin/` restringido por origen; environment `release` para secretos |
| 5.16 Gestión de identidades | Sí | 🟡 | Usuarios por organización en Django Admin. Falta el proceso de altas y bajas (alta, revisión, baja al terminar la relación) |
| 5.17 Información de autenticación | Sí | ✅ | Argon2id; refresh tokens solo como hash (RD-09); `SECRET_KEY` fuerte obligatoria en producción; tokens en Keystore/Keychain en el móvil |
| 5.18 Derechos de acceso | Sí | 🟡 | Membresía revocable que corta la sesión en el siguiente refresh. Falta la revisión periódica de accesos |
| 5.19 Seguridad en las relaciones con proveedores | Sí | 🏢 | Proveedores críticos identificados en [`inventario-activos.md`](inventario-activos.md) §4 |
| 5.20 Seguridad en los acuerdos con proveedores | Sí | 🏢 | Revisión de términos de GitHub, el proveedor del VPS y Cloudflare |
| 5.21 Seguridad en la cadena de suministro TIC | Sí | ✅ | Lockfiles con hashes, digests, actions por SHA, binarios con checksum, Dependency Review, SBOM, atestaciones SLSA, espera de 7 días en Dependabot |
| 5.22 Seguimiento de servicios de proveedores | Sí | 🏢 | Revisión anual de proveedores |
| 5.23 Seguridad en el uso de servicios en la nube | Sí | 🟡 | R2 con bucket de backups separado y token acotado; VPS con aprovisionamiento endurecido (pendiente). Falta evaluar al proveedor |
| 5.24 Planificación de la gestión de incidentes | Sí | 🟡 | Procedimiento en [`politicas.md`](politicas.md) §8 (borrador); detección con alertas (ADR-014) e issue `incidente` (uptime.yml) |
| 5.25 Evaluación de eventos de seguridad | Sí | 🟡 | Clasificación de severidad en [`politicas.md`](politicas.md) §8 |
| 5.26 Respuesta a incidentes | Sí | 🟡 | Procedimiento en borrador. Faltan los runbooks probados |
| 5.27 Aprendizaje de los incidentes | Sí | 🟡 | Post-mortem obligatorio en el procedimiento §8 |
| 5.28 Recopilación de evidencias | Sí | 🟡 | `AuditEvent` de solo lectura y logs. Falta la cadena de custodia |
| 5.29 Seguridad durante una interrupción | Sí | 🏢 | Plan de continuidad |
| 5.30 Preparación de las TIC para la continuidad | Sí | 🟡 | Backups con simulacro de restauración en CI. Faltan RTO/RPO aprobados y un simulacro real |
| 5.31 Requisitos legales y contractuales | Sí | 🟡 | Ley 1581 de 2012 identificada ([`tratamiento-datos-personales.md`](tratamiento-datos-personales.md)). Faltan el registro ante la SIC si aplica y los contratos con clientes |
| 5.32 Derechos de propiedad intelectual | Sí | ✅ | Dependency Review deniega licencias copyleft fuertes; decisión de no copiar código GPL de QField (doc 08 §7). Falta definir la licencia del propio repo |
| 5.33 Protección de los registros | Sí | ✅ | `AuditEvent` sin alta, cambio ni borrado en el Admin; purga solo por política; historial de git y de CI |
| 5.34 Privacidad y protección de datos personales | Sí | 🟡 | IP solo como HMAC, retención definida, sin backup en la nube en Android. Falta el aviso de privacidad en la app y la retención de visitas y fotos |
| 5.35 Revisión independiente de la seguridad | Sí | 🏢 | Auditoría interna o externa del SGSI |
| 5.36 Cumplimiento de políticas y normas | Sí | ✅ | Los gates de CI hacen cumplir las políticas técnicas en cada PR ([`owasp-top10-2025.md`](owasp-top10-2025.md)) |
| 5.37 Procedimientos de operación documentados | Sí | 🟡 | `infra/backup/README.md`, `infra/monitoring/README.md`, CONTRIBUTING. Faltan los runbooks de despliegue, rollback e incidentes |

## 6. Controles de personas

| Control | Aplica | Estado | Justificación / evidencia |
|---|---|---|---|
| 6.1 Verificación de antecedentes | Sí | 🏢 | Proceso de contratación |
| 6.2 Términos y condiciones de empleo | Sí | 🏢 | Cláusulas de seguridad y confidencialidad |
| 6.3 Concienciación, educación y formación | Sí | 🏢 | Formación en desarrollo seguro (OWASP Top 10) para quien contribuya al código |
| 6.4 Proceso disciplinario | Sí | 🏢 | — |
| 6.5 Responsabilidades al terminar el empleo | Sí | 🏢 | Revocar los accesos a GitHub, al VPS, a R2 y a Grafana |
| 6.6 Acuerdos de confidencialidad | Sí | 🏢 | NDA con personal y proveedores |
| 6.7 Trabajo en remoto | Sí | 🏢 | El acceso al servidor y a los paneles es solo por túnel SSH (`infra/monitoring/README.md`) |
| 6.8 Notificación de eventos de seguridad | Sí | 🟡 | `SECURITY.md` pendiente; canal interno en [`politicas.md`](politicas.md) §8 |

## 7. Controles físicos

El software corre en un VPS de un proveedor de nube: la seguridad física del centro de datos se **hereda del proveedor** y se evalúa en 5.19–5.23. Las oficinas y los equipos del personal son responsabilidad de la organización.

| Control | Aplica | Estado | Justificación / evidencia |
|---|---|---|---|
| 7.1 Perímetros de seguridad física | Sí | 🏢 | Heredado del proveedor del VPS para el servidor; oficinas a cargo de la organización |
| 7.2 Controles de entrada física | Sí | 🏢 | Ídem |
| 7.3 Seguridad de oficinas e instalaciones | Sí | 🏢 | — |
| 7.4 Monitorización de la seguridad física | Sí | 🏢 | Heredado del proveedor |
| 7.5 Protección contra amenazas físicas y ambientales | Sí | 🏢 | Heredado del proveedor; los backups fuera del VPS cubren la pérdida del servidor |
| 7.6 Trabajo en áreas seguras | No | ➖ | No hay áreas seguras propias: la infraestructura está en la nube |
| 7.7 Escritorio y pantalla limpios | Sí | 🏢 | — |
| 7.8 Ubicación y protección de equipos | Sí | 🏢 | — |
| 7.9 Seguridad de los activos fuera de las instalaciones | Sí | 🟡 | Los teléfonos de campo tienen la BD cifrada (SQLCipher), tokens en Keystore, sin backup en la nube y con sesión revocable. Falta la gestión de dispositivos (MDM) |
| 7.10 Soportes de almacenamiento | Sí | 🏢 | Política para la copia offline de la llave privada de backups |
| 7.11 Servicios de suministro | No | ➖ | Heredado íntegramente del proveedor del VPS |
| 7.12 Seguridad del cableado | No | ➖ | Heredado íntegramente del proveedor del VPS |
| 7.13 Mantenimiento de los equipos | Sí | 🏢 | — |
| 7.14 Eliminación o reutilización segura de equipos | Sí | 🏢 | Borrado de teléfonos de campo dados de baja |

## 8. Controles tecnológicos

| Control | Aplica | Estado | Justificación / evidencia |
|---|---|---|---|
| 8.1 Dispositivos finales de usuario | Sí | 🟡 | App: BD cifrada, tokens en almacenamiento seguro, TLS estricto, sin backup ni tráfico en claro. Falta MDM y bloqueo de pantalla obligatorio |
| 8.2 Derechos de acceso privilegiado | Sí | 🟡 | Superusuario del Admin y acceso SSH al VPS. Falta MFA en el Admin y un inventario de cuentas privilegiadas |
| 8.3 Restricción del acceso a la información | Sí | ✅ | `scoped_qs` + test de arquitectura (RD-11); `404` para recursos de otra organización (ADR-013) |
| 8.4 Acceso al código fuente | Sí | 🟡 | CODEOWNERS y gitleaks en todo el historial. Falta la protección de rama (issue #5) y decidir si el repo público debe pasar a privado |
| 8.5 Autenticación segura | Sí | ✅ | Bloqueo tras 5 fallos (django-axes), `401` genérico sin enumeración, JWT EdDSA corto, rotación con CAS (RD-10), refresh atado al dispositivo. Falta MFA en el Admin (ver 8.2) |
| 8.6 Gestión de la capacidad | Sí | 🟡 | Alertas de CPU, memoria y disco (incluida la proyección a 24 h) en `infra/monitoring/`. Falta desplegarlas |
| 8.7 Protección contra el malware | Sí | 🟡 | Imágenes escaneadas con grype; contenedores sin privilegios y con código de solo lectura. Endpoints del personal a cargo de la organización |
| 8.8 Gestión de vulnerabilidades técnicas | Sí | ✅ | pip-audit, osv-scanner, grype, Dependency Review, Dependabot, escaneos semanales; plazos de corrección en [`politicas.md`](politicas.md) §4; [`excepciones.md`](excepciones.md) con caducidad |
| 8.9 Gestión de la configuración | Sí | ✅ | Configuración como código; lockfiles con hashes; digests; `check --deploy` contra prod; `check_envvars.sh` |
| 8.10 Eliminación de la información | Sí | 🟡 | Purga automática de trazas ([`retencion.md`](retencion.md)). Falta la retención de visitas y fotos y el borrado a petición del titular |
| 8.11 Enmascaramiento de datos | Sí | ✅ | IP como HMAC en auditoría; redacción de secretos en logs y en `job.output` (RD-12) |
| 8.12 Prevención de fuga de datos | Sí | ✅ | Scrubber de logs, gitleaks, sin backup Android, `CSP default-src 'none'` en la API, secretos fuera de git |
| 8.13 Copias de seguridad | Sí | 🟡 | `infra/backup/` (cifrado age, rotación, adjuntos inmutables) con simulacro en CI. Falta desplegarlo y fijar RTO/RPO |
| 8.14 Redundancia de las instalaciones | Sí | 🟡 | VPS único por decisión de coste (ADR-010): riesgo R-21 en [`registro-riesgos.md`](registro-riesgos.md), pendiente de aceptación formal, mitigado con backups fuera del servidor |
| 8.15 Registro (logging) | Sí | ✅ | `AuditEvent` de autenticación, `AccessFailureLog`, logs JSON de Caddy, todo con redacción de secretos |
| 8.16 Actividades de monitorización | Sí | 🟡 | ADR-014: monitoreo interno con alertas probadas en CI y vigilancia externa (`uptime.yml`). Falta desplegarlo |
| 8.17 Sincronización de relojes | Sí | ⬜ | Necesaria para correlacionar logs y validar tokens. Configurar NTP en el aprovisionamiento del VPS |
| 8.18 Uso de programas de utilidad privilegiados | Sí | 🟡 | Contenedores con `cap_drop: ALL` y `no-new-privileges`; sin acceso al socket de Docker (ADR-014). Falta la política de acceso al host |
| 8.19 Instalación de software en sistemas operativos | Sí | 🟡 | Solo imágenes fijadas por digest. Falta el script de aprovisionamiento del VPS |
| 8.20 Seguridad de las redes | Sí | 🟡 | Solo Caddy publica 80 y 443; PostgreSQL sin puertos expuestos; paneles en `127.0.0.1`. Falta el firewall del host |
| 8.21 Seguridad de los servicios de red | Sí | ✅ | TLS automático con HSTS y preload, cabeceras de seguridad verificadas por DAST, redirección HTTPS |
| 8.22 Segregación de redes | Sí | 🟡 | Red interna de Compose sin exposición; pendiente separar en redes frontend y backend |
| 8.23 Filtrado web | Sí | 🏢 | Para los equipos del personal |
| 8.24 Uso de criptografía | Sí | ✅ | Argon2id, EdDSA, SHA-256, SQLCipher, TLS 1.2+, age; gestión de llaves en [`politicas.md`](politicas.md) §5 |
| 8.25 Ciclo de vida de desarrollo seguro | Sí | ✅ | CONTRIBUTING, plantilla de PR, gates de CI, [`owasp-top10-2025.md`](owasp-top10-2025.md) |
| 8.26 Requisitos de seguridad de las aplicaciones | Sí | ✅ | RD-01…RD-16 (doc 08 §3), derivados de una auditoría de código |
| 8.27 Arquitectura y principios de ingeniería seguros | Sí | ✅ | ADR-001…014 con alternativas descartadas; deny-by-default, mínimo privilegio, defensa en profundidad |
| 8.28 Codificación segura | Sí | ✅ | Bandit, Semgrep (OWASP), CodeQL, zizmor, actionlint, hadolint, ruff, mypy, `dart analyze`, lint RD-01 |
| 8.29 Pruebas de seguridad en desarrollo y aceptación | Sí | ✅ | Tests de seguridad, DAST (Schemathesis + ZAP) contra el stack de producción, simulacros de backup y alertas |
| 8.30 Desarrollo externalizado | Sí | 🟡 | Se usan asistentes de IA para programar: todo cambio pasa por los mismos gates y por revisión humana (pendiente de exigir, 5.3). Reuso de código de terceros solo con licencia compatible (qfieldcloud, MIT) |
| 8.31 Separación de los entornos | Sí | ✅ | Settings dev y prod separados; el CI usa secretos efímeros de un solo uso; `.env` fuera de git |
| 8.32 Gestión de cambios | Sí | 🟡 | PR, CI obligatorio, Conventional Commits, CHANGELOG y releases trazables con procedencia firmada. Falta la aprobación independiente obligatoria (issue #5) |
| 8.33 Información de prueba | Sí | ✅ | Los tests y el CI usan datos sintéticos; nunca datos de producción |
| 8.34 Protección durante las pruebas de auditoría | Sí | ✅ | El DAST y los escaneos activos corren contra un stack efímero de CI, nunca contra producción |
