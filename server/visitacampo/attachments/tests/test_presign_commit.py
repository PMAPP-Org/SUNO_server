"""`presign` / `commit` / `url` de extremo a extremo (comandos de verificación del Paso 17).

Los tres casos que el plan enumera literalmente:

  (a) presign de un `sha256` ya existente → `already_present: true`, sin URL;
  (b) commit con checksum que no coincide con el `HEAD` real → rechazado con
      `attachment_checksum_mismatch`, **y el `Blob` no se registra**;
  (c) commit válido → `Blob` creado, idempotente ante doble commit.

Más la autorización de la descarga (RD-11: `404`, nunca `403`), los límites de
tamaño y el sniffing sobre el objeto real.

**Se ejecutan contra el `LocalFsBlobStore`**, con `override_settings`. No es un
mock: es el otro adaptador real del mismo puerto, con `head()` calculando el
SHA-256 de los bytes que hay en disco igual que R2 lo calcula de los que
recibe. Lo que estos tests no cubren —firmar contra R2 de verdad— lo cubre
`test_blobstore.py`, que ejercita la firma real de botocore sin red.
"""

from __future__ import annotations

import tempfile
import unittest
import uuid

from django.test import TestCase, override_settings

from visitacampo.attachments.localfs import LocalFsBlobStore
from visitacampo.core.models import Blob, Role, VisitAttachment
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.serverinfo import capabilities as caps

from .factories import (
    DISGUISED_BYTES,
    JPEG_BYTES,
    JPEG_MIME,
    PNG_BYTES,
    PNG_MIME,
    build_scenario,
    commit_url,
    digest_of,
    download_url,
    make_visit,
    presign_body,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

PRESIGN_URL = "/v1/attachments/presign"
JPEG_SHA = digest_of(JPEG_BYTES)


class _AttachmentsTestCase(TestCase):
    """Escenario con un `LocalFsBlobStore` propio por test.

    El directorio temporal se crea en `setUp` y se destruye en el `cleanup`, de
    modo que ningún test hereda objetos de otro — que es justo lo que haría
    invisible un fallo de deduplicación.
    """

    role = Role.FIELD_AGENT

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        overrides = override_settings(
            STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name
        )
        overrides.enable()
        self.addCleanup(overrides.disable)

        self.scenario = build_scenario(role=self.role, username=f"agente-{uuid.uuid4().hex[:8]}")
        # El mismo directorio que resuelve `get_blob_store()` por settings:
        # `LocalFsBlobStore` no guarda estado propio, así que apuntar dos
        # instancias a la misma raíz es apuntar al mismo almacenamiento.
        # Se construye aquí y no por la fábrica para tener el tipo concreto:
        # `put_object()` es un método de prueba y no pertenece al puerto.
        self.store = LocalFsBlobStore(self._tmp.name)

    # --- helpers ------------------------------------------------------------

    def presign(self, **kwargs):
        return self.client_post(PRESIGN_URL, presign_body(**kwargs))

    def client_post(self, url: str, body: dict):
        return self.scenario.client.post(
            url, data=body, content_type="application/json", **self.scenario.auth()
        )

    def upload(self, data: bytes, mime: str = JPEG_MIME, sha256: str | None = None):
        """Simula el `PUT` directo del dispositivo al object storage."""
        self.store.put_object(sha256 or digest_of(data), data, mime)

    def commit(self, sha256: str, size: int, mime: str = JPEG_MIME):
        return self.client_post(commit_url(sha256), {"size": size, "mime": mime})


@skip_without_db
class PresignTests(_AttachmentsTestCase):
    def test_presign_of_new_content_returns_a_signed_url(self) -> None:
        response = self.presign()
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertFalse(body["already_present"])
        self.assertTrue(body["upload_url"])
        self.assertEqual(body["expires_in"], 900)
        self.assertEqual(
            set(body["required_headers"]),
            {"Content-Length", "Content-Type", "x-amz-checksum-sha256"},
        )
        self.assertIn("server_time", body)

    def test_presign_of_existing_content_deduplicates(self) -> None:
        """**Caso (a) del plan**: `already_present: true` y ninguna URL nueva."""
        Blob.objects.create(
            sha256=JPEG_SHA,
            size=len(JPEG_BYTES),
            mime=JPEG_MIME,
            bucket_key=f"blobs/{JPEG_SHA[0:2]}/{JPEG_SHA[2:4]}/{JPEG_SHA}",
        )
        body = self.presign().json()

        self.assertTrue(body["already_present"])
        self.assertIsNone(body["upload_url"])
        self.assertIsNone(body["expires_in"])
        self.assertIsNone(body["required_headers"])

    def test_dedup_does_not_touch_the_object_store(self) -> None:
        """"Deduplicación gratis" en sentido literal: ni una escritura."""
        Blob.objects.create(
            sha256=JPEG_SHA,
            size=len(JPEG_BYTES),
            mime=JPEG_MIME,
            bucket_key=f"blobs/{JPEG_SHA[0:2]}/{JPEG_SHA[2:4]}/{JPEG_SHA}",
        )
        self.presign()
        self.assertEqual(list(self.store.list_objects()), [])

    def test_a_mime_outside_the_allow_list_is_rejected_by_the_schema(self) -> None:
        response = self.presign(mime="image/svg+xml")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")

    def test_a_file_over_the_per_file_limit_is_rejected_with_413(self) -> None:
        response = self.presign(size=caps.ATTACHMENT_MAX_BYTES + 1)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["code"], "payload_too_large")

    def test_a_malformed_sha256_never_reaches_the_service(self) -> None:
        for value in ("../../etc/passwd", JPEG_SHA.upper(), "zz" * 32, JPEG_SHA[:63]):
            with self.subTest(sha256=value):
                response = self.presign(sha256=value)
                self.assertEqual(response.status_code, 422)

    def test_it_requires_authentication(self) -> None:
        response = self.scenario.client.post(
            PRESIGN_URL, data=presign_body(), content_type="application/json"
        )
        self.assertEqual(response.status_code, 401)


@skip_without_db
class PresignQuotaTests(_AttachmentsTestCase):
    """Cuota acumulada por visita (tarea 5), y permisos sobre la visita."""

    def _attach(self, visit, data: bytes) -> None:
        digest = digest_of(data)
        Blob.objects.create(
            sha256=digest,
            size=caps.ATTACHMENT_MAX_BYTES_PER_VISIT - 10,
            mime=JPEG_MIME,
            bucket_key=f"blobs/{digest[0:2]}/{digest[2:4]}/{digest}",
        )
        VisitAttachment.objects.create(
            id=uuid.uuid4(),
            org=self.scenario.org,
            visit=visit,
            blob_id=digest,
            role="foto",
            captured_at="2026-07-30T14:05:09.123Z",
            change_seq=101,
        )

    def test_within_quota_is_accepted(self) -> None:
        visit = make_visit(self.scenario)
        response = self.presign(visit_id=visit.pk)
        self.assertEqual(response.status_code, 200)

    def test_over_the_per_visit_quota_is_rejected_with_413(self) -> None:
        visit = make_visit(self.scenario)
        self._attach(visit, PNG_BYTES)

        response = self.presign(visit_id=visit.pk, size=1000)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["code"], "payload_too_large")

    def test_a_deleted_attachment_frees_its_quota(self) -> None:
        """Corregir una foto no debe pagar dos veces por el mismo hueco."""
        visit = make_visit(self.scenario)
        self._attach(visit, PNG_BYTES)
        VisitAttachment.objects.filter(visit=visit).update(deleted_at="2026-08-01T00:00:00Z")

        self.assertEqual(self.presign(visit_id=visit.pk, size=1000).status_code, 200)

    def test_a_visit_of_another_organization_is_a_404(self) -> None:
        """RD-11: indistinguible de una visita inexistente."""
        other = build_scenario(username=f"ajeno-{uuid.uuid4().hex[:8]}")
        foreign_visit = make_visit(other)

        response = self.presign(visit_id=foreign_visit.pk)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "not_found")

    def test_an_unknown_visit_is_a_404(self) -> None:
        self.assertEqual(self.presign(visit_id=uuid.uuid4()).status_code, 404)


@skip_without_db
class PresignRoleTests(_AttachmentsTestCase):
    role = Role.VIEWER

    def test_a_viewer_cannot_request_an_upload_url(self) -> None:
        """Un `VIEWER` no captura, así que no sube (política del Paso 17)."""
        response = self.presign()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "forbidden")


@skip_without_db
class CommitTests(_AttachmentsTestCase):
    def test_valid_commit_registers_the_blob(self) -> None:
        """**Caso (c) del plan**, primera mitad."""
        self.upload(JPEG_BYTES)
        response = self.commit(JPEG_SHA, len(JPEG_BYTES))

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["sha256"], JPEG_SHA)
        self.assertEqual(body["size"], len(JPEG_BYTES))
        self.assertEqual(body["mime"], JPEG_MIME)
        self.assertIn("created_at", body)

        blob = Blob.objects.get(pk=JPEG_SHA)
        self.assertEqual(blob.size, len(JPEG_BYTES))
        self.assertEqual(blob.bucket_key, f"blobs/{JPEG_SHA[0:2]}/{JPEG_SHA[2:4]}/{JPEG_SHA}")

    def test_double_commit_is_idempotent(self) -> None:
        """**Caso (c) del plan**, segunda mitad: mismo blob, sin conflicto."""
        self.upload(JPEG_BYTES)
        first = self.commit(JPEG_SHA, len(JPEG_BYTES))
        second = self.commit(JPEG_SHA, len(JPEG_BYTES))

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(Blob.objects.filter(pk=JPEG_SHA).count(), 1)

    def test_checksum_mismatch_is_rejected_and_registers_nothing(self) -> None:
        """**Caso (b) del plan**: el objeto no es lo que dice su clave.

        Se depositan bytes de PNG bajo la clave del JPEG. El `HEAD` del store
        certifica el hash de los bytes **reales**, no el de la clave, así que la
        discrepancia se detecta y **el `Blob` no se registra**.
        """
        self.upload(PNG_BYTES, mime=JPEG_MIME, sha256=JPEG_SHA)

        response = self.commit(JPEG_SHA, len(PNG_BYTES))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "attachment_checksum_mismatch")
        self.assertFalse(Blob.objects.filter(pk=JPEG_SHA).exists())

    def test_size_mismatch_is_rejected_and_registers_nothing(self) -> None:
        """La otra mitad del mismo código de error: el tamaño declarado miente."""
        self.upload(JPEG_BYTES)

        response = self.commit(JPEG_SHA, len(JPEG_BYTES) + 1)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "attachment_checksum_mismatch")
        self.assertFalse(Blob.objects.filter(pk=JPEG_SHA).exists())

    def test_content_type_mismatch_is_rejected(self) -> None:
        """Lo subido como PNG no puede confirmarse como JPEG."""
        self.upload(JPEG_BYTES, mime=PNG_MIME)

        response = self.commit(JPEG_SHA, len(JPEG_BYTES), mime=JPEG_MIME)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
        self.assertFalse(Blob.objects.filter(pk=JPEG_SHA).exists())

    def test_disguised_content_is_rejected_by_the_magic_bytes(self) -> None:
        """El caso que justifica el sniffing (tarea 5).

        Aquí **todo cuadra menos los bytes**: el hash es el de lo subido, el
        tamaño coincide y el `Content-Type` almacenado dice `image/jpeg`. Lo
        único que delata al ejecutable son sus primeros bytes.
        """
        digest = digest_of(DISGUISED_BYTES)
        self.upload(DISGUISED_BYTES, mime=JPEG_MIME)

        response = self.commit(digest, len(DISGUISED_BYTES))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
        self.assertFalse(Blob.objects.filter(pk=digest).exists())

    def test_committing_something_never_uploaded_is_a_404(self) -> None:
        response = self.commit(JPEG_SHA, len(JPEG_BYTES))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "not_found")
        self.assertFalse(Blob.objects.filter(pk=JPEG_SHA).exists())

    def test_a_malformed_sha256_in_the_path_is_rejected_at_the_edge(self) -> None:
        """Traversal por la ruta del commit: muere en el `pattern` del contrato."""
        for value in ("..%2f..%2fetc%2fpasswd", "aa/bb", JPEG_SHA.upper()):
            with self.subTest(sha256=value):
                response = self.client_post(
                    commit_url(value), {"size": 1, "mime": JPEG_MIME}
                )
                self.assertIn(response.status_code, (404, 422))
                self.assertEqual(Blob.objects.count(), 0)

    def test_it_requires_authentication(self) -> None:
        response = self.scenario.client.post(
            commit_url(JPEG_SHA),
            data={"size": 1, "mime": JPEG_MIME},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)


@skip_without_db
class DownloadUrlTests(_AttachmentsTestCase):
    """`GET /v1/attachments/{sha256}/url`: nunca sin comprobar permisos."""

    def _commit_and_attach(self, visit=None):
        self.upload(JPEG_BYTES)
        self.commit(JPEG_SHA, len(JPEG_BYTES))
        target = visit if visit is not None else make_visit(self.scenario)
        VisitAttachment.objects.create(
            id=uuid.uuid4(),
            org=self.scenario.org,
            visit=target,
            blob_id=JPEG_SHA,
            role="foto",
            captured_at="2026-07-30T14:05:09.123Z",
            change_seq=102,
        )
        return target

    def get(self, sha256: str):
        return self.scenario.client.get(download_url(sha256), **self.scenario.auth())

    def test_a_visible_attachment_yields_a_short_lived_url(self) -> None:
        self._commit_and_attach()
        response = self.get(JPEG_SHA)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["url"])
        self.assertEqual(body["expires_in"], 300)
        self.assertEqual(body["mime"], JPEG_MIME)
        self.assertEqual(body["size"], len(JPEG_BYTES))

    def test_the_download_name_is_generated_by_the_server(self) -> None:
        """RD-06: el nombre sugerido sale de un UUID, no de ninguna entrada."""
        self._commit_and_attach()
        first = self.get(JPEG_SHA).json()["url"]
        second = self.get(JPEG_SHA).json()["url"]
        self.assertIn(".jpg", first)
        self.assertNotEqual(first, second)

    def test_a_blob_with_no_attachment_in_my_org_is_a_404(self) -> None:
        """El contrato, literal: `404` por inexistencia, no `403`."""
        self.upload(JPEG_BYTES)
        self.commit(JPEG_SHA, len(JPEG_BYTES))  # el blob existe...
        # ...pero no hay ningún `visit_attachment` de esta organización.
        response = self.get(JPEG_SHA)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "not_found")

    def test_an_attachment_of_another_organization_is_a_404(self) -> None:
        """El aislamiento que `Blob` delega en `visit_attachment` (RD-11)."""
        other = build_scenario(username=f"ajeno-{uuid.uuid4().hex[:8]}")
        self.upload(JPEG_BYTES)
        self.commit(JPEG_SHA, len(JPEG_BYTES))
        VisitAttachment.objects.create(
            id=uuid.uuid4(),
            org=other.org,
            visit=make_visit(other),
            blob_id=JPEG_SHA,
            role="foto",
            captured_at="2026-07-30T14:05:09.123Z",
            change_seq=103,
        )

        response = self.get(JPEG_SHA)
        self.assertEqual(response.status_code, 404)

    def test_a_deleted_attachment_no_longer_grants_access(self) -> None:
        self._commit_and_attach()
        VisitAttachment.objects.filter(blob_id=JPEG_SHA).update(
            deleted_at="2026-08-01T00:00:00Z"
        )
        self.assertEqual(self.get(JPEG_SHA).status_code, 404)

    def test_a_pending_blob_is_a_404_not_a_broken_url(self) -> None:
        """El adjunto llegó por el push antes que sus bytes: aún no hay descarga."""
        VisitAttachment.objects.create(
            id=uuid.uuid4(),
            org=self.scenario.org,
            visit=make_visit(self.scenario),
            blob_id=JPEG_SHA,
            role="foto",
            captured_at="2026-07-30T14:05:09.123Z",
            change_seq=104,
        )
        self.assertEqual(self.get(JPEG_SHA).status_code, 404)

    def test_a_malformed_sha256_is_rejected_at_the_edge(self) -> None:
        for value in ("..%2f..%2fetc%2fpasswd", JPEG_SHA.upper(), "zz" * 32):
            with self.subTest(sha256=value):
                self.assertIn(self.get(value).status_code, (404, 422))

    def test_it_requires_authentication(self) -> None:
        self.assertEqual(self.scenario.client.get(download_url(JPEG_SHA)).status_code, 401)


@skip_without_db
class ViewerDownloadTests(_AttachmentsTestCase):
    role = Role.VIEWER

    def test_a_viewer_can_download_what_it_can_read(self) -> None:
        """Leer es leer: `VIEWER` existe para consultar (política `can_read_visit`)."""
        self.store.put_object(JPEG_SHA, JPEG_BYTES, JPEG_MIME)
        Blob.objects.create(
            sha256=JPEG_SHA,
            size=len(JPEG_BYTES),
            mime=JPEG_MIME,
            bucket_key=f"blobs/{JPEG_SHA[0:2]}/{JPEG_SHA[2:4]}/{JPEG_SHA}",
        )
        VisitAttachment.objects.create(
            id=uuid.uuid4(),
            org=self.scenario.org,
            visit=make_visit(self.scenario),
            blob_id=JPEG_SHA,
            role="foto",
            captured_at="2026-07-30T14:05:09.123Z",
            change_seq=105,
        )
        self.assertEqual(
            self.scenario.client.get(download_url(JPEG_SHA), **self.scenario.auth()).status_code,
            200,
        )
