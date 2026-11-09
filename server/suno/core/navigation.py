"""Estado de navegación derivado del mismo RequestContext que autoriza."""

from __future__ import annotations

from suno.core.access import Section
from suno.core.context import RequestContext


def platform_navigation(ctx: RequestContext) -> dict[str, bool]:
    return {section: ctx.has_section_access(section) for section in Section.values}
