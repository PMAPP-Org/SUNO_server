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
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from visitacampo.core.context import Principal, RequestContext, resolve_request_context
from visitacampo.core.models import AppUser


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
            return redirect("platform_home")
        form.add_error(None, "Tu cuenta no tiene acceso activo a una organización.")

    return render(request, "suno/login.html", {"form": form})


@login_required(login_url="platform_login")
def platform_home(request: HttpRequest) -> HttpResponse:
    """Small authenticated landing page for the public-facing platform."""

    user = cast(AppUser, request.user)
    if not _context_for(user).is_member:
        auth_logout(request)
        return redirect("platform_login")
    return render(request, "suno/platform.html", {"organization": user.org})


@require_POST
def platform_logout(request: HttpRequest) -> HttpResponse:
    """End only the public web session; admin access keeps its own entry point."""

    auth_logout(request)
    return redirect("home")
