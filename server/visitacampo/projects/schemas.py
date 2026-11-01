"""Esquemas de `/v1/projects/*` (`components.schemas.FieldTask*` del contrato)."""

from __future__ import annotations

import uuid

from ninja import Schema

__all__ = ["FieldTask", "FieldTaskList"]


class FieldTask(Schema):
    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    wbs: str
    outline_number: str
    name: str
    last_validated_pct: float | None


class FieldTaskList(Schema):
    version: str
    changed: bool
    items: list[FieldTask]
