# Identidad visual SUNO

La identidad utiliza el emblema original entregado y el nombre **SUNO**. El mockup `3d69e6ad-06b7-42d5-93be-ce45c3c8c995.png` es la referencia visual; el nombre SORA de esa referencia no se usa como nombre del producto.

## Paleta

| Uso | Color | Criterio |
| --- | --- | --- |
| Rojo de marca | `#E84546` | Emblema, acentos y líneas de identidad |
| Rojo de acción | `#A5282C` | Botones con texto crema; contraste AA para texto normal |
| Rojo profundo | `#8F1D23` | Enlaces y hover |
| Crema | `#FFFDF4` | Fondo principal |
| Papel | `#F6F3EB` | Superficies secundarias |
| Tinta | `#161715` | Texto y secciones oscuras |
| Gris de lectura | `#62655F` | Texto secundario |
| Gris de línea | `#E5E1DA` | Separadores decorativos |
| Rosa | `#D57C87` | Acentos sobre tinta; no texto pequeño sobre crema |
| Verde natural | `#6B7F6B` | Referencia de marca para estados |
| Verde funcional | `#465E48` | Confirmaciones legibles |

El color de marca no se usa directamente detrás de texto pequeño blanco. El tema oscuro utiliza `#F0A0A5` para acciones y enlaces sobre tinta. Los estados siempre incluyen una etiqueta, no solo color. Los bordes de campos usan un gris más fuerte que los separadores decorativos.

## Tipografía y composición

Inter 4.1, incluida localmente con su licencia SIL OFL. Fuente: https://rsms.me/inter/download/ y release oficial https://github.com/rsms/inter/releases/tag/v4.1. Pesos de interfaz: 400, 500, 600 y 700. La web utiliza el archivo variable WOFF2; Flutter usa TTF estáticos. No se hacen peticiones a Google Fonts ni a un CDN al abrir la aplicación.

SUNO se compone como palabra tipográfica en mayúsculas, acompañada del emblema original. El trazo de pincel pertenece al símbolo; se mantiene una tipografía legible en títulos y controles. No se presenta la fuente sans-serif Sora como si fuera la escritura de pincel del mockup.

Ritmo de 4/8 px, márgenes de 24 px en móvil, botones de al menos 48 dp y esquinas de 6–8 px. Sin degradados de interfaz, sombras decorativas, tarjetas anidadas o estadísticas inventadas. En web: composición editorial, separadores finos y bloques amplios; en administración: densidad adecuada para tablas y formularios.

## Iconografía

Iconos funcionales de línea y un significado estable por acción: persona (usuario), candado (contraseña), ubicación con signo más (visita), flechas circulares (sincronizar), documento (registros) y alerta (revisión). Flutter centraliza Material Icons Outlined en `lib/ui/theme/app_icons.dart`. La web usa SVG locales de línea, 24 × 24, trazo 1.6 y sin relleno. Django conserva sus iconos de acciones y estado para respetar su comportamiento nativo. No se utilizan emojis como iconos de producto.

## Archivos y mantenimiento

- Cliente: `lib/ui/theme/`, `lib/ui/widgets/app_brand.dart`, `assets/brand/`, `assets/fonts/`.
- Servidor: `server/visitacampo/core/static/suno/` y `server/visitacampo/core/templates/`.
- Los PNG originales y la referencia se conservan sin modificación en ambos repositorios. La app empaqueta solo los activos declarados en `pubspec.yaml`.
- Principal para fondos claros, variantes para fondos oscuros y monocromáticas disponibles para futuras piezas. Nunca estirar, recolorear ni añadir sombras al emblema. Área de respeto mínima: un cuarto de la altura del símbolo.
- Cualquier cambio de paleta debe reflejarse en `app_colors.dart` y en `tokens.css`/`admin.css`. No hay dependencia de ejecución entre los dos repositorios.

## Alcance funcional

La nueva portada pública está en `/`. El panel de trabajo sigue en `/admin/`, con los permisos y MFA existentes. Las restricciones de red de Caddy continúan aplicándose al panel. La portada no es una segunda aplicación autenticada ni un visor cartográfico nuevo.

La app mantiene su arquitectura móvil nativa, almacenamiento sin conexión, contratos y nombres internos de paquetes. El nombre visible pasa a SUNO en Android, iOS, login e inicio. El tema se aplica también a captura, revisión, formularios y firma; el lienzo exportado de firma conserva tinta y papel constantes.

## Verificación

- Flutter: `flutter pub get --enforce-lockfile`, `dart run build_runner build`, `flutter analyze`, `flutter test --concurrency=1`, `bash tool/lint_ui_tokens.sh`.
- Django: `python manage.py check`, `python manage.py test visitacampo.core.tests.test_branding`, `python manage.py collectstatic --noinput`.
- Antes de publicar: revisar portada y panel a 390 px y escritorio, temas claro/oscuro y campos de autenticación con OTP. Los cambios locales no equivalen a un despliegue.
