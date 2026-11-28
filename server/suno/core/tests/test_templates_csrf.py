"""Cada formulario POST de las plantillas lleva `{% csrf_token %}` donde Semgrep lo ve (#89).

La regla `python.django.security.django-no-csrf-token` (pack `p/django`, en
`.github/workflows/security.yml`) es de modo genérico: su `...` entre el token y `</form>`
abarca como mucho 10 líneas. Un token en la línea siguiente al `<form>` de un formulario
largo daba falsos positivos que se callaban con `nosemgrep`, y Code scanning los mostraba
igual. El token va a 10 líneas o menos del cierre (en los largos, justo antes de
`</form>`) y no quedan supresiones de esa regla.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
POST_FORM = re.compile(r"<form\b[^>]*\bmethod=[\"']?(post|put|patch|delete)\b", re.IGNORECASE)
TOKEN = "{% csrf_token %}"
#: Líneas que el `...` genérico de Semgrep salta como máximo (medido con semgrep 1.185).
MAX_SPAN = 10
RULE = "django-no-csrf-token"


def post_forms(text: str):
    """(línea del `<form>`, líneas del formulario) de cada formulario POST."""
    lines = text.splitlines()
    for start, line in enumerate(lines):
        found = POST_FORM.search(line)
        if found is None:
            continue
        if "</form>" in line[found.start() :]:
            end = start
        else:
            end = next(k for k in range(start + 1, len(lines)) if "</form>" in lines[k])
        yield start + 1, lines[start : end + 1]


class CsrfTokenPlacementTests(SimpleTestCase):
    def test_every_post_form_has_the_token_near_its_end(self) -> None:
        offenders = []
        for path in sorted(TEMPLATES.rglob("*.html")):
            for number, form in post_forms(path.read_text(encoding="utf-8")):
                tokens = [k for k, line in enumerate(form) if TOKEN in line]
                if not tokens or len(form) - 1 - tokens[-1] > MAX_SPAN:
                    offenders.append(f"{path.relative_to(TEMPLATES)}:{number}")
        self.assertEqual(offenders, [])

    def test_no_template_silences_the_rule(self) -> None:
        silenced = [
            str(path.relative_to(TEMPLATES))
            for path in sorted(TEMPLATES.rglob("*.html"))
            if RULE in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(silenced, [])
