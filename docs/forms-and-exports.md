# Fase C5 y C6

## Formularios

El catálogo `visitacampo/form@1` incorpora `qr`, `repeat` y la propiedad `default`.
Hay que actualizar primero servidor y app antes de publicar plantillas que los
usen: los clientes antiguos rechazan explícitamente tipos desconocidos.

- `qr` almacena texto (máximo 4096 caracteres, restringible con `min_length` y
  `max_length`). La app permite escanear o escribir; nunca abre el contenido como URL.
- `default` es un literal del tipo del campo, validado al publicar. Se aplica al
  crear una visita o añadir una fila; no modifica visitas editadas ni campos
  vaciados. No se admite en fotos, firmas o grupos repetibles.
- `repeat` contiene `fields`, `min_rows` (por defecto 0) y `max_rows` (1–100,
  por defecto 100). La respuesta es una lista de objetos. Cada fila tiene su
  propio ámbito de visibilidad: solo campos hermanos declarados antes. Máximo
  tres niveles de grupos, 300 campos declarados y 10000 posiciones de respuesta
  posibles, contando el producto de los máximos de los grupos anidados.
- Los errores incluyen la ruta, por ejemplo `answers.predios.0.codigo`.
  Los adjuntos de las filas siguen siendo referencias, y se sincronizan con el
  mecanismo habitual. Los campos ocultos se eliminan al guardar, también dentro
  de los grupos. El servidor no exige hijos de un grupo oculto, pero comprueba
  el tipo de cualquier respuesta que reciba.

Ejemplo:

```json
{"fields":[{"key":"predios","type":"repeat","label":{"es":"Predios"},
"max_rows":20,"fields":[
{"key":"codigo","type":"qr","label":{"es":"Código"},"required":true},
{"key":"estado","type":"text","label":{"es":"Estado"},"default":"Pendiente"}
]}]}
```

## Importar XLSForm

En `/admin`, abre una plantilla y selecciona **Importar XLSForm**. Adjunta el
`.xlsx` y pulsa **Validar y publicar nueva versión**. Se exige rol OWNER o ADMIN;
las versiones anteriores y sus visitas quedan intactas. El resultado viaja por
la sincronización normal y queda auditado. También está disponible:

```sh
python manage.py import_xlsform formulario.xlsx --org UUID --by administrador --template UUID --validate-only
python manage.py import_xlsform formulario.xlsx --org UUID --by administrador --template UUID --name "Inspección"
```

Subconjunto admitido, basado en [XLSForm](https://xlsform.org/):

| Hoja | Columnas |
|---|---|
| survey | type, name, label, hint, required, default, relevant, appearance, repeat_count |
| choices | list_name, name, label |
| settings | form_title, form_id, version (identificadores de origen; SUNO asigna su propia versión) |

Tipos: text, integer, decimal, date, dateTime, select_one, select_multiple,
geopoint, geotrace, geoshape, image, barcode, begin_repeat/end_repeat (también
con espacio). `image` con `appearance=signature` produce una firma.
Las etiquetas se importan como español. `required` admite yes/no o true/false.
`relevant` admite una comparación `${campo} = 'literal'` o `!=`, `<`, `<=`, `>`,
`>=`, con texto entre comillas o un número literal; `and`/`or` se rechazan.
No ejecuta XPath. `repeat_count` solo admite un entero literal (1–100) en
`begin_repeat` y se convierte en `max_rows`; es necesario para anidar grupos,
porque con el máximo por defecto (100) dos niveles ya superan las 10000
posiciones de respuesta.

Las columnas no admitidas con contenido, cálculos, constraints, funciones,
listas externas, grupos no repetibles y apariencias distintas se rechazan;
no se ignoran reglas. Los valores iniciales son literales; las fechas pueden ser
texto ISO o una celda de fecha de Excel sin hora. Máximo 5 MiB de archivo, 20 MiB expandido, 100 entradas ZIP, 2000 filas
y 32 columnas por hoja. Se rechazan macros, enlaces externos y fórmulas Excel;
el XML se analiza con defusedxml. Un error indica hoja/fila cuando corresponde.

## Exportar

En la lista de visitas del administrador selecciona las visitas y ejecuta
**Exportar a GeoJSON**, **Exportar a GeoPackage** o **Informe PDF de una visita**.
PDF exige exactamente una visita. Los tres formatos requieren OWNER, ADMIN o
SUPERVISOR, filtran por organización, excluyen borradas y registran la descarga
en auditoría. Se mantiene el máximo de 10000 visitas por selección; las salidas
geográficas se rechazan si superan 20000 entidades o 64 MiB de contenido JSON.

GeoJSON sigue RFC 7946 y GeoPackage el
[formato OGC 1.3](https://www.geopackage.org/spec130/). Ambos usan WGS84, orden
longitud/latitud. Una visita puede producir varias entidades: su punto GPS y
cada campo de ubicación, línea o polígono, incluidos los de grupos repetibles.
`visit_id` agrupa esas entidades y `field` identifica la ruta del campo. Si no
hay geometría, se conserva una entidad con geometría nula. Las respuestas
completas acompañan cada entidad; en GeoPackage son JSON en la columna `answers`.

El PDF contiene identificador, formulario, versión, fecha con zona horaria,
autor, ubicación y respuestas con etiquetas, incluidas las filas repetidas.
Fotos y firmas se indican por referencia: sus binarios no se incrustan.
Se escapa el texto y no se descargan recursos externos.

En la app, supervisión puede abrir el detalle de una visita y elegir **Exportar
visita**, incluso sin conexión: GeoJSON o PDF mediante el menú nativo de
compartir/guardar. La copia indica que procede del dispositivo y su estado de
sincronización; puede contener cambios aún pendientes. Esta salida local no
genera una descarga ni un evento de auditoría en el servidor. Para exportaciones
centralizadas auditadas o lotes GeoPackage se usa el administrador.

Antes del uso operativo falta la prueba física de QR (permiso de cámara,
cancelación y reanudación) y de guardar/compartir PDF/GeoJSON en Android/iOS.
