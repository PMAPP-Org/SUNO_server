"""Public web access for SUNO users.

The operational back-office remains under ``/admin/`` and keeps its staff and
OTP requirements.  These views use Django's regular session authentication so
members without admin privileges have a separate, explicit entry point.
"""

from __future__ import annotations

from typing import cast

from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from suno.core.context import Principal, RequestContext, resolve_request_context
from suno.core.models import AppUser, Assignment, Membership, Record
from suno.core.scoping import scoped_qs
from suno.projects.access import visible_projects


def _context_for(user: AppUser) -> RequestContext:
    return resolve_request_context(Principal(user_id=user.pk, org_id=user.org_id))


@require_http_methods(["GET", "POST"])
def platform_login(request: HttpRequest) -> HttpResponse:
    """Authenticate an organization member without granting admin access."""

    if request.user.is_authenticated:
        return redirect("platform_home")

    form = AuthenticationForm(request, data=request.POST or None)
    form.fields["username"].widget.attrs.update({"autocomplete": "username", "autofocus": True})
    form.fields["password"].widget.attrs.update({"autocomplete": "current-password"})
    if request.method == "POST" and form.is_valid():
        user = cast(AppUser, form.get_user())
        if _context_for(user).is_member:
            auth_login(request, user)
            if not request.POST.get("remember"):
                request.session.set_expiry(0)
            return redirect("platform_home")
        form.add_error(None, "Tu cuenta no tiene acceso activo a una organización.")

    return render(request, "suno/login.html", {"form": form})


@login_required(login_url="platform_login")
def platform_home(request: HttpRequest) -> HttpResponse:
    """Authenticated operational overview backed only by tenant-scoped data."""

    user = cast(AppUser, request.user)
    request_context = _context_for(user)
    if not request_context.is_member:
        auth_logout(request)
        return redirect("platform_login")

    records = scoped_qs(request_context, Record).filter(deleted_at__isnull=True)
    recent_records = list(
        records.select_related("template_version__template", "created_by")
        .annotate(review_count=Count("reviews"))
        .order_by("-captured_at")[:8]
    )
    located_records = list(
        records.exclude(lat__isnull=True)
        .exclude(lon__isnull=True)
        .select_related("template_version__template", "created_by")
        .order_by("-captured_at")[:80]
    )
    map_points = [
        {
            "id": str(record.pk),
            "lat": record.lat,
            "lon": record.lon,
            "title": record.template_version.template.name,
            "author": record.created_by.display_name or record.created_by.username,
            "captured_at": record.captured_at.isoformat(),
        }
        for record in located_records
    ]
    pending_assignments = (
        scoped_qs(request_context, Assignment)
        .filter(deleted_at__isnull=True)
        .annotate(record_count=Count("records"))
        .filter(record_count=0)
        .count()
    )
    context = {
        "organization": user.org,
        "recent_records": recent_records,
        "map_points": map_points,
        "record_count": records.count(),
        "pending_review_count": records.filter(reviews__isnull=True).count(),
        "pending_assignment_count": pending_assignments,
        "member_count": scoped_qs(request_context, Membership).count(),
        "projects": list(visible_projects(request_context).order_by("name")[:6]),
        "project_count": visible_projects(request_context).count(),
    }
    return render(request, "suno/platform.html", context)


@require_POST
def platform_logout(request: HttpRequest) -> HttpResponse:
    """End only the public web session; admin access keeps its own entry point."""

    auth_logout(request)
    return redirect("home")
