"""Datos de prueba del libro de costos. Las pruebas sí pueden usar `Model.objects`."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Max
from django.utils import timezone

from suno.budgets.tests.factories import apply_rows, make_budget
from suno.budgets.tests.samples import AIU, V1_ROWS
from suno.core.access import AccessScope, Section
from suno.core.access_services import set_section_grant
from suno.core.models import (
    AppUser,
    Blob,
    Budget,
    BudgetItem,
    CostDocumentType,
    CostEntry,
    CostEntryItem,
    CostKind,
    CostScope,
    CostStatus,
    Equipment,
    EquipmentOwnership,
    EquipmentType,
    EquipmentUsage,
    EquipmentUsageStatus,
    Project,
    Task,
    TransportContractor,
    TransportMaterial,
    TransportRoute,
    TransportVehicle,
    Trip,
)
from suno.projects.tests.factories import FieldWorld, make_field_world

#: Un PDF mínimo: lo que se revisa son sus primeros bytes (`%PDF-`).
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
#: Una "imagen" PNG: la firma de 8 bytes y relleno.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
D = Decimal


def cost_world(name: str = "Constructora") -> tuple[FieldWorld, Budget]:
    """`make_field_world` con `samples.V1_ROWS` aplicado con AIU (Administración 10 % del
    costo directo de 9.130.000,00 = 913.000,00). Capítulo 1 (1.1, 1.2) y capítulo 2 (2.1,
    2.2, 2.4). El administrador es Gestor de Costos (grant de la migración y la señal)."""
    world = make_field_world(name)
    budget = make_budget(world.project, world.admin)
    apply_rows(world.ctx(), budget, V1_ROWS, charges=AIU)
    return world, budget


def item(budget: Budget, code: str) -> BudgetItem:
    return BudgetItem.objects.get(budget=budget, code=code)


def grant_costs(
    world: FieldWorld,
    user: AppUser,
    role: Any,
    *,
    scope: Any = AccessScope.ORGANIZATION,
    projects: Sequence[Project] = (),
) -> None:
    set_section_grant(
        world.ctx(),
        user_id=user.pk,
        section=Section.COSTS,
        role=role,
        scope=scope,
        project_ids=[p.pk for p in projects],
    )


def support_blob(seed: str = "soporte") -> Blob:
    """Un `Blob` registrado (sin bytes en el almacén): basta para las restricciones y el
    cálculo. Las pruebas de subida y descarga usan `pdf_upload` con un almacén temporal."""
    digest = hashlib.sha256(seed.encode()).hexdigest()
    blob, _ = Blob.objects.get_or_create(
        sha256=digest,
        defaults={
            "size": len(PDF),
            "mime": "application/pdf",
            "bucket_key": f"blobs/{digest[:2]}/{digest[2:4]}/{digest}",
        },
    )
    return blob


def pdf_upload(name: str = "factura.pdf", content: bytes = PDF) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type="application/pdf")


def entry(
    world: FieldWorld,
    *,
    amount: str = "1000.00",
    day: date = date(2026, 3, 10),
    scope: Any = CostScope.INDIRECT,
    status: Any = CostStatus.DRAFT,
    items: Sequence[BudgetItem] = (),
    chapter: BudgetItem | None = None,
    task: Task | None = None,
    kind: Any = CostKind.MATERIALS,
    created_by: AppUser | None = None,
    decided_by: AppUser | None = None,
    attachment: Blob | None = None,
    reverses: CostEntry | None = None,
    note: str = "",
    description: str = "Concreto premezclado",
) -> CostEntry:
    """Un movimiento escrito directo en la BD (sin el servicio): para el cálculo, el cargador
    y las restricciones. Aprobado: con soporte y aprobado por la supervisión."""
    author = created_by or world.admin
    decided = status != CostStatus.DRAFT
    approver = decided_by or (world.supervisor if decided else None)
    if attachment is None and status == CostStatus.APPROVED and reverses is None:
        attachment = support_blob()
    row = CostEntry.objects.create(
        org=world.org,
        project=world.project,
        incurred_on=day,
        kind=kind,
        description=description,
        amount=D(amount),
        document_type=CostDocumentType.INVOICE,
        scope=scope,
        chapter=chapter,
        task=task,
        status=status,
        created_by=author,
        updated_by=author,
        decided_by=approver,
        decided_at=timezone.now() if decided else None,
        decision_note=note or ("Sin soporte válido" if status == CostStatus.REJECTED else ""),
        attachment=attachment,
        reverses=reverses,
    )
    for budget_item in items:
        CostEntryItem.objects.create(org=world.org, entry=row, item=budget_item)
    return row


def equipment(
    world: FieldWorld, code: str = "RE-01", *, ownership: Any = EquipmentOwnership.OWN
) -> Equipment:
    kind, _ = EquipmentType.objects.get_or_create(
        org=world.org, code="RET", defaults={"name": "Retroexcavadora"}
    )
    return Equipment.objects.create(
        org=world.org,
        code=code,
        name=f"Retroexcavadora {code}",
        equipment_type=kind,
        ownership=ownership,
        created_by=world.admin,
        updated_by=world.admin,
    )


def usage(
    world: FieldWorld,
    machine: Equipment,
    day: date,
    cost: str | None,
    *,
    budget_item: BudgetItem | None = None,
    status: Any = EquipmentUsageStatus.COMPLETED,
    hours: str = "8.00",
) -> EquipmentUsage:
    """Un uso con su costo ya calculado (como lo deja `_create_usage`); `cost=None`: sin tarifa."""
    priced = cost is not None
    return EquipmentUsage.objects.create(
        org=world.org,
        equipment=machine,
        project=world.project,
        operator=world.agent,
        usage_date=day,
        start_time=time(7),
        end_time=time(15),
        activity="Excavación",
        status=status,
        budget_item=budget_item,
        billable_hours=D(hours) if priced else None,
        hourly_rate=(D(cost) / D(hours)).quantize(D("0.01")) if cost is not None else None,
        cost=D(cost) if cost is not None else None,
        created_by=world.admin,
        updated_by=world.admin,
    )


def trip(world: FieldWorld, day: date, amount: str | None, *, own: Equipment | None = None) -> Trip:
    """Un viaje de 14 m³; de un vehículo de terceros salvo que se pase `own` (equipo propio)."""
    material, _ = TransportMaterial.objects.get_or_create(org=world.org, name="Gravas")
    route, _ = TransportRoute.objects.get_or_create(
        org=world.org, origin="Cantera", destination="Obra", defaults={"distance_km": D("8.5")}
    )
    vehicle = None
    if own is None:
        contractor, _ = TransportContractor.objects.get_or_create(
            org=world.org, name="Transportes TDR"
        )
        vehicle, _ = TransportVehicle.objects.get_or_create(
            org=world.org, plate="ABC123", defaults={"contractor": contractor}
        )
    number = (Trip.objects.filter(org=world.org).aggregate(top=Max("number"))["top"] or 0) + 1
    return Trip.objects.create(
        id=uuid.uuid4(),
        org=world.org,
        number=number,
        recorded_at=timezone.make_aware(datetime.combine(day, time(10))),
        equipment=own,
        vehicle=vehicle,
        project=world.project,
        route=route,
        distance_km=route.distance_km,
        material=material,
        volume_m3=D("14"),
        unit_price=(D(amount) / D("14")).quantize(D("0.01")) if amount is not None else None,
        amount=D(amount) if amount is not None else None,
        requested_by=world.agent,
        dispatched_by=world.supervisor,
        received_by=world.admin,
        created_by=world.admin,
    )
