"""Adjuntos: puerto `BlobStore`, presign/commit verificado y descarga (Paso 17, ADR-007).

**Paquete Python, no app de Django** — mismo criterio que `visitacampo/forms/`
(Paso 20): los modelos `Blob` y `VisitAttachment` ya viven en
`core/models/visit.py` (Paso 5) y no se añade ninguna tabla nueva, así que no
hay migraciones que registrar ni `AppConfig` que instalar. Lo único que este
paquete aporta a `INSTALLED_APPS` sería ruido.

**Las cuatro invariantes de este paquete**, que ningún cambio puede relajar:

1. **Los bytes nunca pasan por el API** (ADR-007 punto 3). El dispositivo hace
   `PUT` directo al object storage con una URL prefirmada; el servidor solo
   firma, hace `HEAD` de verificación y lee los **primeros bytes** para el
   sniffing. No existe ningún camino en el que el proceso Django tenga un
   adjunto entero en memoria — que es la clase de fuga de recursos de **H-05**
   (multipart armado en memoria en el cliente QField). Aquí no se mitiga: no
   hay superficie donde reproducirla.

2. **Las rutas se derivan del hash, nunca de un nombre del cliente** (RD-06,
   **H-06**/**H-21**). `blobstore.bucket_key_for()` es la **única** función que
   construye una clave de objeto, y solo acepta un `sha256` que haya pasado por
   `normalize_sha256()` — 64 caracteres `[0-9a-f]`, sin excepciones. Un nombre
   con `../`, una ruta absoluta o un byte nulo no "se limpia": no valida como
   sha256 y muere antes de tocar nada. El nombre de descarga que se le sugiere
   al navegador se **genera aquí** (`{uuid}.{ext}`, `validation.
   generated_filename`) y no deriva de ninguna entrada del cliente. El API, de
   hecho, **no acepta ningún nombre de archivo**: el contrato del Paso 1 no
   declara ese campo y este paso no lo inventa.

3. **El blob se registra solo tras verificar el objeto real** (ADR-007 punto
   4). `services.commit()` compara contra el `HEAD` del store: tamaño exacto,
   checksum SHA-256 **certificado por el propio object storage** (ver
   `s3.py`), `Content-Type` declarado y *magic bytes* leídos del objeto. Sin
   esa verificación, una URL prefirmada sería una vía para inyectar contenido
   arbitrario bajo un hash que el servidor daría por bueno.

4. **El registro es un `INSERT ... ON CONFLICT DO NOTHING`** sobre `sha256`, sin
   `select_for_update` (RD-08). No hay ningún campo mutable que actualizar, así
   que dos dispositivos que confirmen el mismo contenido a la vez no compiten
   por nada: el segundo `INSERT` no hace nada y ambos leen la misma fila. El
   lost-update de `File.latest_version` (**H-08**) no tiene aquí forma posible.

**Los archivos** (uno por responsabilidad):

- `blobstore.py` — el **puerto**: tipos de datos, errores, derivación de claves
  y el `Protocol` que ambos adaptadores implementan.
- `s3.py` — `S3BlobStore`, el adaptador de producción (Cloudflare R2 por su API
  compatible con S3, vía `boto3`).
- `localfs.py` — `LocalFsBlobStore`, el adaptador de tests y desarrollo sin red.
- `storage.py` — la fábrica que elige adaptador según settings.
- `validation.py` — allow-list de MIME, *sniffing* de magic bytes, límites de
  tamaño y generación local de nombres.
- `services.py` — la lógica de presign / commit / descarga y el GC de subidas
  nunca confirmadas.
- `schemas.py` / `api.py` — la traducción al contrato y el router de Ninja.

Este `__init__` no importa nada a propósito: `api.py` se monta desde
`config/api.py` y los servicios se importan por ruta completa, así que un
import aquí solo crearía trabajo en el arranque y riesgo de ciclos.
"""
