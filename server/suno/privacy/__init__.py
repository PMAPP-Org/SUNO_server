"""Autorización del titular de los datos personales (Ley 1581 de 2012).

`POST /v1/consents`: la app envía la autorización que el agente dio en el aviso
de privacidad (finalidades aceptadas, versión del aviso, cuándo) y el servidor
la guarda como prueba (`core.models.PrivacyConsent`) y la audita.
"""
