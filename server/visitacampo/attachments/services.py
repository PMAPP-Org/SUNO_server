"""Presign, commit verificado, URL de descarga y GC (Paso 17, ADR-007).

ADR-004: aquí vive la lógica; `api.py` solo traduce a HTTP. Los cuatro flujos:

1. **presign** — ¿ya tengo este contenido? Entonces no subas nada
   (`already_present`, deduplicación por hash). Si no, firma un `PUT` con
   tamaño, tipo y checksum ya fijados.
2. **commit** — comprueba contra el objeto **real** y solo entonces registra el
   `Blob`, con un `INSERT ... ON CONFLICT DO NOTHING`.
3. **download_url** — autoriza por `visit_attachment` (que sí lleva `org_id`) y
   firma un `GET` corto.
4. **collect_unconfirmed_uploads** — la basura de las subidas que nunca se
   confirmaron. Lógica lista; quien la invoque periódicamente es el Paso 19.

**Sobre el orden de las comprobaciones del commit.** Se verifica de lo barato a
lo caro y de lo más general a lo más específico: primero el `HEAD` (una llamada,
da tamaño + checksum + `Content-Type`), y solo si todo eso cuadra se leen los
16 bytes del sniffing. Un objeto con el tamaño cambiado no llega nunca a
provocar una segunda llamada al store.

**Sobre por qué no hay `select_for_update` en ninguna parte** (RD-08): no hay
ningún campo mutable que proteger. `Blob` se inserta entero o no se inserta;
dos commits simultáneos del mismo `sha256` no compiten por nada, el segundo
`INSERT` no hace nada y los dos leen la misma fila. El lost-update de H-08
necesitaba un puntero que actualizar, y aquí no existe.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone
from ninja.errors import HttpError

from visitacampo.attachments import validation
from visitacampo.attachments.blobstore import (
    BlobStore,
    ObjectHead,
    ObjectNotFound,
    StoredObject,
    bucket_key_for,
    normalize_sha256,
)
from visitacampo.attachments.storage import get_blob_store
from visitacampo.core.context import RequestContext
from visitacampo.core.models import Blob, Visit, VisitAttachment
from visitacampo.core.policies import (
    CODE_NOT_FOUND,
    PolicyDenied,
    check_read_visit,
    check_upload_attachment,
    check_write_visit,
)
from visitacampo.core.scoping import blob_registry, scoped_qs
from visitacampo.serverinfo import capabilities as caps

__all__ = [
    "AttachmentNotFound",
    "AttachmentTooLarge",
    "ChecksumMismatch",
    "CommitResult",
    "DownloadTicket",
    "GarbageCollection",
    "MimeNotAcceptable",
    "ObjectNotUploaded",
    "PresignResult",
    "collect_unconfirmed_uploads",
    "commit",
    "download_url",
    "presign",
    "unconfirmed_sha256",
]


# --- Errores (todos con el `code` estable del contrato) ---------------------


class AttachmentTooLarge(HttpError):
    """`413 payload_too_large`: por archivo o por visita.

    Se usa el mismo código que el lote de push demasiado grande porque
    significa lo mismo para el cliente: *no reintentes esto tal cual*. La
    diferencia con un `422` importa — un `422` invitaría a reintentar creyendo
    que el problema es de forma.
    """

    code = "payload_too_large"

    def __init__(self, message: str) -> None:
        super().__init__(413, message)


class ChecksumMismatch(HttpError):
    """`422 attachment_checksum_mismatch`: el objeto no es lo que se declaró.

    Es el código del contrato (`components.schemas.ErrorCode`) y cubre las dos
    discrepancias que el plan nombra: **tamaño** y **checksum**. El `Blob` no se
    registra; el cliente debe volver a subir.
    """

    code = "attachment_checksum_mismatch"

    def __init__(self, message: str) -> None:
        super().__init__(422, message)


class MimeNotAcceptable(HttpError):
    """`422 validation_error`: el tipo declarado no está permitido o no casa con los bytes.

    Deliberadamente **distinto** de `attachment_checksum_mismatch`: el contrato
    enumera los dos casos por separado en la descripción del `422` del commit
    ("`attachment_checksum_mismatch` … o MIME fuera de la allow-list"), y para
    el cliente son acciones distintas — un checksum que no cuadra se resuelve
    resubiendo; un MIME que no casa, no se resuelve reintentando.
    """

    code = "validation_error"

    def __init__(self, message: str) -> None:
        super().__init__(422, message)


class ObjectNotUploaded(HttpError):
    """`404 not_found`: se confirma un objeto que no está en el store.

    El contrato declara `404` en el commit justamente para esto. Es un estado
    normal, no un error del servidor: el `PUT` pudo fallar, caducar la URL o
    perderse la red entre el `PUT` y el commit, y el cliente debe repetir la
    subida.
    """

    code = CODE_NOT_FOUND

    def __init__(self) -> None:
        super().__init__(404, "The uploaded object was not found in the blob store.")


class AttachmentNotFound(PolicyDenied):
    """`404` en la descarga: no existe, o no es visible desde esta organización.

    RD-11 en su forma literal (y lo que el contrato pide para este endpoint:
    "*si el solicitante no puede ver ningún adjunto que referencie ese
    `sha256`, la respuesta es `404` … no `403`*"). No se distingue "el blob no
    existe" de "existe pero es de otra organización": distinguirlos convertiría
    el endpoint en un oráculo para averiguar qué contenidos guardan otros
    tenants.
    """

    def __init__(self) -> None:
        super().__init__(404, CODE_NOT_FOUND, "Attachment not found.")


# --- Resultados -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PresignResult:
    """`components.schemas.PresignResponse`, sin el `server_time` (lo pone la vista)."""

    already_present: bool
    upload_url: str | None = None
    expires_in: int | None = None
    required_headers: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class CommitResult:
    """El `Blob` registrado y si lo creó **esta** llamada.

    `created` no viaja al cliente —el contrato responde `201` con el blob en
    los dos casos, porque para el cliente el resultado es idéntico: el
    contenido está confirmado— pero es lo que los tests de idempotencia y de
    concurrencia comprueban, y lo que un log de operación querrá registrar.
    """

    blob: Blob
    created: bool


@dataclass(frozen=True, slots=True)
class DownloadTicket:
    """`components.schemas.DownloadUrlResponse`, sin el `server_time`."""

    url: str
    expires_in: int
    mime: str
    size: int


@dataclass(frozen=True, slots=True)
class GarbageCollection:
    """Resultado del GC, para que el job del Paso 19 pueda registrarlo."""

    inspected: int
    deleted: tuple[str, ...]
    reclaimed_bytes: int


# --- 1. Presign --------------------------------------------------------------


def presign(
    ctx: RequestContext,
    *,
    sha256: str,
    size: int,
    mime: str,
    visit_id: uuid.UUID | None = None,
    store: BlobStore | None = None,
) -> PresignResult:
    """URL prefirmada de subida, o `already_present` si el contenido ya existe.

    El orden importa y es este a propósito:

    1. **Autorizar** (nunca después de haber leído nada del tenant).
    2. **Validar** el MIME y los dos límites de tamaño.
    3. **Deduplicar**: si el `sha256` ya está en `blob`, no se firma nada. Es
       "deduplicación gratis" en el sentido literal — ni una llamada al object
       storage, ni un byte por la red móvil del agente.
    4. Firmar.

    Deduplicar **después** de validar, y no antes, es deliberado: un `sha256`
    que ya existe con un tamaño declarado distinto sigue siendo una petición
    mal formada, y responder `already_present` a ciegas le confirmaría al
    cliente una mentira sobre lo que hay guardado.
    """
    digest = normalize_sha256(sha256)
    check_upload_attachment(ctx, ctx.org_id)

    try:
        validation.check_mime_allowed(mime)
    except validation.MimeRejected as exc:
        raise MimeNotAcceptable(str(exc)) from exc

    if size > caps.ATTACHMENT_MAX_BYTES:
        raise AttachmentTooLarge(
            f"Attachment of {size} bytes exceeds the per-file limit of "
            f"{caps.ATTACHMENT_MAX_BYTES} bytes."
        )

    if visit_id is not None:
        _check_visit_quota(ctx, visit_id=visit_id, incoming_size=size)

    if blob_registry().filter(pk=digest).exists():
        return PresignResult(already_present=True)

    expires_in = int(settings.ATTACHMENT_UPLOAD_URL_TTL_SECONDS)
    upload = (store or get_blob_store()).presign_upload(
        sha256=digest, size=size, mime=mime, expires_in=expires_in
    )
    return PresignResult(
        already_present=False,
        upload_url=upload.url,
        expires_in=upload.expires_in,
        required_headers=dict(upload.required_headers),
    )


def _check_visit_quota(ctx: RequestContext, *, visit_id: uuid.UUID, incoming_size: int) -> None:
    """Cuota acumulada por visita (tarea 5). Autoriza **sobre la visita real**.

    `scoped_qs` es lo que hace que una visita de otra organización sea
    indistinguible de una inexistente: el filtro por `org_id` va en el punto de
    partida, no encadenado después (RD-11).
    """
    target = scoped_qs(ctx, Visit).filter(pk=visit_id).first()
    if target is None:
        raise AttachmentNotFound()
    check_write_visit(ctx, target)

    used = _committed_bytes_of_visit(ctx, visit_id)
    if used + incoming_size > caps.ATTACHMENT_MAX_BYTES_PER_VISIT:
        raise AttachmentTooLarge(
            f"Visit attachments would reach {used + incoming_size} bytes, over the "
            f"per-visit limit of {caps.ATTACHMENT_MAX_BYTES_PER_VISIT} bytes."
        )


def _committed_bytes_of_visit(ctx: RequestContext, visit_id: uuid.UUID) -> int:
    """Bytes ya confirmados de una visita: adjuntos vivos con blob registrado.

    Dos consultas y no un `JOIN` porque `visit_attachment.sha256` es una FK
    **sin constraint física** (ver el docstring del modelo): un adjunto puede
    referenciar un blob que todavía no existe, y un `JOIN` del ORM sobre esa
    relación mezclaría "aún no subido" con "no cuenta". Contando en dos pasos,
    lo pendiente simplemente no suma — que es la semántica correcta.
    """
    pending = (
        scoped_qs(ctx, VisitAttachment)
        .filter(visit_id=visit_id, deleted_at__isnull=True)
        .values_list("blob_id", flat=True)
    )
    digests = list(pending)
    if not digests:
        return 0
    total = blob_registry().filter(pk__in=digests).aggregate(total=Sum("size"))["total"]
    return int(total or 0)


# --- 2. Commit ----------------------------------------------------------------


def commit(
    ctx: RequestContext,
    *,
    sha256: str,
    size: int,
    mime: str,
    store: BlobStore | None = None,
) -> CommitResult:
    """Verifica el objeto subido y registra el `Blob`. Idempotente.

    **La verificación completa**, en el orden en que se hace:

    - el MIME declarado está en la allow-list;
    - existe un objeto bajo la clave derivada del `sha256` (si no, `404`);
    - el tamaño del objeto es **exactamente** el declarado;
    - el store certifica un checksum SHA-256 y coincide con el `sha256` de la
      ruta — si el store **no** lo certifica, se rechaza igual: "no he podido
      comprobarlo" no es "está bien";
    - el `Content-Type` almacenado coincide con el declarado;
    - los primeros bytes del objeto son los de ese tipo (magic bytes).

    Solo entonces se inserta. Si el blob ya existía, se devuelve tal cual sin
    tocar el store: un segundo commit del mismo `sha256` es una operación
    válida y barata, no un conflicto.
    """
    digest = normalize_sha256(sha256)
    check_upload_attachment(ctx, ctx.org_id)

    existing = blob_registry().filter(pk=digest).first()
    if existing is not None:
        # Idempotencia: el contenido ya está confirmado y es inmutable, así que
        # no hay nada que volver a verificar ni nada que pudiera haber cambiado.
        return CommitResult(blob=existing, created=False)

    try:
        validation.check_mime_allowed(mime)
    except validation.MimeRejected as exc:
        raise MimeNotAcceptable(str(exc)) from exc

    if size > caps.ATTACHMENT_MAX_BYTES:
        raise AttachmentTooLarge(
            f"Attachment of {size} bytes exceeds the per-file limit of "
            f"{caps.ATTACHMENT_MAX_BYTES} bytes."
        )

    blob_store = store or get_blob_store()
    try:
        head = blob_store.head(digest)
    except ObjectNotFound as exc:
        raise ObjectNotUploaded() from exc

    _verify_head(head, digest=digest, size=size, mime=mime)
    _verify_magic_bytes(blob_store, digest=digest, mime=mime)

    return _register(digest, size=size, mime=mime)


def _verify_head(head: ObjectHead, *, digest: str, size: int, mime: str) -> None:
    """Tamaño, checksum y `Content-Type` contra el objeto realmente almacenado."""
    if head.size != size:
        raise ChecksumMismatch(f"Stored object is {head.size} bytes; {size} were declared.")
    if head.sha256 is None:
        raise ChecksumMismatch(
            "The blob store did not certify a SHA-256 checksum for the uploaded "
            "object, so its content cannot be verified."
        )
    if head.sha256 != digest:
        # Sin el checksum real en el mensaje: es un dato del objeto de otro y
        # no le dice nada útil a quien sube (RD-12).
        raise ChecksumMismatch("Stored object checksum does not match the declared sha256.")
    if head.content_type is not None and head.content_type != mime:
        raise MimeNotAcceptable(
            f"Stored object was uploaded as {head.content_type!r}, not {mime!r}."
        )


def _verify_magic_bytes(store: BlobStore, *, digest: str, mime: str) -> None:
    """*Sniffing* de los primeros bytes (tarea 5): los bytes mandan sobre lo declarado."""
    prefix = store.read_prefix(digest, validation.SNIFF_LENGTH)
    if not validation.magic_bytes_match(prefix, mime):
        raise MimeNotAcceptable(f"The uploaded bytes do not start like a {mime} file.")


def _register(digest: str, *, size: int, mime: str) -> CommitResult:
    """`INSERT ... ON CONFLICT DO NOTHING` sobre `sha256` (tarea 3, RD-08).

    `bulk_create(..., ignore_conflicts=True)` es exactamente ese SQL en el
    backend de PostgreSQL. Se usa en vez de `get_or_create` a propósito:
    `get_or_create` hace `SELECT` y luego `INSERT` **en dos sentencias**, así
    que dos procesos concurrentes pueden pasar los dos por el `SELECT` vacío y
    el segundo `INSERT` revienta con `IntegrityError` — el patrón de
    "recuperación" que el plan pide evitar. Con `ignore_conflicts` la carrera
    la resuelve PostgreSQL dentro de una sola sentencia y ningún cliente ve un
    error.

    El `.get()` posterior es el que devuelve la fila **ganadora**, sea quien sea
    quien la escribió, y de paso trae el `created_at` real que asignó la BD.

    `created` se decide comparando ese `created_at` con el que `bulk_create`
    fijó en el candidato en memoria (`auto_now_add` se aplica también en el
    camino de `bulk_create`). No se usa el valor de retorno de `bulk_create`:
    con `ignore_conflicts=True` Django documenta explícitamente que **no**
    puede fijar las claves primarias, así que ese retorno no distingue la fila
    escrita de la descartada. La comparación de instantes sí: si la fila
    guardada es la nuestra, sus marcas coinciden al microsegundo.
    """
    candidate = Blob(
        sha256=digest,
        size=size,
        mime=mime,
        bucket_key=bucket_key_for(digest),
    )
    blob_registry().bulk_create([candidate], ignore_conflicts=True)
    stored = blob_registry().get(pk=digest)
    created = candidate.created_at is not None and stored.created_at == candidate.created_at
    return CommitResult(blob=stored, created=created)


# --- 3. Descarga ---------------------------------------------------------------


def download_url(
    ctx: RequestContext,
    *,
    sha256: str,
    store: BlobStore | None = None,
) -> DownloadTicket:
    """URL prefirmada de descarga, **solo tras comprobar permisos** (ADR-007 punto 6).

    La autorización no se hace sobre `blob` —que no tiene `org_id` y no podría
    responder la pregunta— sino sobre `visit_attachment`, que sí lo tiene: se
    exige que exista **al menos un adjunto vivo de la organización del
    solicitante** que referencie este contenido, y que el solicitante pueda leer
    su visita. Es el camino que el docstring de `Blob` anticipaba desde el Paso
    5 y el que `scoping.blob_registry()` obliga a recorrer.

    El bucket nunca es público: sin pasar por aquí no hay forma de llegar a los
    bytes.
    """
    digest = normalize_sha256(sha256)

    attachment = (
        scoped_qs(ctx, VisitAttachment)
        .filter(blob_id=digest, deleted_at__isnull=True)
        .select_related("visit")
        .first()
    )
    if attachment is None:
        raise AttachmentNotFound()
    check_read_visit(ctx, attachment.visit)

    blob = blob_registry().filter(pk=digest).first()
    if blob is None:
        # El adjunto existe pero su blob aún no se ha confirmado: el contrato
        # admite ese estado (una visita se acepta con adjuntos pendientes), y
        # la respuesta correcta es la misma `404` — todavía no hay nada que
        # descargar.
        raise AttachmentNotFound()

    expires_in = int(settings.ATTACHMENT_DOWNLOAD_URL_TTL_SECONDS)
    url = (store or get_blob_store()).presign_download(
        sha256=digest,
        expires_in=expires_in,
        mime=blob.mime,
        # RD-06: el nombre se genera aquí y ahora, del MIME ya verificado.
        filename=validation.generated_filename(blob.mime),
    )
    return DownloadTicket(url=url, expires_in=expires_in, mime=blob.mime, size=blob.size)


# --- 4. GC de subidas nunca confirmadas ----------------------------------------


def unconfirmed_sha256(
    objects: Iterable[StoredObject],
    registered: frozenset[str],
    *,
    now: datetime,
    max_age: timedelta,
) -> list[StoredObject]:
    """**La decisión, pura**: qué objetos son basura. Sin I/O y sin base de datos.

    Un objeto es basura si cumple **las tres** condiciones:

    1. su clave revierte a un `sha256` válido — es decir, lo escribió este
       código y no otra cosa que comparta bucket (backups, por ejemplo);
    2. no hay una fila `blob` con ese hash, o sea que nadie llegó a confirmarlo;
    3. lleva más de `max_age` sin tocarse (24 h por defecto, ADR-007).

    La tercera condición es la que hace el job seguro frente a la carrera
    obvia: un dispositivo que acaba de hacer el `PUT` y todavía no ha llamado a
    `commit` tiene un objeto sin fila `blob`, y borrárselo sería destruir una
    subida en curso. Con 24 h de margen, el único objeto que se borra es el de
    una subida que ya nadie va a confirmar.

    Se separa de `collect_unconfirmed_uploads` justo para poder probar esta
    frontera —el objeto de 23 h 59 min no se borra, el de 24 h 01 min sí— sin
    store, sin BD y sin esperar un día.
    """
    threshold = now - max_age
    return [
        entry
        for entry in objects
        if entry.sha256 is not None
        and entry.sha256 not in registered
        and entry.last_modified <= threshold
    ]


def collect_unconfirmed_uploads(
    *,
    store: BlobStore | None = None,
    now: datetime | None = None,
    max_age: timedelta | None = None,
) -> GarbageCollection:
    """Borra los objetos subidos y nunca confirmados. **Tarea 7 del Paso 17.**

    Esta función es invocable tal cual, hoy: `collect_unconfirmed_uploads()`
    hace el trabajo completo. Lo que el Paso 19 añadirá es **cuándo** se llama
    (un job periódico de la cola `SKIP LOCKED`), no qué hace — por eso aquí no
    hay ninguna dependencia de la cola, ni un `Job`, ni un `run_after`. La
    frontera está donde el plan la pone: lógica lista, invocación después.

    Es **idempotente y reintentable**: borrar dos veces el mismo objeto no es
    un error (ver `delete` en ambos adaptadores), así que un job que muera a
    mitad y se reintente no deja nada inconsistente.
    """
    blob_store = store or get_blob_store()
    moment = now or timezone.now()
    window = max_age or timedelta(hours=int(settings.ATTACHMENT_UNCONFIRMED_MAX_AGE_HOURS))

    listed = list(blob_store.list_objects())
    candidates = {entry.sha256 for entry in listed if entry.sha256 is not None}
    registered = frozenset(blob_registry().filter(pk__in=candidates).values_list("pk", flat=True))

    garbage = unconfirmed_sha256(listed, registered, now=moment, max_age=window)
    for entry in garbage:
        assert entry.sha256 is not None  # garantizado por `unconfirmed_sha256`  # nosec B101
        blob_store.delete(entry.sha256)

    return GarbageCollection(
        inspected=len(listed),
        deleted=tuple(entry.sha256 for entry in garbage if entry.sha256 is not None),
        reclaimed_bytes=sum(entry.size for entry in garbage),
    )
