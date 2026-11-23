"""Estado de navegación derivado del mismo RequestContext que autoriza."""

from __future__ import annotations

from suno.core.access import Section
from suno.core.context import RequestContext
from suno.core.policies import can_read_labor_registry


def platform_navigation(ctx: RequestContext) -> dict[str, bool]:
    found = {section: ctx.has_section_access(section) for section in Section.values}
    # "Trabajadores" (costos, pieza 6d): solo con Mano de obra en toda la organización.
    found["LABOR_REGISTRY"] = can_read_labor_registry(ctx, ctx.org_id)
    return found
