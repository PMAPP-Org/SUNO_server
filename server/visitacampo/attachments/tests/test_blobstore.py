"""El puerto `BlobStore` y sus dos adaptadores. **Sin base de datos y sin red.**

Los tres bloques:

- `KeyDerivationTests` / `TraversalTests` — la derivación de claves y el
  **test de traversal explícito** que H-21 pide como "documentación de intent".
- `LocalFsBlobStoreTests` — el adaptador local, contra un directorio temporal.
- `S3BlobStoreTests` — el adaptador de producción. **Se ejercita de verdad, sin
  red**: `generate_presigned_url` de botocore es cálculo local puro, así que se
  comprueba la URL firmada real (algoritmo, caducidad y cabeceras firmadas);
  para `head`/`read_prefix`/`list_objects`/`delete`, que sí harían I/O, se
  inyecta un doble del cliente en el constructor —`S3BlobStore(config,
  client=...)` existe justo para eso— y se verifica que el adaptador pasa los
  parámetros correctos y traduce las respuestas al vocabulario del puerto.
"""

from __future__ import annotations

import base64
import hashlib
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from django.test import SimpleTestCase

from visitacampo.attachments.blobstore import (
    BlobStoreError,
    InvalidSha256,
    ObjectNotFound,
    bucket_key_for,
    checksum_header_value,
    normalize_sha256,
    sha256_from_bucket_key,
    sha256_from_checksum_header,
)
from visitacampo.attachments.localfs import LocalFsBlobStore
from visitacampo.attachments.s3 import S3BlobStore, S3Config

from .factories import JPEG_BYTES, JPEG_MIME, PNG_BYTES, PNG_MIME, digest_of

JPEG_SHA = digest_of(JPEG_BYTES)


class KeyDerivationTests(SimpleTestCase):
    def test_key_follows_adr_007(self) -> None:
        self.assertEqual(
            bucket_key_for(JPEG_SHA),
            f"blobs/{JPEG_SHA[0:2]}/{JPEG_SHA[2:4]}/{JPEG_SHA}",
        )

    def test_round_trips_through_sha256_from_bucket_key(self) -> None:
        self.assertEqual(sha256_from_bucket_key(bucket_key_for(JPEG_SHA)), JPEG_SHA)

    def test_rejects_keys_this_code_did_not_write(self) -> None:
        """El GC solo borra lo que reconoce; todo lo demás devuelve `None`."""
        for key in (
            "backups/2026-08-01.dump",  # otro uso del mismo bucket
            f"blobs/{JPEG_SHA}",  # sin particionar
            f"blobs/ff/ff/{JPEG_SHA}",  # partición que no casa con el hash
            f"blobs/{JPEG_SHA[0:2]}/{JPEG_SHA[2:4]}/{JPEG_SHA}.tmp",
            "blobs/aa/bb/no-es-un-hash",
        ):
            with self.subTest(key=key):
                self.assertIsNone(sha256_from_bucket_key(key))

    def test_checksum_header_round_trips(self) -> None:
        header = checksum_header_value(JPEG_SHA)
        self.assertEqual(base64.b64decode(header), bytes.fromhex(JPEG_SHA))
        self.assertEqual(sha256_from_checksum_header(header), JPEG_SHA)

    def test_checksum_header_parsing_rejects_garbage(self) -> None:
        for value in ("no-base64!!", base64.b64encode(b"corto").decode(), ""):
            with self.subTest(value=value):
                self.assertIsNone(sha256_from_checksum_header(value))


class TraversalTests(SimpleTestCase):
    """**Test de traversal explícito** (H-21, tarea 6 del Paso 17, RD-06).

    La recomendación literal de doc 07 es "mantener test unitario explícito de
    rechazo de traversal como documentación de intent". Esto es ese test.

    Lo que documenta: `bucket_key_for()` es la **única** función del backend
    que compone una ruta de objeto, y no acepta nada que no sean 64 caracteres
    `[0-9a-f]`. Por eso ninguna de estas entradas llega a construir una ruta —
    no es que se sanee el valor, es que se rechaza antes de mirarlo.
    """

    #: Cada entrada es una familia de ataque distinta, no una variación.
    HOSTILE = {
        "traversal relativo": "../../etc/passwd",
        "traversal encadenado": "a/../../../root/.ssh/id_rsa",
        "ruta absoluta posix": "/etc/shadow",
        "ruta absoluta windows": "C:\\Windows\\System32\\config\\SAM",
        "unc windows": "\\\\servidor\\share\\secreto",
        "traversal url-encoded": "..%2f..%2fetc%2fpasswd",
        "byte nulo": "a" * 63 + "\x00",
        "separador incrustado": "aa/bb/" + "c" * 58,
        "hash con mayusculas": JPEG_SHA.upper(),
        "hash corto": JPEG_SHA[:63],
        "hash largo": JPEG_SHA + "0",
        "no hexadecimal": "g" * 64,
        "vacio": "",
        "solo espacios": " " * 64,
        "espacio final": JPEG_SHA[:63] + " ",
    }

    def test_no_hostile_name_ever_builds_a_key(self) -> None:
        for label, value in self.HOSTILE.items():
            with self.subTest(caso=label):
                with self.assertRaises(InvalidSha256):
                    bucket_key_for(value)

    def test_normalize_rejects_non_strings(self) -> None:
        for value in (None, 42, b"a" * 64, ["a" * 64]):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(InvalidSha256):
                    normalize_sha256(value)  # type: ignore[arg-type]

    def test_the_local_adapter_never_leaves_its_root(self) -> None:
        """La misma garantía, comprobada donde la ruta se resuelve de verdad."""
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalFsBlobStore(tmp)
            for label, value in self.HOSTILE.items():
                with self.subTest(caso=label):
                    with self.assertRaises((InvalidSha256, BlobStoreError)):
                        store.head(value)
            # Y nada se ha creado fuera (ni dentro) del directorio raíz.
            self.assertEqual(list(Path(tmp).rglob("*")), [])

    def test_the_generated_download_name_never_comes_from_the_client(self) -> None:
        """RD-06: el nombre sale de un UUID + la extensión del MIME verificado.

        El API, de hecho, no tiene por dónde recibir un nombre — el contrato no
        declara ese campo en ningún esquema de adjuntos. Este test fija esa
        propiedad: dos llamadas seguidas con el mismo MIME dan nombres
        distintos, así que el nombre no puede estar derivándose de una entrada.
        """
        from visitacampo.attachments.validation import generated_filename

        first = generated_filename(JPEG_MIME)
        second = generated_filename(JPEG_MIME)
        self.assertNotEqual(first, second)
        for name in (first, second):
            self.assertTrue(name.endswith(".jpg"))
            self.assertNotIn("/", name)
            self.assertNotIn("\\", name)
            self.assertNotIn("..", name)


class LocalFsBlobStoreTests(SimpleTestCase):
    """El adaptador local: es el que sostiene toda la suite de integración."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = LocalFsBlobStore(self._tmp.name)

    def test_head_certifies_the_real_digest_not_the_key(self) -> None:
        """La propiedad que hace verificable el commit: el hash sale de los bytes."""
        # Se depositan bytes de PNG bajo la clave del JPEG: una clave que miente.
        self.store.put_object(JPEG_SHA, PNG_BYTES, PNG_MIME)
        head = self.store.head(JPEG_SHA)
        self.assertEqual(head.size, len(PNG_BYTES))
        self.assertEqual(head.sha256, hashlib.sha256(PNG_BYTES).hexdigest())
        self.assertNotEqual(head.sha256, JPEG_SHA)

    def test_head_on_a_missing_object_raises_object_not_found(self) -> None:
        with self.assertRaises(ObjectNotFound):
            self.store.head(JPEG_SHA)

    def test_head_returns_the_stored_content_type(self) -> None:
        self.store.put_object(JPEG_SHA, JPEG_BYTES, JPEG_MIME)
        self.assertEqual(self.store.head(JPEG_SHA).content_type, JPEG_MIME)

    def test_read_prefix_reads_only_the_prefix(self) -> None:
        self.store.put_object(JPEG_SHA, JPEG_BYTES, JPEG_MIME)
        self.assertEqual(self.store.read_prefix(JPEG_SHA, 4), JPEG_BYTES[:4])

    def test_read_prefix_on_a_missing_object_raises_object_not_found(self) -> None:
        with self.assertRaises(ObjectNotFound):
            self.store.read_prefix(JPEG_SHA, 4)

    def test_presigned_urls_carry_the_signed_headers(self) -> None:
        upload = self.store.presign_upload(
            sha256=JPEG_SHA, size=len(JPEG_BYTES), mime=JPEG_MIME, expires_in=900
        )
        self.assertEqual(upload.expires_in, 900)
        self.assertEqual(
            upload.required_headers,
            {
                "Content-Length": str(len(JPEG_BYTES)),
                "Content-Type": JPEG_MIME,
                "x-amz-checksum-sha256": checksum_header_value(JPEG_SHA),
            },
        )
        self.assertIn(bucket_key_for(JPEG_SHA), upload.url)

    def test_list_objects_reports_the_parsed_digest(self) -> None:
        self.store.put_object(JPEG_SHA, JPEG_BYTES, JPEG_MIME)
        listed = list(self.store.list_objects())
        self.assertEqual([entry.sha256 for entry in listed], [JPEG_SHA])
        self.assertEqual(listed[0].size, len(JPEG_BYTES))

    def test_list_objects_skips_the_metadata_sidecars(self) -> None:
        """El sidecar `.meta.json` no es un blob y el GC no debe verlo."""
        self.store.put_object(JPEG_SHA, JPEG_BYTES, JPEG_MIME)
        self.assertEqual(len(list(self.store.list_objects())), 1)

    def test_delete_is_idempotent(self) -> None:
        self.store.put_object(JPEG_SHA, JPEG_BYTES, JPEG_MIME)
        self.store.delete(JPEG_SHA)
        self.store.delete(JPEG_SHA)  # no debe lanzar
        self.assertEqual(list(self.store.list_objects()), [])

    def test_set_last_modified_lets_the_gc_be_tested(self) -> None:
        self.store.put_object(JPEG_SHA, JPEG_BYTES, JPEG_MIME)
        old = datetime.now(UTC) - timedelta(days=3)
        self.store.set_last_modified(JPEG_SHA, old)
        listed = list(self.store.list_objects())[0]
        self.assertLess(abs((listed.last_modified - old).total_seconds()), 2)


class _FakeS3Client:
    """Doble del cliente de botocore. Registra llamadas y devuelve lo pactado."""

    def __init__(self, **responses: object) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def _record(self, name: str, kwargs: dict[str, object]) -> object:
        self.calls.append((name, kwargs))
        value = self.responses.get(name)
        if isinstance(value, Exception):
            raise value
        return value

    def head_object(self, **kwargs: object) -> object:
        return self._record("head_object", kwargs)

    def get_object(self, **kwargs: object) -> object:
        return self._record("get_object", kwargs)

    def delete_object(self, **kwargs: object) -> object:
        return self._record("delete_object", kwargs)

    def get_paginator(self, name: str) -> object:
        self.calls.append(("get_paginator", {"name": name}))
        return self.responses["paginator"]


class _FakeBody:
    """Cuerpo de `get_object`, que además comprueba que se cierra (RD-05)."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.closed = False

    def read(self, length: int | None = None) -> bytes:
        return self.data[:length] if length is not None else self.data

    def close(self) -> None:
        self.closed = True


class _FakePaginator:
    def __init__(self, pages: list[dict[str, object]]) -> None:
        self.pages = pages

    def paginate(self, **_: object) -> list[dict[str, object]]:
        return self.pages


class _NotFound(Exception):
    response = {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}


class _Boom(Exception):
    response = {"Error": {"Code": "InternalError"}, "ResponseMetadata": {"HTTPStatusCode": 500}}


CONFIG = S3Config(
    bucket="visitacampo-test",
    endpoint_url="https://cuenta.r2.cloudflarestorage.com",
    access_key_id="AKIAIOSFODNN7EXAMPLE",
    secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    region="auto",
)


class S3PresignTests(SimpleTestCase):
    """Firma **real** de botocore, sin red: `generate_presigned_url` no hace I/O."""

    def setUp(self) -> None:
        self.store = S3BlobStore(CONFIG)

    def test_upload_url_signs_length_type_and_checksum(self) -> None:
        upload = self.store.presign_upload(
            sha256=JPEG_SHA, size=len(JPEG_BYTES), mime=JPEG_MIME, expires_in=900
        )
        query = parse_qs(urlparse(upload.url).query)

        self.assertEqual(query["X-Amz-Algorithm"], ["AWS4-HMAC-SHA256"])
        self.assertEqual(query["X-Amz-Expires"], ["900"])
        signed = query["X-Amz-SignedHeaders"][0].split(";")
        # Estas tres son las que ADR-007 punto 3-4 exige fijar en la firma:
        # sin ellas, la URL prefirmada admitiría cualquier contenido.
        self.assertIn("content-length", signed)
        self.assertIn("content-type", signed)
        self.assertIn("x-amz-checksum-sha256", signed)
        self.assertTrue(query["X-Amz-Signature"][0])

    def test_upload_url_targets_the_content_addressed_key(self) -> None:
        upload = self.store.presign_upload(sha256=JPEG_SHA, size=1, mime=JPEG_MIME, expires_in=900)
        self.assertIn(bucket_key_for(JPEG_SHA), urlparse(upload.url).path)

    def test_required_headers_are_what_the_client_must_send(self) -> None:
        upload = self.store.presign_upload(sha256=JPEG_SHA, size=99, mime=PNG_MIME, expires_in=900)
        self.assertEqual(
            upload.required_headers,
            {
                "Content-Length": "99",
                "Content-Type": PNG_MIME,
                "x-amz-checksum-sha256": checksum_header_value(JPEG_SHA),
            },
        )

    def test_download_url_fixes_type_and_generated_filename(self) -> None:
        url = self.store.presign_download(
            sha256=JPEG_SHA, expires_in=300, mime=JPEG_MIME, filename="abc.jpg"
        )
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["X-Amz-Expires"], ["300"])
        self.assertEqual(query["response-content-type"], [JPEG_MIME])
        self.assertIn("abc.jpg", query["response-content-disposition"][0])

    def test_two_signatures_of_different_content_differ(self) -> None:
        """Prueba de que se está firmando de verdad y no devolviendo una plantilla."""
        other = digest_of(PNG_BYTES)
        first = self.store.presign_upload(sha256=JPEG_SHA, size=1, mime=JPEG_MIME, expires_in=900)
        second = self.store.presign_upload(sha256=other, size=1, mime=JPEG_MIME, expires_in=900)
        self.assertNotEqual(
            parse_qs(urlparse(first.url).query)["X-Amz-Signature"],
            parse_qs(urlparse(second.url).query)["X-Amz-Signature"],
        )


class S3ObjectAccessTests(SimpleTestCase):
    """`head`/`read_prefix`/`list_objects`/`delete` con un doble del cliente."""

    def test_head_requests_checksum_mode_and_decodes_it(self) -> None:
        client = _FakeS3Client(
            head_object={
                "ContentLength": 1234,
                "ContentType": JPEG_MIME,
                "ChecksumSHA256": checksum_header_value(JPEG_SHA),
                "LastModified": datetime(2026, 8, 1, tzinfo=UTC),
            }
        )
        head = S3BlobStore(CONFIG, client=client).head(JPEG_SHA)

        self.assertEqual(head.size, 1234)
        self.assertEqual(head.content_type, JPEG_MIME)
        self.assertEqual(head.sha256, JPEG_SHA)
        name, kwargs = client.calls[0]
        self.assertEqual(name, "head_object")
        # Sin `ChecksumMode=ENABLED`, S3/R2 no devuelven el checksum y el
        # commit rechazaría **todo** por "checksum no certificado".
        self.assertEqual(kwargs["ChecksumMode"], "ENABLED")
        self.assertEqual(kwargs["Key"], bucket_key_for(JPEG_SHA))

    def test_head_without_checksum_reports_none(self) -> None:
        client = _FakeS3Client(head_object={"ContentLength": 1, "ContentType": "image/jpeg"})
        self.assertIsNone(S3BlobStore(CONFIG, client=client).head(JPEG_SHA).sha256)

    def test_head_translates_404_to_object_not_found(self) -> None:
        client = _FakeS3Client(head_object=_NotFound())
        with self.assertRaises(ObjectNotFound):
            S3BlobStore(CONFIG, client=client).head(JPEG_SHA)

    def test_head_translates_other_failures_to_blob_store_error(self) -> None:
        client = _FakeS3Client(head_object=_Boom())
        with self.assertRaises(BlobStoreError) as raised:
            S3BlobStore(CONFIG, client=client).head(JPEG_SHA)
        self.assertNotIsInstance(raised.exception, ObjectNotFound)

    def test_read_prefix_uses_a_range_and_closes_the_stream(self) -> None:
        body = _FakeBody(JPEG_BYTES)
        client = _FakeS3Client(get_object={"Body": body})
        data = S3BlobStore(CONFIG, client=client).read_prefix(JPEG_SHA, 16)

        self.assertEqual(data, JPEG_BYTES[:16])
        self.assertEqual(client.calls[0][1]["Range"], "bytes=0-15")
        # RD-05: el stream se cierra siempre. Es la disciplina que H-05 echaba
        # en falta, aplicada también donde el objeto es diminuto.
        self.assertTrue(body.closed)

    def test_list_objects_parses_digests_and_skips_foreign_keys(self) -> None:
        moment = datetime(2026, 8, 1, tzinfo=UTC)
        client = _FakeS3Client(
            paginator=_FakePaginator(
                [
                    {
                        "Contents": [
                            {"Key": bucket_key_for(JPEG_SHA), "Size": 10, "LastModified": moment},
                            {"Key": "blobs/otra-cosa.txt", "Size": 3, "LastModified": moment},
                        ]
                    }
                ]
            )
        )
        listed = list(S3BlobStore(CONFIG, client=client).list_objects())
        self.assertEqual([entry.sha256 for entry in listed], [JPEG_SHA, None])

    def test_delete_swallows_not_found(self) -> None:
        client = _FakeS3Client(delete_object=_NotFound())
        S3BlobStore(CONFIG, client=client).delete(JPEG_SHA)  # no debe lanzar

    def test_delete_propagates_real_failures(self) -> None:
        client = _FakeS3Client(delete_object=_Boom())
        with self.assertRaises(BlobStoreError):
            S3BlobStore(CONFIG, client=client).delete(JPEG_SHA)
