"""Formularios declarativos: definición versionada + validación (Paso 20, ADR-012).

**LA REGLA DE ARQUITECTURA DE ESTE PAQUETE, ANTES QUE NADA** (ADR-012, RD-06,
corrección de H-06 en su forma más severa):

    Una plantilla de formulario es **dato**, nunca código. En este paquete
    —y en el renderer de Flutter que lo consume (Paso 21)— está **prohibido**:
    `eval`, `exec`, `compile`, `__import__`, `getattr` sobre nombres que vengan
    de la plantilla, `pickle`/`marshal`, plantillas de texto que se rendericen
    como expresiones, JavaScript embebido, QML, Dart dinámico, y cualquier otro
    mecanismo que convierta un string de la definición en algo que se ejecuta.
    Ni siquiera existe un *parser* de expresiones en texto: el DSL de
    visibilidad condicional es JSON con una etiqueta `op` de una **allow-list
    cerrada** (ver `visibility.py`), de modo que no hay lenguaje que analizar ni
    gramática que se pueda desbordar. Lo que no está en la allow-list no se
    "interpreta con cuidado": se **rechaza al validar la plantilla** y nunca
    llega a evaluarse.

    Esta regla no es una recomendación de estilo: es lo que hace que la clase
    de vulnerabilidad de H-06 (plugin QML con path traversal ⇒ RCE) no tenga
    superficie donde existir. Un PR que introduzca cualquiera de esos
    mecanismos aquí debe rechazarse aunque "solo sea para un caso".

**Las cuatro piezas** (un archivo, una responsabilidad):

- `visibility.py` — el **DSL** de visibilidad condicional: su vocabulario
  cerrado, su validación estructural y su evaluador puro (sin I/O, sin efectos
  secundarios, sin excepciones).
- `definition.py` — el **meta-schema**: qué es una plantilla bien formada
  (catálogo cerrado de tipos de campo, etiquetas i18n, obligatoriedad, rangos).
  Se aplica al crear/publicar una versión.
- `answers.py` — la validación de `visit.answers` **contra la definición de su
  propia versión de plantilla**. La ejecuta el servidor en el push
  (`sync/services.py`); el cliente nunca es la fuente de verdad (RD-03).
- `services.py` — publicar una versión nueva y la regla de **inmutabilidad**
  (una versión publicada no se edita: se publica otra).

**Decisión de superficie de API (tarea 4 del Paso 20)**: el CRUD de plantillas
**no se expone como API pública en el MVP**. El contrato de Paso 1
(`server/openapi/v1.yaml`) no declara ninguna ruta de escritura de
`form_template`/`form_template_version` —solo las entrega por el pull de
sync—, así que exponer una API aquí sería añadir superficie fuera de contrato
para un caso de uso (back-office) que el plan ya asigna al **Django Admin
(Paso 24)**. La vía sancionada de escritura es por tanto
`forms.services.publish_version()`, que el Admin llamará; y el camino de
lectura para el dispositivo sigue siendo el pull (Paso 9, que ya entrega
`form_template_version`, ver `sync/pull.py::_ENTITY_MODELS`). Si algún día
hiciera falta la API, se añade al contrato primero — nunca al revés.

**Sobre "JSON Schema" (ADR-012) y qué es aquí el schema.** El meta-schema
—"¿es esta plantilla una plantilla bien formada?"— **sí** se emite como JSON
Schema real (`definition.META_SCHEMA`, derivado de los modelos Pydantic
sellados de este paquete): es un contrato entre sistemas y conviene poder
publicarlo. La validación de `answers`, en cambio, se deriva **directamente de
la definición versionada** en vez de compilar un segundo documento JSON Schema
por plantilla. El motivo es explícito: la definición ya es JSON declarativo,
versionado e inmutable, y viaja al cliente por el pull; generar además un JSON
Schema equivalente crearía **dos fuentes de verdad para las mismas reglas**
(una de las cuales podría divergir en silencio), y aun así no bastaría, porque
lo que un JSON Schema no puede expresar —visibilidad condicional, catálogo de
tipos de campo, etiquetas i18n— es justo lo que el renderer necesita
interpretar. Servidor (aquí) y cliente (Paso 21) derivan su validación de la
**misma** definición, que es la propiedad que ADR-012 pide.

Este `__init__` no importa nada a propósito: `core/models/catalog.py` importa
`forms.definition` para validar en `clean()`, y un import de `services.py`
—que sí importa modelos— cerraría el ciclo.
"""
