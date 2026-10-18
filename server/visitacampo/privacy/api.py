"""Router de `/v1/consents` (Ley 1581). Solo traducción: contexto, servicio, respuesta."""

from __future__ import annotations

from axes.helpers import get_client_ip_address
from ninja import Router, Status

from visitacampo.core.context import get_request_context
from visitacampo.privacy import services
from visitacampo.privacy.schemas import ConsentRequest, ConsentResponse

__all__ = ["router"]

router = Router(tags=["privacy"])


@router.post(
    "/consents",
    response={201: ConsentResponse},
    summary="Registra la autorización de datos personales del titular",
    operation_id="consentsRecord",
)
def record_consent(request, payload: ConsentRequest):
    """`POST /v1/consents`. Autenticado (bearer global); el titular es quien llama."""
    consent = services.record_consent(
        get_request_context(request),
        device_id=payload.device_id,
        notice_version=payload.notice_version,
        purposes=list(payload.purposes),
        accepted_at=payload.accepted_at,
        ip=get_client_ip_address(request),
    )
    return Status(201, {"id": consent.pk, "recorded_at": consent.recorded_at})
