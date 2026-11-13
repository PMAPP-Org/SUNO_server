"""Soportes subidos desde la web (costos, pieza 6a): `put_blob` y `store_upload`."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from suno.attachments.blobstore import bucket_key_for, checksum_header_value
from suno.attachments.localfs import LocalFsBlobStore
from suno.attachments.s3 import S3BlobStore, S3Config
from suno.attachments.storage import get_blob_store
from suno.attachments.web import MAX_BYTES, UploadRejected, sniff_mime, store_upload
from suno.core.models import Blob
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import PDF, PNG, pdf_upload

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

PDF_SHA = hashlib.sha256(PDF).hexdigest()


class PutBlobTests(SimpleTestCase):
    def test_localfs_keeps_bytes_and_content_type(self) -> None:
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as work:
            source = Path(work) / "soporte.pdf"
            source.write_bytes(PDF)
            store = LocalFsBlobStore(root)
            store.put_blob(sha256=PDF_SHA, path=str(source), size=len(PDF), mime="application/pdf")
            head = store.head(PDF_SHA)
        self.assertEqual(
            (head.size, head.sha256, head.content_type), (len(PDF), PDF_SHA, "application/pdf")
        )

    def test_s3_puts_with_checksum_and_type(self) -> None:
        client = mock.MagicMock()
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "soporte.pdf"
            source.write_bytes(PDF)
            S3BlobStore(S3Config(bucket="suno"), client=client).put_blob(
                sha256=PDF_SHA, path=str(source), size=len(PDF), mime="application/pdf"
            )
        kwargs = client.put_object.call_args.kwargs
        self.assertEqual(kwargs["Key"], bucket_key_for(PDF_SHA))
        self.assertEqual(kwargs["ContentType"], "application/pdf")
        self.assertEqual(kwargs["ChecksumSHA256"], checksum_header_value(PDF_SHA))

    def test_sniff_mime(self) -> None:
        self.assertEqual(sniff_mime(PDF[:16]), "application/pdf")
        self.assertEqual(sniff_mime(PNG[:16]), "image/png")
        self.assertIsNone(sniff_mime(b"MZ\x90\x00"))


class _Huge:
    """Un archivo que dice pesar más del límite (no se llega a leer)."""

    size: int | None = MAX_BYTES + 1

    def chunks(self, chunk_size: int | None = None) -> Iterator[bytes]:
        yield b""


@skip_without_db
class StoreUploadTests(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        overrides = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        overrides.enable()
        self.addCleanup(overrides.disable)

    def test_stores_registers_and_deduplicates(self) -> None:
        blob = store_upload(pdf_upload())
        self.assertEqual(
            (blob.sha256, blob.mime, blob.size), (PDF_SHA, "application/pdf", len(PDF))
        )
        self.assertEqual(get_blob_store().head(PDF_SHA).sha256, PDF_SHA)
        self.assertEqual(store_upload(pdf_upload("copia.pdf")).pk, blob.pk)
        self.assertEqual(Blob.objects.filter(pk=PDF_SHA).count(), 1)

    def test_rejects_other_types_empty_and_huge_files(self) -> None:
        for upload in (
            pdf_upload("virus.exe", b"MZ\x90\x00" + b"\x00" * 40),
            pdf_upload("vacio.pdf", b""),
            _Huge(),
        ):
            with self.subTest(upload=upload), self.assertRaises(UploadRejected):
                store_upload(upload)
        self.assertFalse(Blob.objects.exists())
