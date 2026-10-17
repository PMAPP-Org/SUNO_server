<!--
El TÍTULO de este PR será el mensaje del commit en master (Squash and merge)
y de él sale el CHANGELOG: usa Conventional Commits, p. ej.
  feat(sync): …   fix(admin): …   feat!: … (rompe compatibilidad)
-->

## Qué cambia y por qué



## Cómo probarlo



## Checklist del autor

**Calidad**
- [ ] El CI está en verde, o lo que falla está explicado aquí y no es de este cambio.
- [ ] Los tests nuevos o cambiados cubren el comportamiento, incluido el caso de error (la cobertura no baja del suelo de `server/pyproject.toml`).
- [ ] Si cambia un `requirements*.txt`: regeneré su `.lock` con el comando de su cabecera.
- [ ] Si cambia el contrato de la API: `server/openapi/v1.yaml` (y `fixtures/` si aplica) está actualizado en el mismo PR, y hay un issue o PR en [SUNO_client](https://github.com/PMAPP-Org/SUNO_client) para actualizar su `contract/`.

**Seguridad** ([`docs/seguridad/owasp-top10-2025.md`](../docs/seguridad/owasp-top10-2025.md))
- [ ] Sin secretos, tokens ni llaves en el código, los tests ni el historial: vienen de variables de entorno o del environment `release`.
- [ ] Los datos nuevos que llegan del cliente se validan en el borde (esquemas Pydantic/Ninja) y el acceso pasa por `scoped_qs` (aislamiento por organización, RD-11).
- [ ] No se desactiva ningún gate. Los falsos positivos se suprimen por regla exacta (`# nosec`, `# nosemgrep`, `.gitleaksignore`) con la justificación al lado, y las vulnerabilidades aceptadas van a [`docs/seguridad/excepciones.md`](../docs/seguridad/excepciones.md) con fecha de caducidad.
- [ ] Ningún log, `job.output` ni `AuditEvent` nuevo guarda contraseñas, tokens ni la IP en claro (RD-12).
- [ ] Si toca autenticación, autorización, TLS, cifrado o el borde (Caddy): lo indico aquí para una revisión más cuidadosa.

**Datos personales** (Ley 1581 de 2012, ISO 27001 A.5.34)
- [ ] Si se guarda un dato personal nuevo (ubicación, foto, identificador, IP): está justificado y tiene plazo de retención en [`docs/seguridad/retencion.md`](../docs/seguridad/retencion.md).

**Despliegue**
- [ ] Si hay migraciones: se pueden aplicar con la versión anterior aún corriendo, o se explica aquí la ventana necesaria.
- [ ] Si hay variables de entorno nuevas: están en `infra/.env.example` (y `check_envvars.sh` pasa).
- [ ] Si rompe compatibilidad con la app ya instalada (SUNO_client) o con datos existentes: el título lleva `!` y se explica la migración.

## Para quien revisa

La aprobación tiene que darla **una persona distinta del autor** (ISO 27001 A.8.32). Revisa el diff, no solo el CI.
