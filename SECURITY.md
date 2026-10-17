# Política de seguridad

## Reportar una vulnerabilidad

**No abras un issue público** para una vulnerabilidad: este repositorio es público.

Usa el reporte privado de GitHub: pestaña **Security → Report a vulnerability** de este repositorio. Solo lo ven los mantenedores. Si no está disponible, escribe a los mantenedores listados en [`.github/CODEOWNERS`](.github/CODEOWNERS) pidiendo un canal privado, sin incluir los detalles en el primer mensaje.

Incluye, si puedes:
- qué componente está afectado (app, API, infraestructura) y qué versión o commit;
- cómo reproducirlo;
- qué impacto crees que tiene.

## Qué puedes esperar

| Paso | Plazo |
|---|---|
| Acuse de recibo | 3 días hábiles |
| Evaluación inicial y severidad | 7 días hábiles |
| Corrección | Según la severidad: 7 días si es crítica, 30 si es alta, 90 si es media ([`docs/seguridad/politicas.md`](docs/seguridad/politicas.md) §4) |

Te avisaremos cuando esté corregida y, si quieres, te mencionaremos en las notas de la release.

## Alcance

Entra en el alcance el código de este repositorio y su despliegue de producción. **No** se permite probar contra producción ataques de denegación de servicio, ingeniería social ni acceso a datos de otras personas. Para probar, levanta el stack local (`infra/README.md`).

## Versiones soportadas

Solo la última release publicada recibe correcciones de seguridad.
