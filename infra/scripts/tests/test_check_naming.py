"""Pruebas de check_naming.py: el nombre viejo no vuelve al repositorio."""

import contextlib
import importlib.util
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "check_naming", pathlib.Path(__file__).resolve().parents[1] / "check_naming.py"
)
check_naming = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_naming)


class OffendersTests(unittest.TestCase):
    def test_flags_the_old_system_name(self):
        self.assertEqual(
            check_naming.offenders("import visitacampo.core\n", "package"), ["visitacampo"]
        )
        self.assertEqual(
            check_naming.offenders("VISITACAMPO_API_IMAGE=x\n", "package"), ["VISITACAMPO"]
        )

    def test_concept_mode_also_flags_the_old_system_name(self):
        self.assertEqual(
            check_naming.offenders("VISITACAMPO_API_IMAGE=x\n", "concept"), ["VISITACAMPO"]
        )

    def test_allows_only_an_explicit_legacy_deploy_literal(self):
        text = "VISITACAMPO_API_IMAGE=x\nimport visitacampo.core\n"
        self.assertEqual(
            check_naming.offenders(
                text,
                "package",
                allowed_literals=("VISITACAMPO_API_IMAGE",),
            ),
            ["visitacampo"],
        )

    def test_flags_the_old_concept_in_code_and_text(self):
        found = check_naming.offenders(
            "class Visit(Model):\n    visit_id = 1  # la visita\n", "concept"
        )
        self.assertEqual(found, ["Visit", "visit_id", "visita"])

    def test_allows_words_that_only_contain_visit(self):
        text = "visited nodes; revisitar; visitados; visitante\n"
        self.assertEqual(check_naming.offenders(text, "concept"), [])

    def test_allows_citing_historical_plans_that_exist(self):
        text = "ver plans/visitacampo-construccion.md\n"
        tracked = frozenset({"plans/visitacampo-construccion.md"})
        self.assertEqual(check_naming.offenders(text, "package", tracked), [])

    def test_flags_citing_a_plan_that_does_not_exist(self):
        text = "ver plans/visitacampo-deploy.md\n"
        tracked = frozenset({"plans/visitacampo-construccion.md"})
        self.assertEqual(check_naming.offenders(text, "package", tracked), ["visitacampo"])

    def test_flags_citing_the_renamed_progress_log_by_its_old_name(self):
        text = "ver plans/visitacampo-progreso.md\n"
        tracked = frozenset({"plans/suno-progreso.md"})
        self.assertEqual(check_naming.offenders(text, "package", tracked), ["visitacampo"])

    def test_package_mode_ignores_the_concept(self):
        self.assertEqual(check_naming.offenders("class Visit: pass\n", "package"), [])

    def test_skips_history(self):
        self.assertTrue(check_naming.skipped("plans/visitacampo-construccion.md"))
        self.assertTrue(check_naming.skipped("CHANGELOG.md"))
        self.assertTrue(check_naming.skipped(".gitleaksignore"))  # huellas con rutas viejas
        self.assertTrue(check_naming.skipped("plans/suno-progreso.md"))  # historia de avance
        # La guardia se nombra a sí misma.
        self.assertTrue(check_naming.skipped("infra/scripts/check_naming.py"))
        self.assertFalse(check_naming.skipped("server/suno/core/models/record.py"))


class MigrationHistoryTests(unittest.TestCase):
    def test_skips_applied_migrations_and_the_rename_by_exact_path(self):
        for path in (
            "server/suno/core/migrations/0001_initial.py",
            "server/suno/core/migrations/0002_add_visit_attachment_org_isolation_fk.py",
            "server/suno/core/migrations/0012_add_visit_task_progress.py",
            "server/suno/core/migrations/0014_add_quantity_matrix.py",
            "server/suno/core/migrations/0027_rename_visit_to_record.py",
        ):
            self.assertTrue(check_naming.skipped(path), path)

    def test_stays_strict_with_any_other_migration(self):
        for path in (
            "server/suno/core/migrations/0005_add_job_idempotency_key.py",
            "server/suno/core/migrations/0011_add_projects.py",
            "server/suno/core/migrations/0015_add_section_access.py",
            "server/suno/core/migrations/0028_add_visit_flag.py",
            "server/suno/core/migrations/0002_add_visit_other.py",
            "server/suno/otra/migrations/0001_initial.py",
        ):
            self.assertFalse(check_naming.skipped(path), path)


class ConceptCompatibilityTests(unittest.TestCase):
    def test_the_rename_runbook_may_cite_only_its_literals(self):
        allowed = check_naming._CONCEPT_COMPATIBILITY["docs/operacion/despliegue.md"]
        text = "core.0027_rename_visit_to_record; entity IN ('visit', 'visit_attachment')"
        self.assertEqual(check_naming.offenders(text, "concept", allowed_literals=allowed), [])
        self.assertEqual(
            check_naming.offenders("la visita", "concept", allowed_literals=allowed),
            ["visita"],
        )


class MainTests(unittest.TestCase):
    def test_runs_from_the_repository_root_when_invoked_from_a_subdirectory(self):
        # Sin GIT_DIR/GIT_WORK_TREE heredados: git debe descubrir el repo temporal.
        env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
        with tempfile.TemporaryDirectory() as repo, mock.patch.dict(os.environ, env, clear=True):
            root = pathlib.Path(repo)
            (root / "plans").mkdir()
            (root / "plans" / "visitacampo-construccion.md").write_text("historia\n")
            (root / "server").mkdir()
            (root / "server" / "app.py").write_text(
                "import visitacampo  # ver plans/visitacampo-construccion.md\n"
            )
            (root / "server" / "ok.py").write_text("# ver plans/visitacampo-construccion.md\n")
            subprocess.run(["git", "init", "-q", repo], check=True)
            subprocess.run(["git", "-C", repo, "add", "."], check=True)
            previous = os.getcwd()
            os.chdir(root / "server")
            try:
                with (
                    mock.patch.object(sys, "argv", ["check_naming.py", "package"]),
                    contextlib.redirect_stdout(io.StringIO()) as out,
                ):
                    status = check_naming.main()
            finally:
                os.chdir(previous)
        self.assertEqual(status, 1)
        self.assertIn("server/app.py: visitacampo", out.getvalue())
        self.assertNotIn("server/ok.py", out.getvalue())


if __name__ == "__main__":
    unittest.main()
