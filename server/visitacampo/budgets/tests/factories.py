"""Presupuestos de prueba con BD. Los tests sí pueden usar `Model.objects`."""

from __future__ import annotations

from typing import Any

from visitacampo.budgets.importer import (
    apply_version,
    charges_of,
    create_upload,
    prepare_version,
    update_pending,
)
from visitacampo.budgets.totals import ChargeSpec
from visitacampo.core.context import RequestContext
from visitacampo.core.models import AppUser, Budget, BudgetVersion, Project

from .samples import MAPPING, xlsx


def make_budget(
    project: Project, user: AppUser, *, name: str = "Contrato de obra", code: str = "C-01"
) -> Budget:
    return Budget.objects.create(
        org_id=project.org_id, project=project, name=name, code=code, created_by=user
    )


def stage_rows(
    ctx: RequestContext,
    budget: Budget,
    rows: list[list[Any]],
    *,
    file_name: str = "presupuesto.xlsx",
) -> BudgetVersion:
    """Sube el Excel de `rows` y prepara su vista previa con `samples.MAPPING`."""
    version = create_upload(ctx, budget, file_name=file_name, data=xlsx(rows))
    return prepare_version(ctx, version.pk, MAPPING)


def apply_rows(
    ctx: RequestContext,
    budget: Budget,
    rows: list[list[Any]],
    *,
    label: str = "",
    charges: list[ChargeSpec] | None = None,
) -> BudgetVersion:
    """Sube, prepara y aplica una versión. Sin `charges` usa los que propone la vista previa."""
    version = stage_rows(ctx, budget, rows)
    if label or charges is not None:
        update_pending(
            ctx,
            version.pk,
            label=label,
            charges=charges if charges is not None else charges_of(ctx, version),
        )
    return apply_version(ctx, version.pk)
