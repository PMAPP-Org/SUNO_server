"""Qué proyectos ve cada usuario y quién responde por cada tarea."""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from django.db.models import QuerySet
from django.http import Http404

from visitacampo.core.context import RequestContext
from visitacampo.core.models import AppUser, Project, Task, TaskResponsible
from visitacampo.core.policies import PROJECT_READ_ALL_ROLES
from visitacampo.core.scoping import scoped_qs


def visible_projects(ctx: RequestContext) -> QuerySet[Project]:
    projects = scoped_qs(ctx, Project)
    if ctx.has_any_role(PROJECT_READ_ALL_ROLES):
        return projects
    return projects.filter(
        tasks__responsibles__user_id=ctx.user_id, tasks__retired_in__isnull=True
    ).distinct()


def get_visible_project(ctx: RequestContext, project_id: UUID) -> Project:
    """El proyecto, o 404 si es de otra organización o el usuario no lo ve (RD-11)."""
    try:
        return visible_projects(ctx).get(pk=project_id)
    except Project.DoesNotExist as exc:
        raise Http404 from exc


def effective_responsibles(ctx: RequestContext, project: Project) -> dict[UUID, list[AppUser]]:
    """Responsables de cada tarea: los suyos más los heredados de sus ancestros."""
    parents = dict(scoped_qs(ctx, Task).filter(project=project).values_list("id", "parent_id"))
    direct: dict[UUID, list[AppUser]] = defaultdict(list)
    for link in (
        scoped_qs(ctx, TaskResponsible)
        .filter(task__project=project)
        .select_related("user")
        .order_by("user__username")
    ):
        direct[link.task_id].append(link.user)

    memo: dict[UUID, list[AppUser]] = {}

    def resolve(task_id: UUID) -> list[AppUser]:
        if task_id not in memo:
            parent = parents.get(task_id)
            inherited = resolve(parent) if parent is not None else []
            seen = {u.pk for u in direct.get(task_id, [])}
            memo[task_id] = direct.get(task_id, []) + [u for u in inherited if u.pk not in seen]
        return memo[task_id]

    return {task_id: resolve(task_id) for task_id in parents}
