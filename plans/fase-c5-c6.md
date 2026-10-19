# Fases C5 y C6

## Alcance y decisiones

- C5: grupos repetibles acotados, valores iniciales literales, captura QR en
  móvil e importación XLSForm en el servidor como una nueva versión inmutable.
- C6: exportación geográfica GeoJSON y GeoPackage desde el administrador, PDF
  individual de visita; exportar una visita local desde la app sin conexión.
- Conservar SQLite/JSON de respuestas, aislamiento por organización, políticas
  de autoría, auditoría y paridad de validación. No desplegar una release como
  efecto de preparar estas funcionalidades.
- Extender el catálogo declarativo existente evita un segundo motor de
  formularios. El importador traduce un subconjunto explícito de XLSForm y
  rechaza columnas/expresiones no soportadas; no ejecuta fórmulas ni código.
- Los valores por defecto se aplican una vez al crear una visita o fila, nunca
  al editar datos históricos ni tras borrar una respuesta.
- Repeticiones: hasta 100 filas por grupo, hasta 3 niveles y 300 campos totales.
  La visibilidad de los campos de una fila referencia únicamente hermanos
  anteriores. Los errores conservan rutas de grupo/fila/campo.
- Las exportaciones se construyen desde la selección autorizada y conservan
  geometrías WGS84, respuestas y metadatos. No consultan imágenes o URLs remotas.

## Secuencia y verificación

- [ ] Extender definición/validación de servidor y casos de paridad.
- [ ] Importar XLSForm con límites de archivo, errores útiles y publicación autorizada.
- [ ] Extender parser, editor de repeticiones, defaults y QR en Flutter.
- [ ] Verificar captura/edición/sincronización de campos y adjuntos repetidos.
- [ ] Exportaciones GeoJSON/GeoPackage y PDF con controles de acceso.
- [ ] Exportación local y compartir desde el detalle móvil.
- [ ] Pruebas funcionales, aislamiento, límites y renderizado visual de PDF.
- [ ] Actualizar contrato fijado, documentación y CI de ambos repositorios.

La prueba física de cámara QR, GPS y compartir archivos sigue requiriendo un
teléfono. El OTP y la sincronización con la app cerrada no forman parte de estos
bloques.
