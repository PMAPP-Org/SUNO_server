"""Back-office de VisitaCampo (Paso 24): la lógica que hay detrás del Django Admin.

`core/admin.py` solo pinta: listas, filtros, formularios y acciones. Lo que
cambia datos vive aquí, en funciones que reciben un `RequestContext` y pasan
por las mismas piezas que la API:

- **aislamiento**: `scoped_qs` (ADR-011, RD-11). Un miembro del personal solo
  ve y toca su organización (`base.OrgScopedAdmin`);
- **autorización**: las políticas de `core/policies.py` (ADR-013), nunca los
  permisos nativos del Admin a secas. Un permiso de Django dice "puede abrir
  esta pantalla"; la política dice "puede hacer esto con esta fila";
- **auditoría**: cada acción con efecto deja un `AuditEvent` (ISO 27001 A.8.15).

**El superusuario** es el operador de la plataforma: ve todas las
organizaciones, porque alguien tiene que dar de alta la primera. Pero **las
acciones siguen sujetas a política**: su contexto es el de su propia
organización, y en una ajena ninguna política le concede nada. Ver no es
actuar (deny-by-default).
"""
