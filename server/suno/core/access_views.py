"""Matriz web de usuarios, secciones, roles y capacidades efectivas."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from suno.core.access import (
    CAPABILITY_MATRIX,
    AccessScope,
    Capability,
    Section,
    capabilities_for,
)
from suno.core.access_services import revoke_section_grant, set_section_grant
from suno.core.context import Principal, RequestContext, resolve_request_context
from suno.core.models import AppUser, Membership, Project, SectionGrant
from suno.core.navigation import platform_navigation
from suno.core.scoping import get_organization, scoped_qs


def _ctx(request: HttpRequest) -> RequestContext:
    user = cast(AppUser, request.user)
    ctx = resolve_request_context(Principal(user_id=user.pk, org_id=user.org_id))
    if not ctx.has_capability(Section.USERS_ACCESS, Capability.MANAGE_ACCESS):
        raise PermissionDenied
    return ctx


def _uuid(value: str | None) -> UUID | None:
    try:
        return UUID(value) if value else None
    except ValueError:
        return None


def _error_text(exc: ValidationError) -> str:
    return " ".join(exc.messages)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def access_matrix(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    selected_id = _uuid(request.POST.get("user_id") or request.GET.get("usuario"))

    if request.method == "POST":
        if selected_id is None:
            raise Http404
        if not scoped_qs(ctx, Membership).filter(user_id=selected_id).exists():
            # Un UUID de otro tenant se comporta como un recurso inexistente.
            raise Http404
        section = request.POST.get("section", "")
        try:
            if request.POST.get("action") == "revoke":
                revoke_section_grant(ctx, user_id=selected_id, section=section)
                messages.success(request, "Acceso revocado.")
            else:
                project_ids = [
                    value
                    for raw in request.POST.getlist("projects")
                    if (value := _uuid(raw)) is not None
                ]
                result = set_section_grant(
                    ctx,
                    user_id=selected_id,
                    section=section,
                    role=request.POST.get("role", ""),
                    scope=request.POST.get("scope", ""),
                    project_ids=project_ids,
                )
                messages.success(
                    request,
                    "Acceso asignado." if result.created else "Acceso actualizado.",
                )
        except ValidationError as exc:
            messages.error(request, _error_text(exc))
        return redirect(f"/plataforma/accesos/?usuario={selected_id}")

    memberships = list(scoped_qs(ctx, Membership).select_related("user").order_by("user__username"))
    member_ids = {membership.user_id for membership in memberships}
    if selected_id is None and memberships:
        selected_id = memberships[0].user_id
    if selected_id is not None and selected_id not in member_ids:
        raise Http404

    grants = list(
        scoped_qs(ctx, SectionGrant)
        .filter(active=True)
        .select_related("user")
        .prefetch_related("project_scopes__project")
        .order_by("user__username", "section")
    )
    grants_by_cell = {(grant.user_id, grant.section): grant for grant in grants}
    section_rows = [
        {
            "value": section,
            "label": Section(section).label,
            "roles": [{"value": role, "label": role.label} for role in CAPABILITY_MATRIX[section]],
        }
        for section in Section.values
    ]
    matrix = [
        {
            "membership": membership,
            "cells": [
                grants_by_cell.get((membership.user_id, section)) for section in Section.values
            ],
        }
        for membership in memberships
    ]
    selected_membership = next(
        (membership for membership in memberships if membership.user_id == selected_id), None
    )
    selected_grants = {
        section: grants_by_cell.get((selected_id, section)) for section in Section.values
    }
    capability_rows = []
    for section in Section.values:
        grant = selected_grants[section]
        capability_rows.append(
            {
                "section": Section(section).label,
                "grant": grant,
                "capabilities": set(capabilities_for(section, grant.role)) if grant else set(),
            }
        )

    context = {
        "organization": get_organization(ctx),
        "navigation": platform_navigation(ctx),
        "memberships": memberships,
        "matrix": matrix,
        "sections": section_rows,
        "section_values": Section.values,
        "capability_values": list(Capability),
        "capability_rows": capability_rows,
        "selected_membership": selected_membership,
        "selected_grants": selected_grants,
        "scopes": AccessScope.choices,
        "projects": list(scoped_qs(ctx, Project).order_by("name")),
    }
    return render(request, "suno/access/matrix.html", context)
