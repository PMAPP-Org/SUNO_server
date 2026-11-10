"""`bootstrap_demo` (revisión final de despliegue del demo, hallazgo Crítico).

Verifica lo que un operador de un VPS recién desplegado necesita que sea
cierto: el comando crea una `Organization`, un `AppUser` con `Membership`
`FIELD_AGENT`, y una `FormTemplateVersion` publicada cuya `definition` acepta
`altura_cm`/`notas` -- los mismos dos campos que
la pantalla de captura de PMAPP-Org/SUNO_client envía en `answers`. También
que el comando es seguro de correr dos veces (falla en vez de duplicar) y que
nunca hardcodea una contraseña real cuando no se le pasa una.
"""

from __future__ import annotations

import io
import unittest

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command

from suno.core.context import Principal, resolve_request_context
from suno.core.models import (
    FormTemplateVersion,
    Membership,
    Organization,
    Project,
    Role,
    Task,
    TaskResponsible,
)
from suno.forms.definition import validate_definition
from suno.projects.field_tasks import field_tasks, project_items

from .dbsupport import SKIP_REASON, setup_test_database, teardown_test_database

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class BootstrapDemoTests(unittest.TestCase):
    def test_creates_org_user_membership_and_published_template(self) -> None:
        out = io.StringIO()
        call_command(
            "bootstrap_demo",
            "--org-name=Org bootstrap test",
            "--username=agente-bootstrap-1",
            "--password=una-contraseña-de-prueba-larga-42",
            stdout=out,
        )

        org = Organization.objects.get(name="Org bootstrap test")

        app_user_model = get_user_model()
        user = app_user_model.objects.get(username="agente-bootstrap-1")
        self.assertEqual(user.org_id, org.id)
        self.assertTrue(user.check_password("una-contraseña-de-prueba-larga-42"))

        membership = Membership.objects.get(org=org, user=user)
        self.assertEqual(membership.role, Role.FIELD_AGENT)

        template_version = FormTemplateVersion.objects.get(org=org)
        self.assertIsNotNone(template_version.published_at)
        self.assertGreater(template_version.change_seq, 0)

        # La definición publicada es válida contra el meta-schema real (el
        # mismo que `FormTemplateVersion.clean()`/Django Admin exigirían) y
        # declara los dos campos que `capture_screen.dart` envía.
        parsed = validate_definition(template_version.definition)
        field_keys = {field.key for field in parsed.fields}
        self.assertEqual(field_keys, {"altura_cm", "notas"})

        output = out.getvalue()
        self.assertIn(str(org.id), output)
        self.assertIn("agente-bootstrap-1", output)
        self.assertIn(str(template_version.id), output)
        # La contraseña se pasó explícitamente por --password: no se genera
        # ni se imprime como "generada".
        self.assertNotIn("generada", output)

    def test_extra_agents_for_the_load_test(self) -> None:
        """Paso 25: N agentes más en la misma organización, misma contraseña."""
        call_command(
            "bootstrap_demo",
            "--org-name=Org carga test",
            "--username=carga",
            "--password=una-contraseña-de-prueba-larga-42",
            "--extra-agents=3",
            stdout=io.StringIO(),
        )
        org = Organization.objects.get(name="Org carga test")
        agents = get_user_model().objects.filter(org=org).order_by("username")
        self.assertEqual(
            [a.username for a in agents], ["carga", "carga-01", "carga-02", "carga-03"]
        )
        self.assertTrue(all(a.check_password("una-contraseña-de-prueba-larga-42") for a in agents))
        self.assertEqual(Membership.objects.filter(org=org, role=Role.FIELD_AGENT).count(), 4)

    def test_with_schedule_seeds_a_task_the_agent_can_report(self) -> None:
        out = io.StringIO()
        call_command(
            "bootstrap_demo",
            "--org-name=Org cronograma test",
            "--username=agente-cronograma",
            "--password=una-contraseña-de-prueba-larga-42",
            "--with-schedule",
            stdout=out,
        )
        org = Organization.objects.get(name="Org cronograma test")
        user = get_user_model().objects.get(username="agente-cronograma")
        project = Project.objects.get(org=org)
        leaf = Task.objects.get(project=project, uid=2)
        self.assertEqual((leaf.wbs, leaf.name, leaf.is_summary), ("1.1", "Excavación", False))
        assert leaf.parent is not None
        self.assertEqual(leaf.parent.uid, 1)
        self.assertTrue(
            TaskResponsible.objects.filter(task__uid=1, task__project=project, user=user).exists()
        )
        self.assertIn(str(leaf.id), out.getvalue())

    def test_with_quantities_the_task_is_measured_by_quantities(self) -> None:
        call_command(
            "bootstrap_demo",
            "--org-name=Org cantidades test",
            "--username=agente-cantidades",
            "--password=una-contraseña-de-prueba-larga-42",
            "--with-schedule",
            "--with-quantities",
            stdout=io.StringIO(),
        )
        org = Organization.objects.get(name="Org cantidades test")
        user = get_user_model().objects.get(username="agente-cantidades")
        ctx = resolve_request_context(Principal(user_id=user.pk, org_id=org.pk))
        task = next(t for t in field_tasks(ctx) if t["wbs"] == "1.1")
        self.assertEqual(task["measured_by"], "quantities")
        self.assertEqual(
            [(i["code"], i["unit"], i["planned_quantity"]) for i in task["planned_items"]],
            [("1.1", "m3", 100.0)],
        )
        project = Project.objects.get(org=org)
        catalog = project_items(ctx, [project.pk])
        self.assertEqual([i["code"] for i in catalog[0]["items"]], ["1.1", "1.2"])

    def test_quantities_need_the_schedule(self) -> None:
        with self.assertRaises(CommandError):
            call_command(
                "bootstrap_demo",
                "--org-name=Org sin cronograma cantidades",
                "--username=agente-sin-cronograma-cantidades",
                "--password=una-contraseña-de-prueba-larga-42",
                "--with-quantities",
                stdout=io.StringIO(),
            )

    def test_without_the_flag_there_is_no_project(self) -> None:
        call_command(
            "bootstrap_demo",
            "--org-name=Org sin cronograma",
            "--username=agente-sin-cronograma",
            "--password=una-contraseña-de-prueba-larga-42",
            stdout=io.StringIO(),
        )
        self.assertFalse(Project.objects.filter(org__name="Org sin cronograma").exists())

    def test_generates_and_prints_a_password_when_none_is_given(self) -> None:
        out = io.StringIO()
        call_command(
            "bootstrap_demo",
            "--org-name=Org bootstrap test 2",
            "--username=agente-bootstrap-2",
            stdout=out,
        )

        app_user_model = get_user_model()
        user = app_user_model.objects.get(username="agente-bootstrap-2")

        output = out.getvalue()
        self.assertIn("generada", output)
        # La contraseña impresa es la misma que quedó hasheada en el usuario:
        # se extrae la última palabra de la línea "generada" y se verifica
        # contra el hash real, en vez de repetir a mano el formato exacto de
        # la línea (frágil si cambia la redacción del mensaje).
        generated_line = next(line for line in output.splitlines() if "generada" in line)
        generated_password = generated_line.strip().split()[-1]
        self.assertTrue(user.check_password(generated_password))

    def test_running_twice_with_the_same_org_name_fails_instead_of_duplicating(self) -> None:
        call_command(
            "bootstrap_demo",
            "--org-name=Org bootstrap test 3",
            "--username=agente-bootstrap-3",
            "--password=una-contraseña-de-prueba-larga-42",
            stdout=io.StringIO(),
        )

        with self.assertRaises(CommandError):
            call_command(
                "bootstrap_demo",
                "--org-name=Org bootstrap test 3",
                "--username=agente-bootstrap-3-b",
                "--password=otra-contraseña-de-prueba-larga-42",
                stdout=io.StringIO(),
            )

        # Ni la segunda organización ni un segundo usuario se crearon.
        self.assertEqual(Organization.objects.filter(name="Org bootstrap test 3").count(), 1)
        app_user_model = get_user_model()
        self.assertFalse(app_user_model.objects.filter(username="agente-bootstrap-3-b").exists())

    def test_running_twice_with_the_same_username_fails_instead_of_duplicating(self) -> None:
        call_command(
            "bootstrap_demo",
            "--org-name=Org bootstrap test 4",
            "--username=agente-bootstrap-4",
            "--password=una-contraseña-de-prueba-larga-42",
            stdout=io.StringIO(),
        )

        with self.assertRaises(CommandError):
            call_command(
                "bootstrap_demo",
                "--org-name=Org bootstrap test 4 bis",
                "--username=agente-bootstrap-4",
                "--password=otra-contraseña-de-prueba-larga-42",
                stdout=io.StringIO(),
            )

        self.assertFalse(Organization.objects.filter(name="Org bootstrap test 4 bis").exists())


if __name__ == "__main__":
    unittest.main()
