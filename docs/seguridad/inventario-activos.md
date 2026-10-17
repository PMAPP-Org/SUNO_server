# Inventario de activos y clasificación de la información

> **BORRADOR — pendiente de aprobación.** ISO/IEC 27001:2022 A.5.9 (inventario), A.5.12 (clasificación) y A.5.19–A.5.23 (proveedores). Los dueños se asignan al aprobarlo.

## 1. Esquema de clasificación propuesto

| Nivel | Qué es | Ejemplos | Tratamiento mínimo |
|---|---|---|---|
| **Secreto** | Su divulgación compromete todo el sistema | Llaves privadas, contraseñas, tokens | Nunca en git ni en logs; solo en gestor de secretos o environment de GitHub; acceso nominal; rotación ante sospecha |
| **Confidencial** | Datos de clientes y personales | Visitas, fotos, GPS, usuarios, trazas con IP | Cifrado en tránsito y en reposo (backups); acceso por organización; retención definida |
| **Interno** | Útil para atacar el sistema, sin ser un dato de cliente | Configuración de producción no secreta, métricas, logs redactados | Solo personal autorizado |
| **Público** | Puede publicarse | Código y documentación del repositorio (hoy público, ver R-22) | — |

## 2. Activos de información

| Activo | Clasificación | Dónde vive | Protección | Dueño |
|---|---|---|---|---|
| Datos de visitas (respuestas, GPS) | Confidencial | PostgreSQL (VPS); BD local cifrada en cada teléfono | Aislamiento por organización, TLS, SQLCipher, backups cifrados | Por designar |
| Fotos y adjuntos | Confidencial | Bucket R2 de adjuntos; teléfono hasta subir | Direccionados por contenido, URL prefirmada de corta vida, backups | Por designar |
| Cuentas de usuario y membresías | Confidencial | PostgreSQL | Argon2id, bloqueo por fallos, auditoría | Por designar |
| Traza de auditoría y logs de axes | Confidencial | PostgreSQL | Solo lectura en el Admin, IP como HMAC, retención ([`retencion.md`](retencion.md)) | Por designar |
| Backups de BD y adjuntos | Confidencial | Bucket R2 de backups | Cifrado age (llave pública en el servidor), bucket y token separados | Por designar |
| Código del servidor e infraestructura, CI y documentación del SGSI | Público (repo público) | GitHub `PMAPP-Org/SUNO_server` | Gates de CI, gitleaks, CODEOWNERS; protección de rama pendiente | Victor-Pulido |
| Código de la app móvil y su CI | Público (repo público) | GitHub `PMAPP-Org/SUNO_client` | Gates de CI, gitleaks, CODEOWNERS; protección de rama pendiente | Victor-Pulido |
| Historial del monorepo anterior (evidencia de cambios y revisiones hasta v0.2.0) | Público, solo lectura | GitHub `PMAPP-Org/Seguimiento_Proyectos` (archivado) | Archivado: sin escrituras | Victor-Pulido |
| Imágenes y artefactos de release | Interno | GHCR y GitHub Releases | Atestaciones SLSA firmadas, SBOM | Por designar |
| Métricas y alertas | Interno | Prometheus y Grafana en el VPS | Solo `127.0.0.1` (túnel SSH), admin con contraseña | Por designar |

## 3. Secretos y llaves

| Secreto | Para qué | Dónde debe vivir | Dónde NO | Rotación |
|---|---|---|---|---|
| `DJANGO_SECRET_KEY` | Firma de sesiones y datos de Django | `.env` del servidor (permisos 600) | git, CI | Ante sospecha (invalida sesiones del Admin y los hash de IP) |
| Llave JWT Ed25519 | Firma de access tokens | Archivo de secreto (`JWT_SIGNING_KEY_FILE`) en el servidor | git, logs | Anual o ante sospecha (rotación con dos llaves: Paso 15) |
| Contraseña de PostgreSQL | Acceso a la BD | `.env` del servidor | git | Anual |
| Credenciales R2 de adjuntos | La API sube y lee adjuntos | `.env` del servidor | git | Anual o ante sospecha |
| Credenciales R2 de backups | Escritura en el bucket de backups | `.env` del servidor, token limitado a ese bucket | Compartidas con las de adjuntos | Anual |
| **Llave privada age** (backups) | Restaurar backups | Gestor de secretos **y** copia offline | **El servidor**, git, CI | Solo si se compromete (re-cifrar los backups vigentes) |
| Llave de subida Android (`.jks`) | Firmar las releases de la app | Gestor de secretos + environment `release` de GitHub | git (`.gitignore`), equipos personales | No rotable fácilmente: custodia estricta (R-19) |
| Contraseña de Grafana | Panel de monitoreo | `.env` del servidor | git | Anual |
| Token de GitHub de despliegue / `RELEASE_PLEASE_TOKEN` | Automatización | Environment `release` | Repositorio, código | Caducidad ≤ 1 año (token *fine-grained*) |

## 4. Proveedores (A.5.19–A.5.23)

| Proveedor | Servicio | Datos que trata | Riesgo principal | A revisar |
|---|---|---|---|---|
| GitHub (Microsoft) | Código, CI, GHCR, releases | Código; secretos de release | Compromiso de cuenta, cadena de suministro de actions | MFA obligatorio para colaboradores; permisos mínimos; actions por SHA (hecho) |
| Proveedor del VPS (por elegir) | Cómputo y disco del servidor | Todos los datos en uso | Disponibilidad, acceso físico | Certificaciones (ISO 27001/SOC 2), ubicación de los datos, backups propios |
| Cloudflare (R2) | Almacenamiento de adjuntos y backups | Fotos; backups cifrados | Transferencia internacional (Ley 1581); disponibilidad | Términos de tratamiento de datos (DPA), región, bucket lock |
| Let's Encrypt (vía Caddy) | Certificados TLS | Nombre de dominio | Fallo de renovación | Alerta `TlsCertExpiringSoon` (hecho) |
| PyPI, pub.dev, Docker Hub, GHCR | Dependencias e imágenes | — | Paquete malicioso | Hashes, digests, espera de 7 días, escaneo (hecho) |
| Proveedor de SMTP (por elegir) | Correos de alerta | Direcciones del equipo | Alertas que no llegan | Probar el envío al desplegar |
