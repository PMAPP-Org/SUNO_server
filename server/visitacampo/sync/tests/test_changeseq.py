"""El lock por organización y el reparto de `change_seq` (doc 08 §4.4).

Lo que se puede comprobar sin Postgres —y es la parte que más fácilmente se
rompe sin que nadie se entere— se comprueba aquí:

- que la clave del lock sea **determinista entre procesos** (si no lo fuera,
  dos workers de gunicorn tomarían locks distintos para la misma organización
  y la serialización desaparecería sin ningún síntoma);
- que tomar el lock **fuera de una transacción** falle ruidosamente, porque un
  `pg_advisory_xact_lock` en autocommit se suelta en la misma sentencia y deja
  el código con la apariencia de estar serializado;
- que `SYNCED_MODELS` siga cubriendo **todas** las entidades que comparten el
  cursor de pull: si una nueva se quedara fuera, el push repetiría un
  `change_seq` ya usado y una página del pull se saltaría una fila.

El `MAX(change_seq) + 1` contra la base de datos se prueba en `test_push.py`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

from django.db import models
from django.test import SimpleTestCase

from visitacampo.core.models import SyncedModel
from visitacampo.sync.changeseq import (
    SYNC_LOCK_NAMESPACE,
    SYNCED_MODELS,
    ChangeSeqAllocator,
    acquire_org_lock,
    org_lock_key,
)

#: `server/` — el raíz del proyecto Django (este archivo está en
#: server/visitacampo/sync/tests/).
SERVER_ROOT = Path(__file__).resolve().parents[3]

ORG = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_ORG = uuid.UUID("99999999-9999-9999-9999-999999999999")

INT32_MIN, INT32_MAX = -(2**31), 2**31 - 1


class LockKeyTests(SimpleTestCase):
    def test_both_keys_fit_in_a_postgres_int4(self) -> None:
        """`pg_advisory_xact_lock(int, int)` no acepta nada más ancho."""
        for key in (SYNC_LOCK_NAMESPACE, org_lock_key(ORG), org_lock_key(OTHER_ORG)):
            with self.subTest(key=key):
                self.assertGreaterEqual(key, INT32_MIN)
                self.assertLessEqual(key, INT32_MAX)

    def test_the_key_is_a_pure_function_of_the_organization(self) -> None:
        self.assertEqual(org_lock_key(ORG), org_lock_key(ORG))
        self.assertNotEqual(org_lock_key(ORG), org_lock_key(OTHER_ORG))

    def test_the_key_is_stable_across_processes(self) -> None:
        """El fallo que este test existe para impedir: usar `hash()`.

        `hash()` de un `str`/`bytes` está salado con `PYTHONHASHSEED`, distinto
        en cada proceso. Dos workers tomarían locks distintos para la misma
        organización y no serializarían nada. Se comprueba lanzando un
        intérprete **nuevo** —que por tanto tiene otra sal— y comparando.
        """
        program = (
            "import django;django.setup();"
            "import uuid;"
            "from visitacampo.sync.changeseq import org_lock_key;"
            f"print(org_lock_key(uuid.UUID('{ORG}')))"
        )
        env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.dev"}
        completed = subprocess.run(
            [sys.executable, "-c", program],
            cwd=SERVER_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(int(completed.stdout.strip()), org_lock_key(ORG))


class LockTransactionGuardTests(SimpleTestCase):
    """Tomar el lock fuera de una transacción es un error, no un no-op."""

    def test_refuses_to_run_in_autocommit(self) -> None:
        with self.assertRaises(RuntimeError) as caught:
            acquire_org_lock(ORG)
        self.assertIn("transaction.atomic", str(caught.exception))


class AllocatorTests(SimpleTestCase):
    def test_hands_out_consecutive_values(self) -> None:
        allocator = ChangeSeqAllocator(10)
        self.assertEqual([allocator.take() for _ in range(3)], [10, 11, 12])

    def test_never_repeats_a_value_within_a_batch(self) -> None:
        """Un `change_seq` repetido haría que el pull se saltase una fila."""
        allocator = ChangeSeqAllocator(1)
        values = [allocator.take() for _ in range(500)]
        self.assertEqual(len(set(values)), 500)
        self.assertEqual(values, sorted(values))

    def test_next_value_does_not_consume(self) -> None:
        allocator = ChangeSeqAllocator(7)
        self.assertEqual(allocator.next_value, 7)
        self.assertEqual(allocator.next_value, 7)
        self.assertEqual(allocator.take(), 7)


class SyncedModelCoverageTests(SimpleTestCase):
    """`SYNCED_MODELS` debe ser exactamente el conjunto real, no un subconjunto."""

    def test_lists_every_concrete_synced_model(self) -> None:
        concrete = {
            model
            for model in models.Model.__subclasses__() + list(SyncedModel.__subclasses__())
            if issubclass(model, SyncedModel) and not model._meta.abstract
        }
        self.assertEqual(set(SYNCED_MODELS), concrete)

    def test_every_listed_model_has_the_cursor_column(self) -> None:
        for model in SYNCED_MODELS:
            with self.subTest(model=model.__name__):
                self.assertTrue(model._meta.get_field("change_seq"))
