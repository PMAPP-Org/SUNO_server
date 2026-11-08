from __future__ import annotations

from typing import Any

from django import template

register = template.Library()


@register.filter
def get_item(mapping: dict[str, Any], key: str) -> Any:
    return mapping.get(key)


@register.filter
def project_list(scopes: Any) -> list[Any]:
    return [scope.project for scope in scopes]
