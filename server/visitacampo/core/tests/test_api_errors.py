"""Ninguna respuesta de `/v1/*` sale sin el cuerpo de error del contrato.

Hallazgo I-1 de la revisión del Paso 8. El manejador por defecto de Django
Ninja para `Exception` **vuelve a lanzar** cuando `DEBUG=False` ("let django
deal with it", `ninja/errors.py::_default_exception`), así que todo lo que no
tuviera un manejador propio salía como la página de error `500` de Django: sin
`code`, sin `message` y sin nada que el cliente sepa parsear. Para un endpoint
de sincronización eso es peor que un error: la outbox del agente de campo
reintenta un `500` para siempre.

**Sin base de datos, y de verdad.** Los casos de cuerpo mal formado se prueban
de extremo a extremo contra `/v1/auth/login` —que es `auth=None` y por tanto no
consulta nada— porque el cuerpo se parsea *antes* de que la vista corra; el
resto se prueba sobre `register_error_handlers` directamente, que es donde vive
la lógica.
"""

from __future__ import annotations

import json

from django.core.exceptions import RequestDataTooBig
from django.db import DatabaseError, DataError, IntegrityError, OperationalError
from django.http import HttpResponse
from django.http.multipartparser import MultiPartParserError
from django.test import Client, RequestFactory, SimpleTestCase
from ninja import NinjaAPI
from ninja.errors import AuthenticationError

from visitacampo.core.api_errors import register_error_handlers

#: El único endpoint del contrato que acepta un cuerpo JSON sin autenticar, y
#: por tanto el único donde un cuerpo ilegible se puede probar sin tocar la BD.
#: Da igual cuál sea: el fallo ocurre en el parseo, antes de la vista.
BODY_ENDPOINT = "/v1/auth/login"


def _api() -> NinjaAPI:
    """Una `NinjaAPI` con exactamente los manejadores que instala `config/api.py`."""
    api = NinjaAPI(urls_namespace="test-api-errors")
    register_error_handlers(api)
    return api


def _handle(exc: Exception) -> tuple[int, dict[str, object]]:
    """Estado y cuerpo con que la API responde a `exc`.

    Pasa por `api.on_exception`, que es el mismo punto de entrada que usa
    `Operation.run`, así que se ejercita también la resolución por MRO: es la
    que hace que un `json.JSONDecodeError` acabe en el manejador de `ValueError`
    sin que nadie lo registre por su nombre.
    """
    response: HttpResponse = _api().on_exception(RequestFactory().post(BODY_ENDPOINT), exc)
    return response.status_code, json.loads(response.content)


class UniformBodyTests(SimpleTestCase):
    """Todo lo que este módulo maneja responde `{code, message}`, sin excepción."""

    CASES: tuple[tuple[str, Exception, int, str], ...] = (
        (
            "json malformado",
            json.JSONDecodeError("Expecting value", "{", 0),
            400,
            "validation_error",
        ),
        (
            "valor no parseable",
            ValueError("Exceeds the limit (4300 digits)"),
            400,
            "validation_error",
        ),
        (
            "utf-8 inválido",
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
            400,
            "validation_error",
        ),
        (
            "anidamiento extremo",
            RecursionError("maximum recursion depth exceeded"),
            400,
            "validation_error",
        ),
        ("error de BD", DatabaseError("boom"), 500, "internal_error"),
        ("jsonb rechazado", DataError("invalid input syntax"), 500, "internal_error"),
        ("violación de integridad", IntegrityError("null value"), 500, "internal_error"),
        ("BD caída", OperationalError("connection lost"), 500, "internal_error"),
        ("cuerpo demasiado grande", RequestDataTooBig(), 413, "payload_too_large"),
        ("sin credenciales", AuthenticationError(), 401, "unauthenticated"),
        (
            "multipart sin boundary",
            MultiPartParserError("Invalid boundary in multipart: None"),
            400,
            "validation_error",
        ),
    )

    def test_every_handled_exception_produces_the_contract_body(self) -> None:
        for label, exc, expected_status, expected_code in self.CASES:
            with self.subTest(case=label):
                status, body = _handle(exc)
                self.assertEqual(status, expected_status)
                self.assertEqual(body["code"], expected_code)
                self.assertTrue(body["message"])
                self.assertLessEqual(set(body), {"code", "message", "details"})


class LeakTests(SimpleTestCase):
    """RD-12: el cuerpo dice dónde y por qué, nunca con qué."""

    def test_a_database_error_never_echoes_the_driver_message(self) -> None:
        """El texto de PostgreSQL arrastra SQL, nombres de columna y valores."""
        secreto = 'DETAIL: Key (id)=(...) already exists en columna "answers"'
        _, body = _handle(DataError(secreto))
        self.assertNotIn(secreto, json.dumps(body))
        self.assertNotIn("answers", json.dumps(body))

    def test_a_parse_error_never_echoes_the_body_fragment(self) -> None:
        secreto = "contrasena-del-agente"
        _, body = _handle(ValueError(f"could not parse {secreto}"))
        self.assertNotIn(secreto, json.dumps(body))


class MalformedBodyOverHttpTests(SimpleTestCase):
    """Los tres casos reales, por HTTP, contra la API montada de verdad.

    Es lo que cierra el hallazgo: no basta con que el manejador exista, tiene
    que **alcanzarse**. Django Ninja parsea el cuerpo dentro del
    `try/except Exception` de `Operation.run`, y estos tests lo comprueban en
    vez de suponerlo.
    """

    def _post(self, body: bytes) -> HttpResponse:
        return Client().post(BODY_ENDPOINT, data=body, content_type="application/json")

    def _assert_structured_400(self, response: HttpResponse) -> None:
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response["Content-Type"], "application/json; charset=utf-8")
        body = json.loads(response.content)
        self.assertEqual(body["code"], "validation_error")
        self.assertTrue(body["message"])

    def test_malformed_json_is_a_400_not_a_500(self) -> None:
        for body in (b"{", b"not json at all", b'{"a": }', b'{"a": 1,}', b'{"a": 1} sobra'):
            with self.subTest(body=body):
                self._assert_structured_400(self._post(body))

    def test_an_empty_body_is_a_422_and_eso_es_correcto(self) -> None:
        """Un cuerpo vacío **no** es un cuerpo ilegible, y Ninja lo distingue.

        Ninja no llama al parser cuando no hay bytes: pasa un objeto vacío al
        esquema, que responde `422` con los campos que faltan. Es más útil que
        un `400` genérico —le dice al cliente *qué* falta— y se fija aquí para
        que el manejador nuevo de `ValueError` no se lo trague algún día.
        """
        response = self._post(b"")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(json.loads(response.content)["code"], "validation_error")

    def test_an_absurdly_long_integer_is_a_400_not_a_500(self) -> None:
        """CPython corta la conversión decimal en 4300 dígitos (`ValueError`)."""
        self._assert_structured_400(self._post(b'{"a": ' + b"9" * 4_400 + b"}"))

    def test_extreme_nesting_is_a_400_not_a_500(self) -> None:
        """El decodificador de `json` es recursivo: agota la pila al parsear."""
        depth = 100_000
        self._assert_structured_400(self._post(b"[" * depth + b"]" * depth))

    def test_a_body_that_is_not_valid_utf8_is_a_400_not_a_500(self) -> None:
        self._assert_structured_400(self._post(b'{"a": "\xff\xfe"}'))

    def test_a_well_formed_body_does_not_take_this_path(self) -> None:
        """La red de seguridad no puede tragarse una request legítima.

        Un cuerpo JSON válido pero incompleto para el esquema tiene que seguir
        dando el `422` de validación del contrato, no el `400` de "no se pudo
        parsear": son dos fallos distintos y el cliente los trata distinto.
        """
        response = self._post(b'{"username": "agente"}')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(json.loads(response.content)["code"], "validation_error")


class GzipBodyTests(SimpleTestCase):
    """`Content-Encoding: gzip` sigue **sin implementar** (ver §6.4 del reporte).

    El contrato lo admite para `POST /v1/sync/push` y ni Django ni Caddy
    descomprimen cuerpos de request, así que hoy los bytes comprimidos llegan
    crudos al parser de JSON. Este test no valida gzip: fija **lo que pasa
    mientras tanto**, que con I-1 corregido es un `400` estructurado que el
    cliente puede leer, y no el `500` desnudo de antes. Si algún día se
    implementa la descompresión acotada, este test fallará — y eso es
    exactamente lo que debe hacer.
    """

    def test_a_gzipped_body_fails_with_a_structured_400(self) -> None:
        import gzip

        compressed = gzip.compress(b'{"username": "agente", "password": "x"}')
        response = Client().post(
            BODY_ENDPOINT,
            data=compressed,
            content_type="application/json",
            HTTP_CONTENT_ENCODING="gzip",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)["code"], "validation_error")
