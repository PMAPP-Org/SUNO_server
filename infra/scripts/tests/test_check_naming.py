"""Pruebas de check_naming.py: el nombre viejo no vuelve al repositorio."""

import importlib.util
import pathlib
import unittest

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

    def test_flags_the_old_concept_in_code_and_text(self):
        found = check_naming.offenders(
            "class Visit(Model):\n    visit_id = 1  # la visita\n", "concept"
        )
        self.assertEqual(found, ["Visit", "visit_id", "visita"])

    def test_allows_words_that_only_contain_visit(self):
        text = "visited nodes; revisitar; visitados; visitante\n"
        self.assertEqual(check_naming.offenders(text, "concept"), [])

    def test_allows_citing_historical_plans_by_their_name(self):
        text = "ver plans/visitacampo-construccion.md\n"
        self.assertEqual(check_naming.offenders(text, "package"), [])

    def test_flags_citing_the_renamed_progress_log_by_its_old_name(self):
        text = "ver plans/visitacampo-progreso.md\n"
        self.assertEqual(check_naming.offenders(text, "package"), ["visitacampo"])

    def test_package_mode_ignores_the_concept(self):
        self.assertEqual(check_naming.offenders("class Visit: pass\n", "package"), [])

    def test_skips_history(self):
        self.assertTrue(check_naming.skipped("plans/visitacampo-construccion.md"))
        self.assertTrue(check_naming.skipped("CHANGELOG.md"))
        self.assertTrue(check_naming.skipped(".gitleaksignore"))  # huellas con rutas viejas
        self.assertTrue(check_naming.skipped("plans/suno-progreso.md"))  # historia de avance
        self.assertTrue(check_naming.skipped("infra/scripts/check_naming.py"))  # se nombra a sí misma
        self.assertFalse(check_naming.skipped("server/suno/core/models/record.py"))


if __name__ == "__main__":
    unittest.main()
