# Seguridad de la información — VisitaCampo

Documentación del SGSI del proyecto, alineada con ISO/IEC 27001:2022 y OWASP Top 10:2025. Los documentos marcados **BORRADOR** esperan la aprobación de la dirección o la validación jurídica: técnicamente están completos, pero no tienen validez de SGSI hasta que se aprueben y se asignen los roles.

## Documentos

| Documento | Qué responde | Estado |
|---|---|---|
| [`declaracion-aplicabilidad.md`](declaracion-aplicabilidad.md) | Qué controles del Anexo A aplican y cómo se cumplen (los 93) | Borrador |
| [`registro-riesgos.md`](registro-riesgos.md) | Qué puede salir mal, cuánto importa y qué se hace al respecto | Borrador |
| [`politicas.md`](politicas.md) | Reglas de desarrollo seguro, cambios, vulnerabilidades (plazos), llaves, acceso, backups, incidentes | Borrador |
| [`inventario-activos.md`](inventario-activos.md) | Qué datos, secretos y proveedores hay, cómo se clasifican y quién responde de ellos | Borrador |
| [`tratamiento-datos-personales.md`](tratamiento-datos-personales.md) | Qué datos personales se tratan (Ley 1581) y qué falta para cumplir | Borrador, requiere jurídico |
| [`retencion.md`](retencion.md) | Cuánto se guarda cada dato y qué lo borra | Vigente (implementado) |
| [`excepciones.md`](excepciones.md) | Vulnerabilidades aceptadas, con caducidad | Vigente |
| [`owasp-top10-2025.md`](owasp-top10-2025.md) | Qué prueba automatizada cubre cada riesgo OWASP y cada control técnico | Vigente (lo verifica el CI) |
| [`hallazgos-tests.toml`](hallazgos-tests.toml) | Qué test cubre cada hallazgo H-01…H-21 de la revisión de QField/QFieldCloud | Vigente (lo verifica el CI de los dos repos) |
| [`verificacion-mvp.md`](verificacion-mvp.md) | Gate de cierre de la Fase 1: suite de seguridad, carga y criterios de aceptación, con lo que queda abierto | Vigente, con pendientes manuales |

## Estado de cumplimiento, en una frase

Los **controles tecnológicos y de desarrollo** (familia 8 del Anexo A) están implementados y se verifican en cada PR. Lo que falta para un SGSI certificable es **organizacional**: aprobar estos borradores, designar roles, exigir la revisión independiente de cambios (issue #7), evaluar proveedores, formar al equipo y cumplir los pendientes de la Ley 1581.

## Próximos pasos, por prioridad

1. Rol Admin del repositorio → protección de `master` con aprobación de otra persona (R-17, issue #7).
2. Aprobación de la dirección: alcance, política general, estos borradores y asignación de roles.
3. Aviso de privacidad y plazos de conservación de visitas y fotos (R-25).
4. ~~MFA en el back-office (R-20) y Paso 15 (R-24)~~: implementados.
5. Ensayar los runbooks de [`docs/operacion/`](../operacion/README.md) y activar el reporte privado de vulnerabilidades (`SECURITY.md`).
6. Al contratar el VPS: aprovisionamiento endurecido (firewall, SSH solo con llave, NTP, rotación de logs), despliegue del monitoreo y primer simulacro real de restauración.
