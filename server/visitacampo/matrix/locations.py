"""Árbol de ubicación de un proyecto: niveles, nombres, rutas y etiquetas.

Un nodo se identifica por su padre y su nombre normalizado (`node_key`), así
que "Tramo 2" y "tramo  2" son el mismo lugar. `Tree` carga todos los nodos del
proyecto en una query y resuelve rutas, etiquetas y descendientes en memoria.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.db.models.functions import Lower

from visitacampo.budgets.mapping import fold
from visitacampo.core import audit
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import RequestContext
from visitacampo.core.models import MAX_LEVELS, LocationLevel, LocationNode, Project
from visitacampo.core.scoping import scoped_qs

MAX_NAME = 120
MAX_LEVEL_NAME = 60
SEPARATOR = " / "


class LevelError(ValueError):
    """Los niveles no se pueden guardar así; el mensaje es para el usuario."""


def clean_name(value: object) -> str:
    """Texto de una celda o un campo como nombre: espacios colapsados; 2.0 → "2"."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, Decimal) and value == value.to_integral_value():
        value = int(value)
    return " ".join(str(value).split())


def node_key(name: str) -> str:
    return " ".join(fold(name).split())


@dataclass(frozen=True)
class PathValues:
    #: Los nombres desde el nivel 1 hasta el último con valor sin huecos.
    names: tuple[str, ...]
    #: Había un valor después de un nivel vacío (se ignoró).
    incomplete: bool
    #: Algún nombre pasaba de 120 caracteres y se recortó.
    truncated: bool


def path_values(values: Sequence[object]) -> PathValues:
    names: list[str] = []
    incomplete = truncated = False
    gap = False
    for raw in values:
        name = clean_name(raw)
        if not name:
            gap = True
            continue
        if gap:
            incomplete = True
            break
        if len(name) > MAX_NAME:
            name, truncated = name[:MAX_NAME], True
        names.append(name)
    return PathValues(tuple(names), incomplete, truncated)


@dataclass(frozen=True)
class LevelSpec:
    name: str
    source_field: str = ""


def project_levels(ctx: RequestContext, project: Project) -> list[LocationLevel]:
    return list(scoped_qs(ctx, LocationLevel).filter(project=project).order_by("depth"))


def from_project(levels: Sequence[LocationLevel]) -> bool:
    """La ubicación sale de MS Project (todos los niveles tienen campo)."""
    return bool(levels) and all(level.source_field for level in levels)


def _validate(specs: Sequence[LevelSpec]) -> list[LevelSpec]:
    if len(specs) > MAX_LEVELS:
        raise LevelError(f"Se admiten hasta {MAX_LEVELS} niveles.")
    cleaned: list[LevelSpec] = []
    seen: set[str] = set()
    for spec in specs:
        name = clean_name(spec.name)
        if not name:
            raise LevelError("Cada nivel necesita un nombre.")
        if len(name) > MAX_LEVEL_NAME:
            raise LevelError(f"El nombre «{name[:20]}…» pasa de {MAX_LEVEL_NAME} caracteres.")
        key = node_key(name)
        if key in seen:
            raise LevelError(f"El nivel «{name}» está repetido.")
        seen.add(key)
        source = spec.source_field.strip()
        if source and not (source.isdigit() and len(source) <= 20):
            raise LevelError(f"El campo de Project del nivel «{name}» no es válido.")
        cleaned.append(LevelSpec(name, source))
    sourced = [bool(spec.source_field) for spec in cleaned]
    if any(sourced) and not all(sourced):
        raise LevelError(
            "Si un nivel se lee de MS Project, todos los niveles deben tener su campo."
        )
    return cleaned


def save_levels(ctx: RequestContext, project: Project, specs: Sequence[LevelSpec]) -> None:
    """Reemplaza los niveles. Quitar un nivel solo se permite si ningún nodo lo usa."""
    cleaned = _validate(specs)
    with transaction.atomic():
        scoped_qs(ctx, Project).select_for_update().get(pk=project.pk)
        levels = scoped_qs(ctx, LocationLevel).filter(project=project)
        existing = {level.depth: level for level in levels}
        for depth, level in sorted(existing.items()):
            if depth > len(cleaned):
                if scoped_qs(ctx, LocationNode).filter(level=level).exists():
                    raise LevelError(
                        f"El nivel «{level.name}» tiene ubicaciones: no se puede quitar."
                    )
                level.delete()
        # Dos pasadas: intercambiar nombres ("Tramo" ↔ "PR") no choca con el UNIQUE.
        kept = [existing[d] for d in range(1, len(cleaned) + 1) if d in existing]
        for level in kept:
            level.name = f"~{level.depth}~{uuid.uuid4().hex[:8]}"
            level.save(update_fields=["name"])
        for depth, spec in enumerate(cleaned, start=1):
            level = existing.get(depth)
            if level is None:
                scoped_qs(ctx, LocationLevel).create(
                    org_id=ctx.org_id,
                    project=project,
                    depth=depth,
                    name=spec.name,
                    source_field=spec.source_field,
                )
            else:
                level.name, level.source_field = spec.name, spec.source_field
                level.save(update_fields=["name", "source_field"])
        audit.record(
            ctx,
            action=AuditAction.MATRIX_LEVELS_CHANGED,
            target=f"project:{project.pk}",
            metadata={
                "levels": [spec.name for spec in cleaned],
                "from_project": bool(cleaned) and bool(cleaned[0].source_field),
            },
        )


def level_named(levels: Iterable[LocationLevel], name: str) -> LocationLevel | None:
    key = node_key(name)
    return next((level for level in levels if node_key(level.name) == key), None)


@dataclass
class Tree:
    """Los nodos de un proyecto, en memoria."""

    project: Project
    nodes: dict[UUID, LocationNode] = field(default_factory=dict)
    _children: dict[tuple[UUID | None, str], UUID] = field(default_factory=dict)

    def _add(self, node: LocationNode) -> None:
        self.nodes[node.pk] = node
        self._children[(node.parent_id, node.key)] = node.pk

    def find(self, names: Sequence[str]) -> UUID | None:
        parent: UUID | None = None
        for name in names:
            found = self._children.get((parent, node_key(name)))
            if found is None:
                return None
            parent = found
        return parent

    def ensure(
        self, ctx: RequestContext, levels: Sequence[LocationLevel], names: Sequence[str]
    ) -> tuple[LocationNode, list[LocationNode]]:
        """El nodo de la ruta `names`, creando los que falten. Al menos un nombre."""
        if not names or len(names) > len(levels):
            raise LevelError("La ruta no coincide con los niveles del proyecto.")
        created: list[LocationNode] = []
        parent: LocationNode | None = None
        for level, name in zip(levels, names, strict=False):
            key = node_key(name)
            found = self._children.get((parent.pk if parent else None, key))
            if found is not None:
                node = self.nodes[found]
                if node.retired_at is not None:
                    node.retired_at = None
                    node.save(update_fields=["retired_at"])
            else:
                node_id = uuid.uuid4()
                node = scoped_qs(ctx, LocationNode).create(
                    id=node_id,
                    org_id=ctx.org_id,
                    project=self.project,
                    level=level,
                    parent=parent,
                    name=name[:MAX_NAME],
                    key=key[:MAX_NAME],
                    path=f"{parent.path if parent else '/'}{node_id.hex}/",
                )
                self._add(node)
                created.append(node)
            parent = node
        if parent is None:  # `names` no vacío: no ocurre
            raise LevelError("La ruta está vacía.")
        return parent, created

    def names(self, node_id: UUID) -> list[str]:
        node = self.nodes[node_id]
        ids = [UUID(hex=part) for part in node.path.strip("/").split("/")]
        return [self.nodes[i].name for i in ids if i in self.nodes]

    def label(self, node_id: UUID | None) -> str:
        if node_id is None or node_id not in self.nodes:
            return ""
        return SEPARATOR.join(self.names(node_id))

    def contains(self, ancestor_id: UUID, node_id: UUID) -> bool:
        """`node_id` es `ancestor_id` o está dentro de él."""
        ancestor, node = self.nodes.get(ancestor_id), self.nodes.get(node_id)
        return ancestor is not None and node is not None and node.path.startswith(ancestor.path)

    def descendants(self, node_id: UUID) -> list[UUID]:
        """El nodo y los vigentes que cuelgan de él, por etiqueta."""
        root = self.nodes[node_id]
        found = [
            n.pk
            for n in self.nodes.values()
            if n.path.startswith(root.path) and (n.retired_at is None or n.pk == node_id)
        ]
        return sorted(found, key=lambda pk: [fold(name) for name in self.names(pk)])


def load_tree(ctx: RequestContext, project: Project) -> Tree:
    tree = Tree(project=project)
    for node in (
        scoped_qs(ctx, LocationNode)
        .filter(project=project)
        .select_related("level")
        .order_by(Lower("name"))
    ):
        tree._add(node)
    return tree
