# Mapas base offline

Los agentes ven en el mapa de la app sus visitas y asignaciones sobre un **mapa base que se descarga una vez y funciona sin red**. Cada organización tiene sus propios paquetes. Los importa un administrador desde el servidor y los teléfonos los descargan con wifi.

## Qué archivo sirve

Un **MBTiles ráster**: teselas PNG, JPEG o WebP en un único archivo `.mbtiles`. Hay dos fuentes habituales:

- **Ortofoto o vuelo de dron del proyecto.** QGIS lo exporta con *Procesamiento → Generar teselas XYZ (MBTiles)*.
- **Extracto de OpenStreetMap de la zona.** Se renderiza con QGIS de la misma forma, o se obtiene ya en MBTiles ráster. Respeta la licencia ODbL: la atribución «© OpenStreetMap contributors» debe acompañar al mapa.

Los MBTiles **vectoriales** (`pbf`) se rechazan, porque la app no los dibuja.

Para que el paquete pese poco, limita la zona y los niveles de zoom. Como referencia, una finca a zoom 12–18 suele ocupar decenas de MB, y un municipio a zoom 10–16, cientos. El tope es de 4 GB (`BASEMAP_MAX_BYTES`). El teléfono comprueba su espacio libre antes de descargar.

## Importar

1. Copia el archivo al contenedor de la API:

   ```bash
   docker compose cp roble.mbtiles api:/tmp/roble.mbtiles
   ```

2. Impórtalo en nombre de un **administrador de esa organización**. La acción queda en su traza de auditoría y se aplica su permiso. No hay un usuario «del sistema».

   ```bash
   docker compose exec api python manage.py import_basemap \
       --org <uuid-de-la-organización> --by <usuario-admin> \
       --name "Finca El Roble 2026" /tmp/roble.mbtiles
   ```

   El comando valida el archivo (formato, zoom, extensión y que tenga teselas), calcula su `sha256` y lo sube al almacenamiento de objetos bajo `basemaps/`. El almacenamiento verifica el checksum al recibirlo. Si el paquete se rechaza, el motivo aparece en la salida.

3. Borra la copia temporal:

   ```bash
   docker compose exec api rm /tmp/roble.mbtiles
   ```

En la siguiente sincronización de mapas, la app ofrece el paquete a los agentes de la organización.

## Retirar

En el Admin, ve a **Mapas base**, selecciónalo y elige la acción **Retirar**. Deja de ofrecerse y los teléfonos lo borran en su siguiente sincronización.

El archivo **se conserva** en el almacenamiento porque su clave es el `sha256`: otra organización podría estar ofreciendo el mismo contenido. Si hace falta borrarlo, que lo haga el operador de la plataforma tras comprobarlo.

## Capas de referencia (GeoJSON)

Además del mapa base, una organización puede publicar **capas** que el agente ve encima: predios, lotes, vías, puntos de muestreo. Al tocar un elemento en la app, se ven sus atributos.

**Formato:** un GeoJSON (RFC 7946) en **EPSG:4326**, con puntos, líneas o polígonos, simples o múltiples. Desde QGIS: clic derecho en la capa → *Exportar → Guardar objetos como…* → formato **GeoJSON**, SRC **EPSG:4326**. Si la capa está en MAGNA-SIRGAS o UTM, se rechaza con un mensaje que explica cómo exportarla.

**Límites:** hasta 20 MB (`LAYER_MAX_BYTES`), 50 000 entidades y 2 millones de vértices. Si la capa es más grande, simplifícala en QGIS (*Vectorial → Herramientas de geometría → Simplificar*).

**Importar:**

```bash
docker compose cp predios.geojson api:/tmp/predios.geojson
docker compose exec api python manage.py import_layer \
    --org <uuid-de-la-organización> --by <usuario-admin> \
    --name "Predios 2026" /tmp/predios.geojson
docker compose exec api rm /tmp/predios.geojson
```

**Retirar:** en el Admin, ve a **Capas de referencia** y elige la acción **Retirar**.

## Seguridad

- La app **no pide teselas a servidores de terceros**, así que nadie fuera de la organización sabe dónde trabaja cada agente (Ley 1581, minimización).
- Solo los miembros de la organización ven y descargan sus paquetes. Uno de otra organización responde `404`, igual que uno inexistente (RD-11).
- La descarga es una URL prefirmada que dura una hora (`BASEMAP_DOWNLOAD_URL_TTL_SECONDS`). La app verifica el `sha256` del archivo antes de usarlo.
- Los paquetes no van por el Admin web: pasar archivos de cientos de MB por el proxy y por Django abriría la aplicación a peticiones enormes.
