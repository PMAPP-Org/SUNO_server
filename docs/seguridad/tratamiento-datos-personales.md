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
| T-8 | Logs del proxy (IP, ruta, hora) | Cualquier cliente | Operación y seguridad | stdout de Caddy en el VPS | Rotación de Docker: 5 archivos de 10 MB (`infra/provision/provision.sh`) | Ninguno | Sin cuerpos de petición ni cabeceras de autorización |
| T-9 | **Autorización del titular**: versión del aviso, finalidades aceptadas, dispositivo, fecha | Agente | Prueba de la autorización (art. 9) | PostgreSQL (`privacy_consent`) | Mientras exista la cuenta (es la prueba de que el tratamiento fue autorizado) | Ninguno | Solo lectura en el Admin; se escribe solo desde `POST /v1/consents`; incluida en la consulta del titular |

## Pendientes para cumplir

1. **Aviso de privacidad y autorización**: la mecánica está hecha. El servidor guarda la prueba (`POST /v1/consents`, T-9) y la app muestra el aviso antes del primer login, con una autorización expresa por finalidad (cuenta, ubicación, fotos). **Falta** que el asesor jurídico valide el texto del aviso (`lib/l10n/app_es.arb` de SUNO_client) y la identificación del responsable que debe incluir (art. 12).
2. **Plazo de conservación** de visitas, fotos y respuestas (T-3 a T-5), acordado con cada cliente e implementado como un periódico de retención, igual que `security.purge_logs`.
3. **Transferencia internacional** a Cloudflare: verificar el DPA, la región del bucket y la base legal (art. 26).
4. ~~**Derechos del titular**: herramienta en el back-office~~ **Hecho en el Paso 24** (ver abajo). Falta fijar con el asesor jurídico el canal de recepción de solicitudes y los plazos de respuesta (art. 14 y 15: 10 y 15 días hábiles).
5. **Fotos de terceros**: criterio de captura (evitar rostros y documentos innecesarios) en la guía de campo del cliente.
6. **Registro Nacional de Bases de Datos** ante la SIC, si el responsable está obligado.
7. ~~**Rotación de logs de Docker** (T-8)~~: hecho en `infra/provision/provision.sh` (`max-size` 10 MB, `max-file` 5).

## Derechos del titular en el back-office (Paso 24)

Los atiende la **administración de la organización** del titular (rol OWNER o ADMIN; supervisión no basta) desde Django Admin → Usuarios → acciones. Ver `server/visitacampo/backoffice/personal_data.py`.

| Derecho | Cómo | Qué cubre |
|---|---|---|
| **Consulta** | Acción "Derechos del titular: exportar sus datos (JSON)" | Perfil, membresías, dispositivos, ubicación y hora de cada visita creada por la persona, y su rastro en la auditoría (T-1, T-2, T-3, T-6) |
| **Rectificación** | Editar el usuario | Nombre, apellidos, correo, nombre visible |
| **Supresión** | Acción "Derechos del titular: suprimir sus datos (anonimizar)", con pantalla de confirmación | **Anonimización irreversible**: usuario `suprimido-<id>`, se vacían nombre, correo y nombre de los dispositivos, se invalida la contraseña, se revocan todas las sesiones y se borran los segundos factores. Las visitas se conservan (son registros del cliente, con su propio plazo de retención) pero dejan de apuntar a una persona identificable |

Garantías: nadie actúa sobre personas de otra organización (ni se confirma que existan), nadie se suprime a sí mismo, y cada consulta y supresión queda en la auditoría (`backoffice.personal_data_exported` / `backoffice.personal_data_suppressed`).

Límites conocidos: la supresión no alcanza a las fotos (T-4) ni a respuestas de formulario que mencionen a la persona (T-5); eso depende de la plantilla y se resuelve con el plazo de conservación del pendiente 2. Tampoco reescribe los backups, que caducan según `retencion.md`.
