"""App `core`: modelo de datos núcleo de VisitaCampo (Paso 5).

Contiene las tablas de doc 08 §4.4 y el andamiaje multi-tenant de ADR-011.
No contiene lógica de negocio: el scoping (`scoped_qs`) llega en Paso 6, el
protocolo de sync en Pasos 8-9 y la cola de jobs en Paso 19.
"""
