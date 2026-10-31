"""Cronograma de proyectos (módulo de costos, etapa 1).

Diseño: plans/2026-10-30-costos-etapa1-cronograma-design.md.

- `Task` es estable por el `UniqueID` de MS Project: cada importación
  actualiza sus campos en sitio y **nunca la borra**; si desaparece del XML
  se marca `retired_in`. Lo que es de SUNO —responsables y, en etapas
  siguientes, avance de campo, homologación y llaves contextualizadoras—
  cuelga de `Task` y sobrevive así a todas las versiones del Project.
- `ScheduleImport` es el registro de cada XML subido: el archivo original
  (gzip) y el resumen de cambios. No hay fotografía de tareas por versión.
- `TaskDependency`, `Resource` y `TaskResource` son datos del XML y se
  reemplazan en cada importación.
"""

from __future__ import annotations

import uuid

from django.db import models

from .base import OrgScopedModel


class Project(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, verbose_name="nombre")
    code = models.CharField(max_length=50, blank=True, verbose_name="código")
    description = models.TextField(blank=True, verbose_name="descripción")
    #: Calendario laboral del Project, en el formato de
    #: `projects.workdays.WorkCalendar.to_json`. Se reemplaza en cada
    #: importación; vacío equivale a lunes a viernes sin festivos.
    calendar = models.JSONField(default=dict, blank=True, verbose_name="calendario")
    #: `StatusDate` de la última importación aplicada (fecha de corte).
    status_date = models.DateField(null=True, blank=True, verbose_name="fecha de corte")
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="creado por"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "project"
        ordering = ["name"]
        verbose_name = "proyecto"
        verbose_name_plural = "proyectos"
        constraints = [
            models.UniqueConstraint(fields=["org", "name"], name="project_org_name_uq"),
        ]

    def __str__(self) -> str:
        return self.name


class ScheduleImportStatus(models.TextChoices):
    PENDING = "pending", "Pendiente de confirmar"
    APPLIED = "applied", "Aplicada"
    DISCARDED = "discarded", "Descartada"


class ScheduleImport(OrgScopedModel):
    """Un XML de MS Project subido a un proyecto.

    Nace `pending` con el XML y el resumen de cambios (la vista previa); al
    confirmarse pasa a `applied` y recibe su `number` consecutivo. Las
    descartadas se conservan como rastro.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.PROTECT, related_name="imports", verbose_name="proyecto"
    )
    #: Consecutivo por proyecto; solo lo tienen las aplicadas.
    number = models.PositiveIntegerField(null=True, blank=True, verbose_name="versión")
    status = models.CharField(
        max_length=16,
        choices=ScheduleImportStatus.choices,
        default=ScheduleImportStatus.PENDING,
        verbose_name="estado",
    )
    file_name = models.CharField(max_length=255, verbose_name="archivo")
    source_xml = models.BinaryField(verbose_name="XML original (gzip)")
    uploaded_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="subido por"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    status_date = models.DateField(null=True, blank=True, verbose_name="fecha de corte")
    #: Resumen de cambios: ver `projects.importer.summarize_changes`.
    summary = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "schedule_import"
        ordering = ["-uploaded_at"]
        verbose_name = "importación de cronograma"
        verbose_name_plural = "importaciones de cronograma"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "number"],
                condition=models.Q(number__isnull=False),
                name="schedule_import_project_number_uq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.project} v{self.number or '?'}"


class Task(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.PROTECT, related_name="tasks", verbose_name="proyecto"
    )
    #: `UniqueID` de MS Project: la identidad entre versiones.
    uid = models.IntegerField(verbose_name="UniqueID")
    wbs = models.CharField(max_length=100, blank=True, verbose_name="EDT")
    outline_number = models.CharField(max_length=100, verbose_name="número de esquema")
    outline_level = models.PositiveSmallIntegerField(verbose_name="nivel")
    #: Orden de la fila en el Project (`ID`).
    sort_order = models.IntegerField(verbose_name="orden")
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )
    #: Tarea de nivel 1 que contiene a esta (ella misma si es de nivel 1).
    stage = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    name = models.CharField(max_length=500, verbose_name="nombre")
    is_summary = models.BooleanField(default=False, verbose_name="resumen")
    is_milestone = models.BooleanField(default=False, verbose_name="hito")
    start = models.DateField(null=True, blank=True, verbose_name="inicio")
    finish = models.DateField(null=True, blank=True, verbose_name="fin")
    actual_finish = models.DateField(null=True, blank=True, verbose_name="fin real")
    duration_days = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    baseline_start = models.DateField(null=True, blank=True, verbose_name="inicio línea base")
    baseline_finish = models.DateField(null=True, blank=True, verbose_name="fin línea base")
    baseline_duration_days = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    project_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="% Project"
    )
    project_physical_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="% físico Project"
    )
    total_slack_days = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    free_slack_days = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_critical = models.BooleanField(default=False, verbose_name="crítica")
    #: Importación en la que dejó de venir en el XML; nula = vigente.
    retired_in = models.ForeignKey(
        ScheduleImport, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        db_table = "task"
        ordering = ["sort_order"]
        verbose_name = "tarea"
        verbose_name_plural = "tareas"
        constraints = [
            models.UniqueConstraint(fields=["project", "uid"], name="task_project_uid_uq"),
        ]
        indexes = [
            models.Index(fields=["project", "sort_order"], name="task_project_order_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.wbs or self.outline_number} {self.name}"


class DependencyType(models.TextChoices):
    FS = "FS", "Fin a comienzo"
    SS = "SS", "Comienzo a comienzo"
    FF = "FF", "Fin a fin"
    SF = "SF", "Comienzo a fin"


class TaskDependency(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="+")
    predecessor = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="successor_links")
    successor = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="predecessor_links")
    type = models.CharField(max_length=2, choices=DependencyType.choices, default=DependencyType.FS)
    lag_days = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        db_table = "task_dependency"
        constraints = [
            models.UniqueConstraint(
                fields=["predecessor", "successor"], name="task_dependency_pair_uq"
            ),
        ]


class Resource(OrgScopedModel):
    """Recurso del Project (ejecutor). Es un dato, no un usuario de SUNO."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="resources")
    uid = models.IntegerField()
    name = models.CharField(max_length=255)

    class Meta:
        db_table = "resource"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["project", "uid"], name="resource_project_uid_uq"),
        ]

    def __str__(self) -> str:
        return self.name


class TaskResource(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="resource_links")
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name="task_links")

    class Meta:
        db_table = "task_resource"
        constraints = [
            models.UniqueConstraint(fields=["task", "resource"], name="task_resource_pair_uq"),
        ]


class TaskResponsible(OrgScopedModel):
    """Usuario SUNO responsable de una tarea; en una resumen cubre toda la rama."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="responsibles")
    user = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="task_responsibilities"
    )
    assigned_by = models.ForeignKey("core.AppUser", on_delete=models.PROTECT, related_name="+")
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "task_responsible"
        constraints = [
            models.UniqueConstraint(fields=["task", "user"], name="task_responsible_pair_uq"),
        ]
