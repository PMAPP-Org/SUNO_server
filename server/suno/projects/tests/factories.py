"""Datos de prueba del avance de campo: un proyecto con el cronograma `v1`
importado, registros vinculados a tareas y sus revisiones.

Los tests sí pueden usar `Model.objects` (el test de arquitectura excluye
`tests/`).
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from suno.authn.tests.factories import make_org, make_user
from suno.core.context import Principal, RequestContext, resolve_request_context
from suno.core.models import (
    AppUser,
    Device,
    FormTemplate,
    FormTemplateVersion,
    Organization,
    Project,
    Role,
    Task,
    TaskResponsible,
    Record,
    RecordReview,
    RecordReviewStatus,
)
from suno.projects.importer import apply_import, stage_import

from .samples import v1

_seq = itertools.count(1000)

#: Instante de referencia: los registros se ordenan por `captured_at`.
T0 = datetime(2026, 3, 10, 9, tzinfo=UTC)


def make_record(
    org: Organization,
    author: AppUser,
    *,
    task: Task | None = None,
    pct: str | None = None,
    at: datetime | None = None,
    deleted: bool = False,
    quantities: list[dict[str, object]] | None = None,
) -> Record:
    """Un registro mínima, con o sin tarea. `pct` como texto (`"35.5"`)."""
    template, _ = FormTemplate.objects.get_or_create(org=org, name="Avance de obra")
    version, _ = FormTemplateVersion.objects.get_or_create(
        org=org,
        template=template,
        version_number=1,
        defaults={"definition": {"fields": []}, "change_seq": next(_seq)},
    )
    device, _ = Device.objects.get_or_create(
        org=org,
        owner=author,
        defaults={"id": uuid.uuid4(), "platform": "android", "app_version": "0.11.0"},
    )
    moment = at or timezone.now()
    return Record.objects.create(
        id=uuid.uuid4(),
        org=org,
        change_seq=next(_seq),
        template_version=version,
        answers={},
        captured_at=moment,
        device=device,
        created_by=author,
        task=task,
        progress_pct=None if pct is None else Decimal(pct),
        quantities=quantities or [],
        deleted_at=moment if deleted else None,
    )


@dataclass
class FieldWorld:
    org: Organization
    admin: AppUser
    supervisor: AppUser
    agent: AppUser
    project: Project

    def ctx(self, user: AppUser | None = None) -> RequestContext:
        who = user or self.admin
        return resolve_request_context(Principal(user_id=who.pk, org_id=who.org_id))

    def task(self, uid: int) -> Task:
        return Task.objects.get(project=self.project, uid=uid)

    def responsible(self, uid: int, user: AppUser | None = None) -> TaskResponsible:
        return TaskResponsible.objects.create(
            org=self.org, task=self.task(uid), user=user or self.agent, assigned_by=self.admin
        )

    def record(
        self,
        uid: int,
        pct: str,
        *,
        hours: int = 0,
        author: AppUser | None = None,
        deleted: bool = False,
    ) -> Record:
        return make_record(
            self.org,
            author or self.agent,
            task=self.task(uid),
            pct=pct,
            at=T0 + timedelta(hours=hours),
            deleted=deleted,
        )

    def review(self, record: Record, status: str) -> RecordReview:
        return RecordReview.objects.create(
            org=self.org, record=record, status=status, reviewer=self.supervisor
        )

    def approve(self, record: Record) -> RecordReview:
        return self.review(record, str(RecordReviewStatus.APPROVED))

    def reject(self, record: Record) -> RecordReview:
        return self.review(record, str(RecordReviewStatus.REJECTED))


def make_field_world(name: str = "Constructora") -> FieldWorld:
    """Organización con administración, supervisión y un agente, y el cronograma
    `samples.v1()` aplicado: 1 Preliminares (resumen) > 1.1 Localización (uid 2),
    1.2 Descapote (uid 3); 2 Estructura (resumen, uid 4) > 2.1 Columnas (uid 5),
    2.2 Fin de obra (hito, uid 6)."""
    org = make_org(name)
    slug = name.lower().replace(" ", "-")
    admin = make_user(org, username=f"admin-{slug}", role=Role.ADMIN)
    supervisor = make_user(org, username=f"super-{slug}", role=Role.SUPERVISOR)
    agent = make_user(org, username=f"agente-{slug}", role=Role.FIELD_AGENT)
    project = Project.objects.create(org=org, name=f"Obra {name}", created_by=admin)
    ctx = resolve_request_context(Principal(user_id=admin.pk, org_id=org.pk))
    apply_import(ctx, stage_import(ctx, project, file_name="v1.xml", data=v1()).pk)
    project.refresh_from_db()
    return FieldWorld(org=org, admin=admin, supervisor=supervisor, agent=agent, project=project)
