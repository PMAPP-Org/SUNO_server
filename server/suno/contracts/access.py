"""Quién ve los contratos del proyecto y cómo se resuelven los objetos de la URL.

Los contratos son de la sección Costos (spec 4): el mismo 404 de la 6a para quien no la lee."""

from __future__ import annotations

from uuid import UUID

from django.http import Http404

from suno.core.context import RequestContext
from suno.core.models import Contract, ContractAmendment, ContractCertificate, Project
from suno.core.scoping import scoped_qs
from suno.costs.access import get_costs_project


def get_contracts_project(ctx: RequestContext, project_id: UUID) -> Project:
    return get_costs_project(ctx, project_id)


def get_contract(ctx: RequestContext, project: Project, contract_id: UUID) -> Contract:
    contract = (
        scoped_qs(ctx, Contract)
        .select_related("created_by", "updated_by", "approved_by", "rejected_by", "closed_by")
        .filter(pk=contract_id, project=project)
        .first()
    )
    if contract is None:
        raise Http404
    return contract


def get_amendment(ctx: RequestContext, contract: Contract, amendment_id: UUID) -> ContractAmendment:
    amendment = (
        scoped_qs(ctx, ContractAmendment)
        .select_related("created_by", "decided_by")
        .filter(pk=amendment_id, contract=contract)
        .first()
    )
    if amendment is None:
        raise Http404
    return amendment


def get_certificate(
    ctx: RequestContext, contract: Contract, certificate_id: UUID
) -> ContractCertificate:
    certificate = (
        scoped_qs(ctx, ContractCertificate)
        .select_related("created_by", "updated_by", "decided_by", "voided_by")
        .filter(pk=certificate_id, contract=contract)
        .first()
    )
    if certificate is None:
        raise Http404
    return certificate
