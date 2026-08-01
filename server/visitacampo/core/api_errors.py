"""El **cuerpo de error uniforme** del contrato, instalado en la API (doc 08 §6).

`core/policies.py` (Paso 6) dejó dicho que "el manejador que renderiza ese
cuerpo se instala con la capa de API (Paso 7)". Esto es ese manejador.

Todas las respuestas de error de `/v1/*` tienen la misma forma
(`components.schemas.Error`)::

    {"code": "invalid_credentials", "message": "...", "details": null}

`code` es lo estable y legible por máquina: el cliente lo traduce a un mensaje
de UI y **nunca** muestra `message` crudo al agente de campo (patrón
`CloudError` del contrato). Por eso el `code` no puede improvisarse endpoint a
endpoint: o lo trae la excepción (`InvalidCredentials.code`,
`PolicyDenied.code`) o sale del mapa de este módulo.

**Redacción (RD-12)**: los errores de validación de Pydantic incluyen un campo
`input` con **el valor que falló**. Devolverlo tal cual significaría que un
`password` demasiado largo vuelve al cliente —y a los logs de quien lo
registre— dentro del cuerpo de error. Aquí se emite solo `field` y `reason`:
dónde falló y por qué, nunca con qué.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI
from ninja.errors import AuthenticationError, HttpError
from ninja.errors import ValidationError as NinjaValidationError

__all__ = ["error_body", "redacted_validation_details", "register_error_handlers"]

#: `code` por defecto según el estado HTTP, para las excepciones que no traen
#: uno propio. Los valores son los de `components.schemas.ErrorCode`; el
#: contrato no define ningún código para 400, así que se reutiliza
#: `validation_error`, que es lo que un 400 significa en esta API.
CODE_BY_STATUS: dict[int, str] = {
    400: "validation_error",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
}

DEFAULT_CODE = "internal_error"


def error_body(code: str, message: str, details: Any | None = None) -> dict[str, Any]:
    """Construye el cuerpo `Error` del contrato.

    `details` se **omite** cuando no hay nada que contar, en vez de emitirse
    como `null`: el contrato solo declara `code` y `message` como `required`,
    y así la respuesta de un login inválido es idéntica byte a byte venga de
    donde venga (un `details: null` de más sería una diferencia sin
    significado que algún cliente acabaría interpretando).
    """
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return body


def redacted_validation_details(errors: list[Any]) -> dict[str, Any]:
    """`loc` + `type` de cada error, **sin** el valor que lo provocó (RD-12).

    Se envuelve en un objeto porque `Error.details` es `type: [object, 'null']`
    en el contrato, no un array.

    Es **público** porque el Paso 8 lo reutiliza: una operación de push cuyo
    payload no valida se devuelve como `rejected` con este mismo `details`, y
    la redacción de RD-12 tiene que ser la misma la produzca el manejador
    global de `422` o el bucle de sync. Duplicarla sería garantizar que un día
    una de las dos copias empiece a filtrar el `input` de Pydantic.
    """
    return {
        "errors": [
            {
                "field": ".".join(str(part) for part in (error.get("loc") or ())),
                "reason": str(error.get("type", "invalid")),
            }
            for error in errors
        ]
    }


def register_error_handlers(api: NinjaAPI) -> None:
    """Instala los manejadores en una `NinjaAPI`.

    Se hace como función explícita —y no con decoradores sueltos en
    `config/api.py`— para que el contrato de errores viva en un solo archivo y
    para que cualquier otra `NinjaAPI` futura (p. ej. la del back-office) lo
    herede llamando a una línea.
    """

    @api.exception_handler(AuthenticationError)
    def _on_authentication_error(request: HttpRequest, exc: Exception) -> HttpResponse:
        """401 uniforme.

        Sin `WWW-Authenticate` con `realm` ni detalle del motivo: el contrato
        describe el 401 como "credenciales ausentes, inválidas o access token
        caducado", los tres indistinguibles a propósito.
        """
        return api.create_response(
            request,
            error_body("unauthenticated", "Authentication credentials are missing or invalid."),
            status=401,
        )

    @api.exception_handler(NinjaValidationError)
    def _on_validation_error(request: HttpRequest, exc: Exception) -> HttpResponse:
        errors = getattr(exc, "errors", []) or []
        return api.create_response(
            request,
            error_body(
                "validation_error",
                "Request body failed schema validation.",
                redacted_validation_details(errors),
            ),
            status=422,
        )

    @api.exception_handler(RequestDataTooBig)
    def _on_request_data_too_big(request: HttpRequest, exc: Exception) -> HttpResponse:
        """413 en vez del 400 genérico de `SuspiciousOperation` (Paso 8).

        Django corta la lectura del cuerpo en `DATA_UPLOAD_MAX_MEMORY_SIZE` y
        lanza `RequestDataTooBig`, que sin este manejador acabaría en el `400`
        de `SuspiciousOperation`. El contrato es explícito: un cuerpo por
        encima del límite duro responde `413 payload_too_large` y el cliente
        **parte el lote** (`components/responses/PayloadTooLarge`). Con un
        `400` el cliente no sabría que dividir el lote es la salida, y la
        outbox se quedaría atascada reintentando lo mismo.
        """
        return api.create_response(
            request,
            error_body("payload_too_large", "Request body exceeds the maximum allowed size."),
            status=413,
        )

    @api.exception_handler(HttpError)
    def _on_http_error(request: HttpRequest, exc: Exception) -> HttpResponse:
        """Cualquier `HttpError`, con el `code` que traiga o el de su estado.

        `PolicyDenied` (Paso 6), `InvalidCredentials` y `DeviceUnavailable`
        (Paso 7) son subclases de `HttpError` que declaran `code`; el
        `getattr` las cubre a todas sin que este módulo tenga que importarlas
        ni conocerlas.
        """
        status = getattr(exc, "status_code", 500)
        code = getattr(exc, "code", None) or CODE_BY_STATUS.get(status, DEFAULT_CODE)
        return api.create_response(request, error_body(code, str(exc)), status=status)
