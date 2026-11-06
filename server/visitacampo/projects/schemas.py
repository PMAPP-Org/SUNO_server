"""Esquemas de `/v1/projects/*` (`components.schemas.FieldTask*` del contrato)."""

from __future__ import annotations

import uuid
from typing import Literal

from ninja import Schema

__all__ = ["FieldLocation", "FieldPlannedItem", "FieldTask", "FieldTaskList"]


class FieldLocation(Schema):
    id: uuid.UUID
    label: str


class FieldPlannedItem(Schema):
    item_id: uuid.UUID
    code: str
    description: str
    unit: str
    planned_quantity: float
    executed_quantity: float


class FieldTask(Schema):
    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    wbs: str
    outline_number: str
    name: str
    last_validated_pct: float | None
    measured_by: Literal["pct", "quantities"]
    location: FieldLocation | None
    location_options: list[FieldLocation]
    planned_items: list[FieldPlannedItem]


class FieldTaskList(Schema):
    version: str
    changed: bool
    items: list[FieldTask]
