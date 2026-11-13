"""Importar costos desde Excel (costos, pieza 6a, spec 2.6).

- Hoja "Costos" con el encabezado en la fila 1; las columnas se encuentran por su texto (sin
  importar mayúsculas ni tildes), así que se pueden reordenar.
- Código vacío: costo indirecto (sin tarea). Un código de capítulo asigna el capítulo completo;
  las selecciones de ítems solo se hacen en el formulario. Un código que esté en dos
  presupuestos del proyecto es un error de la fila.
- La tarea se identifica por su número de esquema ("1.2").
- La vista previa no guarda nada: las filas validadas viajan firmadas (`django.core.signing`)
  en el formulario durante una hora; al aplicar se verifica la firma, el proyecto y el usuario,
  y se valida todo otra vez. Todo o nada, y todo en borrador; los soportes van después.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction

from suno.backoffice.export import safe_cell, to_xlsx_book
from suno.budgets.excel import ExcelRejected, preview_sheet, read_rows
from suno.budgets.mapping import fold, normalize_header
from suno.budgets.totals import money, parse_number_detail
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    BudgetRowKind,
    CostDocumentType,
    CostEntry,
    CostEntryItem,
    CostImportToken,
    CostKind,
    CostScope,
    CostStatus,
    Project,
    Task,
)
from suno.core.policies import can_import_costs
from suno.core.scoping import scoped_qs

from .catalog import Catalog, assignable_tasks, load_catalog
from .services import CostRejected, EntryData, entry_fields, validate_entry

SHEET = "Costos"
H_DATE = "Fecha"
H_KIND = "Tipo"
H_CODE = "Código de ítem o capítulo"
H_TASK = "Tarea"
H_DESCRIPTION = "Descripción"
H_AMOUNT = "Valor"
H_THIRD = "Tercero"
H_DOC_TYPE = "Tipo de documento"
H_DOC_NUMBER = "Número de documento"
HEADER = [
    H_DATE, H_KIND, H_CODE, H_TASK, H_DESCRIPTION, H_AMOUNT, H_THIRD, H_DOC_TYPE, H_DOC_NUMBER,
]  # fmt: skip
REQUIRED = (H_DATE, H_KIND, H_CODE, H_DESCRIPTION, H_AMOUNT, H_DOC_TYPE)
MAX_IMPORT_ROWS = 2000
MAX_ERRORS = 50
#: Sal de la firma de la vista previa (no es un secreto: la clave es `SECRET_KEY`).
PREVIEW_SALT = "suno.costs.import"
TOKEN_MAX_AGE = 3600
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y")
INDIRECT_LABEL = "Indirecto (proyecto)"
#: Diferencia con los centavos por debajo de la cual un número de Excel es ruido de coma
#: flotante.
FLOAT_NOISE = Decimal("0.000001")
ALREADY_APPLIED = "Esta vista previa ya se cargó: revisa los borradores antes de importar otra vez."


@dataclass(frozen=True)
class PreviewRow:
    #: Fila del Excel (el encabezado es la 1).
    line: int
    data: EntryData
    assignment: str
    task: str
    #: Avisos que no impiden cargar la fila (un punto de miles ambiguo).
    notes: tuple[str, ...] = ()

    @property
    def kind_label(self) -> str:
        return str(CostKind(self.data.kind).label)


@dataclass(frozen=True)
class ImportPreview:
    rows: tuple[PreviewRow, ...] = ()
    errors: tuple[str, ...] = ()
    #: Vacío si hay errores: no se puede aplicar.
    token: str = ""

    @property
    def total(self) -> Decimal:
        return sum((row.data.amount for row in self.rows), Decimal(0))

    @property
    def notes(self) -> tuple[str, ...]:
        return tuple(f"Fila {row.line}: {note}" for row in self.rows for note in row.notes)


def template_bytes(ctx: RequestContext, project: Project) -> bytes:
    """La hoja para llenar y, aparte, las listas válidas (tipos, documentos, códigos, tareas)."""
    catalog = load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    return to_xlsx_book(
        [
            (SHEET, HEADER, []),
            ("Tipos", [H_KIND], [[label] for _, label in CostKind.choices]),
            ("Documentos", [H_DOC_TYPE], [[label] for _, label in CostDocumentType.choices]),
            (
                "Códigos",
                ["Código", "Descripción", "Clase", "Presupuesto"],
                [
                    [
                        safe_cell(row.code),
                        safe_cell(row.description),
                        "Capítulo" if row.kind == BudgetRowKind.CHAPTER else "Ítem",
                        safe_cell(row.budget),
                    ]
                    for row in catalog.rows.values()
                ],
            ),
            (
                "Tareas",
                [H_TASK, "Nombre"],
                [[safe_cell(t.outline_number), safe_cell(t.name)] for t in tasks.values()],
            ),
        ]
    )


def _present(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def _text(value: Any) -> str:
    """Texto de una celda. Un número entero de Excel ("2") sale sin decimales."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    text = str(value).strip()
    # Deshace el apóstrofo que `safe_cell` antepone a lo que empieza con = + - @.
    if text[:1] == "'" and text[1:2] in ("=", "+", "-", "@"):
        return text[1:]
    return text


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _thousands(value: Decimal) -> str:
    """`1234` → "1.234" (miles con punto, como se escriben en Colombia)."""
    return f"{value:,.0f}".replace(",", ".")


def _amount(value: Any) -> tuple[Decimal | None, str]:
    """El valor de una celda y, si se leyó "punto + 3 dígitos" como miles, el aviso.

    Una celda numérica de Excel guarda el resultado de una fórmula con 17 cifras
    (`119000.00000000001`): si está a menos de una millonésima de los centavos, es ruido de
    coma flotante y se redondea. Un texto escrito a mano se valida estricto (dos decimales)."""
    amount, guessed = parse_number_detail(value)
    if isinstance(value, float) and amount is not None:
        cents = money(amount)
        if abs(amount - cents) < FLOAT_NOISE:
            amount = cents
    note = (
        f"El valor «{_text(value)}» se leyó como {_thousands(amount)} (el punto separa miles); "
        "si eran decimales, escríbelos con coma."
        if guessed and amount is not None
        else ""
    )
    return amount, note


def _choice(value: Any, choices: Sequence[tuple[str, str]]) -> str | None:
    wanted = fold(_text(value)).strip()
    for key, label in choices:
        if wanted in (fold(label), key):
            return key
    return None


def _columns(header: Sequence[Any]) -> tuple[dict[str, int], list[str]]:
    keys = {normalize_header(name): name for name in HEADER}
    found: dict[str, int] = {}
    for index, value in enumerate(header):
        name = keys.get(normalize_header(value))
        if name is not None and name not in found:
            found[name] = index
    return found, [f"Falta la columna «{name}»." for name in REQUIRED if name not in found]


def _parse_row(
    values: Sequence[Any],
    columns: Mapping[str, int],
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
    by_outline: Mapping[str, Task],
) -> tuple[list[str], tuple[EntryData, str, str, tuple[str, ...]] | None]:
    def cell(name: str) -> Any:
        index = columns.get(name)
        return values[index] if index is not None and index < len(values) else None

    errors: list[str] = []
    day = _day(cell(H_DATE))
    if day is None:
        errors.append("La fecha no es válida (usa AAAA-MM-DD o DD/MM/AAAA).")
    kind = _choice(cell(H_KIND), CostKind.choices)
    if kind is None:
        errors.append(f"El tipo «{_text(cell(H_KIND))}» no existe.")
    document_type = _choice(cell(H_DOC_TYPE), CostDocumentType.choices)
    if document_type is None:
        errors.append(f"El tipo de documento «{_text(cell(H_DOC_TYPE))}» no existe.")
    try:
        amount, note = _amount(cell(H_AMOUNT))
    except ValueError:
        amount, note = None, ""
    if amount is None:
        errors.append("El valor no es un número.")
    scope: str = str(CostScope.INDIRECT)
    item_ids: tuple[UUID, ...] = ()
    chapter_id: UUID | None = None
    assignment = INDIRECT_LABEL
    code = _text(cell(H_CODE))
    if code:
        found = catalog.by_code(code)
        if not found:
            errors.append(
                f"No existe el código «{code}» en la versión vigente de los presupuestos."
            )
        elif len(found) > 1:
            errors.append(f"El código «{code}» está en más de un presupuesto del proyecto.")
        elif found[0].kind == BudgetRowKind.ITEM:
            scope, item_ids, assignment = str(CostScope.ITEM), (found[0].item_id,), found[0].label
        else:
            scope, chapter_id = str(CostScope.CHAPTER), found[0].item_id
            assignment = f"Capítulo {found[0].label}"
    task_id: UUID | None = None
    task_label = ""
    outline = _text(cell(H_TASK))
    if outline:
        task = by_outline.get(outline)
        if task is None:
            errors.append(f"La tarea «{outline}» no es una tarea vigente del proyecto.")
        else:
            task_id, task_label = task.pk, f"{task.outline_number} {task.name}"
    if errors or day is None or kind is None or document_type is None or amount is None:
        return errors, None
    data = EntryData(
        incurred_on=day,
        kind=kind,
        description=_text(cell(H_DESCRIPTION)),
        amount=amount,
        third_party=_text(cell(H_THIRD)),
        document_type=document_type,
        document_number=_text(cell(H_DOC_NUMBER)),
        scope=scope,
        item_ids=item_ids,
        chapter_id=chapter_id,
        task_id=task_id,
    )
    problems, checked = validate_entry(catalog, tasks, data)
    if checked is None:
        return problems, None
    return [], (data, assignment, task_label, (note,) if note else ())


def preview(ctx: RequestContext, project: Project, data: bytes) -> ImportPreview:
    if not can_import_costs(ctx, project):
        raise PermissionDenied
    try:
        sheet = preview_sheet(data, SHEET, rows=1).sheet
        table = read_rows(data, sheet, max_rows=MAX_IMPORT_ROWS + 1)
    except ExcelRejected as exc:
        return ImportPreview(errors=(str(exc),))
    if not table:
        return ImportPreview(errors=("El archivo está vacío.",))
    columns, missing = _columns(table[0])
    if missing:
        return ImportPreview(errors=tuple(missing))
    catalog = load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    by_outline = {task.outline_number: task for task in tasks.values()}
    rows: list[PreviewRow] = []
    errors: list[str] = []
    for line, values in enumerate(table[1:], start=2):
        if not any(_present(value) for value in values):
            continue
        problems, parsed = _parse_row(values, columns, catalog, tasks, by_outline)
        errors.extend(f"Fila {line}: {problem}" for problem in problems)
        if parsed is not None:
            rows.append(PreviewRow(line, *parsed))
        if len(errors) >= MAX_ERRORS:
            errors.append("Hay más errores: corrige estos y vuelve a subir el archivo.")
            break
    if not rows and not errors:
        errors.append("El archivo no tiene filas de costos.")
    if errors:
        return ImportPreview(rows=tuple(rows), errors=tuple(errors))
    return ImportPreview(rows=tuple(rows), token=_sign(ctx, project, rows))


def _serialize(row: PreviewRow) -> dict[str, Any]:
    data = row.data
    return {
        "line": row.line,
        "date": data.incurred_on.isoformat(),
        "kind": data.kind,
        "description": data.description,
        "amount": str(data.amount),
        "third": data.third_party,
        "doc": data.document_type,
        "number": data.document_number,
        "scope": data.scope,
        "items": [str(item_id) for item_id in data.item_ids],
        "chapter": str(data.chapter_id) if data.chapter_id else "",
        "task": str(data.task_id) if data.task_id else "",
    }


def _deserialize(raw: Mapping[str, Any]) -> tuple[int, EntryData]:
    return int(raw["line"]), EntryData(
        incurred_on=date.fromisoformat(raw["date"]),
        kind=raw["kind"],
        description=raw["description"],
        amount=Decimal(raw["amount"]),
        third_party=raw["third"],
        document_type=raw["doc"],
        document_number=raw["number"],
        scope=raw["scope"],
        item_ids=tuple(UUID(value) for value in raw["items"]),
        chapter_id=UUID(raw["chapter"]) if raw["chapter"] else None,
        task_id=UUID(raw["task"]) if raw["task"] else None,
    )


def _sign(ctx: RequestContext, project: Project, rows: Sequence[PreviewRow]) -> str:
    payload = {
        "project": str(project.pk),
        "user": str(ctx.user_id),
        #: Una vista previa se carga una sola vez (`CostImportToken`).
        "nonce": secrets.token_urlsafe(24),
        "rows": [_serialize(row) for row in rows],
    }
    return signing.dumps(payload, salt=PREVIEW_SALT, compress=True)


def apply(ctx: RequestContext, project: Project, token: str) -> int:
    """Crea en borrador todas las filas de la vista previa, o ninguna."""
    if not can_import_costs(ctx, project):
        raise PermissionDenied
    try:
        payload = signing.loads(token, salt=PREVIEW_SALT, max_age=TOKEN_MAX_AGE)
    except signing.SignatureExpired as exc:
        raise CostRejected(["La vista previa caducó: vuelve a subir el archivo."]) from exc
    except signing.BadSignature as exc:
        raise CostRejected(["La vista previa no es válida: vuelve a subir el archivo."]) from exc
    if payload.get("project") != str(project.pk) or payload.get("user") != str(ctx.user_id):
        raise CostRejected(["La vista previa no es tuya o no es de este proyecto."])
    nonce = payload.get("nonce")
    if not isinstance(nonce, str) or not nonce:
        raise CostRejected(["La vista previa no es válida: vuelve a subir el archivo."])
    catalog = load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    checked = []
    errors: list[str] = []
    for raw in payload.get("rows", []):
        line, data = _deserialize(raw)
        problems, assignment = validate_entry(catalog, tasks, data)
        if assignment is None:
            errors.extend(f"Fila {line}: {problem}" for problem in problems)
        else:
            checked.append((data, assignment))
    if errors or not checked:
        raise CostRejected(errors[:MAX_ERRORS] or ["La vista previa no tiene filas."])
    with transaction.atomic():
        # El nonce es único: si otra petición ya cargó esta vista previa (o la está cargando),
        # el INSERT espera a que termine y falla; así no se duplican los borradores.
        try:
            with transaction.atomic():
                scoped_qs(ctx, CostImportToken).create(
                    org_id=ctx.org_id,
                    nonce=nonce,
                    project=project,
                    applied_by_id=ctx.user_id,
                )
        except IntegrityError as exc:
            raise CostRejected([ALREADY_APPLIED]) from exc
        created = scoped_qs(ctx, CostEntry).bulk_create(
            [
                CostEntry(
                    org_id=ctx.org_id,
                    project=project,
                    status=CostStatus.DRAFT,
                    created_by_id=ctx.user_id,
                    updated_by_id=ctx.user_id,
                    **entry_fields(data, assignment),
                )
                for data, assignment in checked
            ]
        )
        scoped_qs(ctx, CostEntryItem).bulk_create(
            [
                CostEntryItem(org_id=ctx.org_id, entry=entry, item_id=item_id)
                for entry, (_, assignment) in zip(created, checked, strict=True)
                for item_id in assignment.item_ids
            ]
        )
        audit.record(
            ctx,
            action=AuditAction.COST_IMPORTED,
            target=f"project:{project.pk}",
            metadata={
                "rows": len(created),
                "amount": str(sum((data.amount for data, _ in checked), Decimal(0))),
            },
        )
    return len(created)
