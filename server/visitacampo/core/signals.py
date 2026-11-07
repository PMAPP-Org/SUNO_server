"""Aprovisionamiento compatible de accesos para nuevas membresías."""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from visitacampo.core.access import AccessScope, ProjectScopeSource, Section
from visitacampo.core.access_services import provision_legacy_section_grants
from visitacampo.core.context import system_context
from visitacampo.core.models import (
    Membership,
    SectionGrant,
    SectionProjectScope,
    TaskResponsible,
)
from visitacampo.core.scoping import scoped_qs


@receiver(post_save, sender=Membership, dispatch_uid="core.provision_section_grants")
def provision_section_grants(
    sender: type[Membership], instance: Membership, created: bool, **kwargs: object
) -> None:
    if created:
        provision_legacy_section_grants(instance)


@receiver(post_save, sender=TaskResponsible, dispatch_uid="core.add_responsible_project_scope")
def add_responsible_project_scope(
    sender: type[TaskResponsible], instance: TaskResponsible, created: bool, **kwargs: object
) -> None:
    if not created:
        return
    ctx = system_context(instance.org_id)
    grant = scoped_qs(ctx, SectionGrant).filter(
        org_id=instance.org_id,
        user_id=instance.user_id,
        section=Section.PROJECTS,
        scope=AccessScope.PROJECTS,
        active=True,
    ).first()
    if grant is not None:
        scoped_qs(ctx, SectionProjectScope).get_or_create(
            org_id=instance.org_id,
            grant=grant,
            project_id=instance.task.project_id,
            defaults={
                "granted_by_id": instance.assigned_by_id,
                "source": ProjectScopeSource.TASK_ASSIGNMENT,
            },
        )


@receiver(post_delete, sender=TaskResponsible, dispatch_uid="core.remove_responsible_project_scope")
def remove_responsible_project_scope(
    sender: type[TaskResponsible], instance: TaskResponsible, **kwargs: object
) -> None:
    project_id = instance.task.project_id
    ctx = system_context(instance.org_id)
    still_responsible = scoped_qs(ctx, TaskResponsible).filter(
        org_id=instance.org_id,
        user_id=instance.user_id,
        task__project_id=project_id,
        task__retired_in__isnull=True,
    ).exists()
    if not still_responsible:
        scoped_qs(ctx, SectionProjectScope).filter(
            org_id=instance.org_id,
            grant__user_id=instance.user_id,
            grant__section=Section.PROJECTS,
            project_id=project_id,
            source=ProjectScopeSource.TASK_ASSIGNMENT,
        ).delete()
