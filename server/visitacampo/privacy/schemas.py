"""Esquemas de `/v1/consents` (`components.schemas.ConsentRequest` / `ConsentResponse`)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from ninja import Schema
from pydantic import ConfigDict, Field, field_validator

from visitacampo.authn.schemas import Timestamp

__all__ = ["ConsentRequest", "ConsentResponse", "Purpose"]

#: Mismos valores que `core.models.PrivacyPurpose`; un test compara los dos.
Purpose = Literal["account", "location", "photos"]

NoticeVersion = Annotated[
    str, Field(min_length=1, max_length=32, pattern=r"^[0-9A-Za-z][0-9A-Za-z._-]*$")
]


class ConsentRequest(Schema):
    """Lo que el agente aceptó en el aviso de privacidad, y desde qué teléfono."""

    model_config = ConfigDict(extra="forbid")

    device_id: uuid.UUID
    notice_version: NoticeVersion
    purposes: list[Purpose] = Field(min_length=1, max_length=3)
    accepted_at: datetime

    @field_validator("purposes")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        # `uniqueItems: true` en el contrato: una finalidad repetida es un error
        # del cliente, no algo que el servidor arregle en silencio.
        if len(set(value)) != len(value):
            raise ValueError("purposes must not repeat")
        return value


class ConsentResponse(Schema):
    id: uuid.UUID
    recorded_at: Timestamp
