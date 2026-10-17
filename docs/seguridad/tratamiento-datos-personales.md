# Registro de tratamientos de datos personales

> **BORRADOR — pendiente de validación jurídica.** Marco: Ley Estatutaria 1581 de 2012 y Decreto 1377 de 2013 (Colombia), e ISO/IEC 27001:2022 A.5.34. **Este documento no es asesoría legal.** Identifica qué datos personales trata el sistema para que el responsable del tratamiento y su asesor jurídico definan las bases, los avisos y los plazos.

**Roles (por confirmar en cada contrato):**
- Cada **organización cliente** que usa VisitaCampo es, en principio, *responsable del tratamiento* de los datos de sus agentes y de lo que estos capturan en campo.
- El operador de VisitaCampo actúa como *encargado del tratamiento*.

## Tratamientos

| # | Datos | Titular | Finalidad | Dónde | Retención | Destinatarios / transferencias | Medidas |
|---|---|---|---|---|---|---|---|
| T-1 | Nombre de usuario, correo, nombre visible, organización, rol | Agente de campo y usuarios del back-office | Autenticación y control de acceso | PostgreSQL | Mientras exista la cuenta + **por definir** | Ninguno | Argon2id; acceso por organización; Admin restringido |
| T-2 | Identificador del dispositivo, plataforma, versión de la app | Agente | Atar la sesión al dispositivo, revocar teléfonos perdidos | PostgreSQL | Mientras exista el dispositivo + **por definir** | Ninguno | Refresh hasheado y revocable |
| T-3 | **Ubicación GPS** de cada visita (lat, lon, precisión, altitud) | Agente (revela dónde estuvo y cuándo) y, en su caso, terceros visitados | Evidencia de la visita de campo | Teléfono (cifrado) → PostgreSQL → backups | **Por definir** (depende del contrato del cliente) | Cloudflare R2 (backups): **transferencia internacional** | TLS, SQLCipher, backups cifrados, aislamiento por organización |
| T-4 | **Fotografías** de la visita (pueden mostrar personas, fachadas, placas, documentos) | Agente y terceros que aparezcan | Evidencia de la visita | Teléfono → R2 de adjuntos → backups | **Por definir** | Cloudflare R2: **transferencia internacional** | URL prefirmada de corta vida, contenido inmutable, backups cifrados |
| T-5 | Respuestas de formulario | Según la plantilla (pueden incluir datos de terceros) | Objeto del servicio del cliente | Teléfono → PostgreSQL | **Por definir** | Ninguno | Validación por esquema, aislamiento por organización |
| T-6 | Traza de auditoría (actor, acción, dispositivo, **IP como HMAC**) | Usuarios | Seguridad y trazabilidad (A.8.15) | PostgreSQL | 365 días ([`retencion.md`](retencion.md)) | Ninguno | Solo lectura; IP no reversible |
| T-7 | Intentos fallidos de login (**usuario e IP en claro**) | Quien intenta entrar, aunque el usuario no exista | Defensa contra fuerza bruta | PostgreSQL (django-axes) | 90 días ([`retencion.md`](retencion.md)) | Ninguno | Purga automática |
| T-8 | Logs del proxy (IP, ruta, hora) | Cualquier cliente | Operación y seguridad | stdout de Caddy en el VPS | Según la rotación de logs de Docker (**por definir**) | Ninguno | Sin cuerpos de petición ni cabeceras de autorización |

## Pendientes para cumplir

1. **Aviso de privacidad y autorización** del titular en la app, antes del primer login y antes de capturar ubicación o fotos (art. 9 y 12, Ley 1581). Diseño pendiente de validación jurídica; el texto irá en `app/lib/l10n/app_es.arb`.
2. **Plazo de conservación** de visitas, fotos y respuestas (T-3 a T-5), acordado con cada cliente e implementado como un periódico de retención, igual que `security.purge_logs`.
3. **Transferencia internacional** a Cloudflare: verificar el DPA, la región del bucket y la base legal (art. 26).
4. **Derechos del titular** (consulta, rectificación y supresión): procedimiento y herramienta en el back-office (Paso 24).
5. **Fotos de terceros**: criterio de captura (evitar rostros y documentos innecesarios) en la guía de campo del cliente.
6. **Registro Nacional de Bases de Datos** ante la SIC, si el responsable está obligado.
7. **Rotación de logs de Docker** (T-8): fijar `max-size`/`max-file` en el aprovisionamiento del VPS.
