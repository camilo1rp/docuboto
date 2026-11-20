# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La única puerta de los demás dominios al almacén (ADR-047 §1).

Sustituye a los cinco clientes HTTP que archive, document, signature y knowledge
tenían contra el mismo proceso. Cada función hace lo que hacía la ruta interna que
reemplaza, sin cabeceras: el tenant y el actor llegan como argumentos y la
autorización de la persona ya la decidió quien llama (con su propia comprobación en
la BD). Lo que el llamante sabía distinguir por código HTTP llega ahora como
excepción de este módulo o como el mismo `*Outcome` que ya consumía.

Las funciones de resultado (`protect_*`, `renew_retention`, `package_aip`,
`repackage_aip`) y las best-effort (`record_event`, `get_file_hashes`,
`read_signed_*`) nunca lanzan: un fallo inesperado del almacén era un 500 que el
cliente leía como transitorio, y así sigue.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import asyncpg
from pydantic import ValidationError

from storage.core.config import settings
from storage.core.constants import SYSTEM_RECONCILER_ID
from storage.core.database import get_pool
from storage.core.minio_client import get_minio
from storage.repositories.file_repository import FileRepository
from storage.repositories.preservacion_repository import PreservacionRepository
from storage.schemas.preservacion import (
    AipCreate,
    AipReempaquetarRequest,
    EventoInternalCreate,
    EventoResponse,
    ExportZipCreate,
    FijarRetencionRequest,
    HoldFijarRequest,
    HoldLevantarRequest,
    ProtegerIndiceRequest,
    RenovarArtefactoRequest,
    RenovarRetencionRequest,
    canonicalize_expediente_id,
)
from storage.schemas.storage import (
    FileResponse,
    PurgaDisposicionRequest,
    PurgaHuerfanosRequest,
)
from storage.services.aip_service import (
    AipAnteriorFixityMismatchError,
    AipNoEncontradoError,
    AipService,
    AipYaExisteError,
    AipYaSustituidoError,
    IndiceNoEsRenovacionError,
)
from storage.services.legal_hold_service import (
    HoldActorRequeridoError,
    HoldMotivoNoCoincideError,
    HoldNoEncontradoError,
    HoldYaLevantadoError,
    LegalHoldService,
)
from storage.services.pdfa_ingest_service import PdfaNonConformantError
from storage.services.preservation_worm_service import (
    FileNotFoundForPreservationError,
    FixityMismatchError,
    InvalidRetentionError,
    PreservationDisabledError,
    PreservationWormService,
    RetencionDiferidaSinHoldError,
    WormObjectNotFoundError,
)
from storage.services.render.factory import get_document_renderer
from storage.services.storage_service import (
    PurgaHuerfano,
    StorageService,
    UnsupportedFormatError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "RETENCION_DIFERIDA",
    "AipPackagingOutcome",
    "AipRepackagingOutcome",
    "ExportZipStream",
    "FileProvenance",
    "FileStream",
    "FileUnavailable",
    "PurgaHuerfano",
    "RenderFailedError",
    "RenderUnavailableError",
    "RenderedDocument",
    "StorageError",
    "StorageExportError",
    "StorageLegalHoldError",
    "StoragePurgeError",
    "StoredFile",
    "UploadedFile",
    "WormProtectionOutcome",
    "WormRenewalOutcome",
]

_CHUNK_SIZE = 65536

RETENCION_DIFERIDA = "valoracion_pendiente"


@asynccontextmanager
async def _tenant_conn() -> AsyncIterator[asyncpg.Connection]:
    """Conexión del pool del proceso. Los repositorios fijan el `search_path` del
    tenant en cada método; aquí solo se devuelve neutro, como hacía `get_db_conn`."""
    async with get_pool().acquire() as conn:
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")


# ── Errores y tipos de valor ─────────────────────────────────────────────────


class StorageError(Exception):
    """El almacén no pudo completar la operación pedida."""


class StoragePurgeError(StorageError):
    """El borrado físico no se completó: los bytes SIGUEN EXISTIENDO.

    Nunca se degrada a un aviso: quien orquesta la eliminación aborta el expediente
    entero y no escribe su lápida (una lápida sobre bytes vivos es peor que no
    eliminar). Un candado WORM o una constancia que cita el anexo llegan aquí."""


class StorageExportError(StorageError):
    """No se pudo ensamblar el ZIP de exportación. `status_code` dice por qué
    (413 tope, 507 disco, 409 fixity, 404 anexo); `None` si fue otro fallo."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class StorageLegalHoldError(StorageError):
    """El acto de retención legal no se aplicó. `status` es el código que archive
    traduce y `code` el motivo (`motivo_no_coincide`, `hold_ya_levantado`, ...).
    503 = preservación apagada o fallo del almacén."""

    def __init__(self, status: int, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class RenderUnavailableError(Exception):
    """Este despliegue no tiene renderizador (`RENDER_ENGINE`).

    Separado de `RenderFailedError` a propósito: configurar el motor y mirar por qué
    un cuerpo concreto no se deja pintar son dos arreglos distintos."""


class RenderFailedError(Exception):
    """El renderizador existe y no pudo producir ESTE documento."""


@dataclass(frozen=True)
class UploadedFile:
    """El fichero guardado: su id, la huella que calculó el almacén y su tamaño."""

    file_id: uuid.UUID
    sha256: str | None = None
    file_size: int | None = None

    @classmethod
    def _from_response(cls, response: FileResponse) -> UploadedFile:
        return cls(file_id=response.id, sha256=response.sha256, file_size=response.file_size)


@dataclass(frozen=True, slots=True)
class FileProvenance:
    """Autor, huella y referencias de un fichero (B2, ADR-031)."""

    sha256: str | None
    uploaded_by: uuid.UUID | None
    referencias: list[str]


@dataclass(frozen=True)
class RenderedDocument:
    """El documento generado. `formato` y `herramienta` van a la traza de
    procedencia: saber con qué se produjo es parte de sostener que es el que se
    produjo."""

    contenido: bytes
    formato: str
    herramienta: str


@dataclass(frozen=True)
class StoredFile:
    """Metadatos y bytes de un objeto, ya cotejados con su huella."""

    id: uuid.UUID
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    contenido: bytes


@dataclass(frozen=True)
class FileUnavailable:
    """No hay archivo que entregar: `file_not_found`, `too_large`,
    `content_mismatch` o `storage`."""

    motivo: str


class FileStream:
    """Los bytes de un fichero por trozos, sin acumularlos en memoria.

    Quien lo abre lo cierra con `aclose()`; `aiter_bytes()` lo cierra solo al
    terminar o si el consumidor se va a mitad."""

    def __init__(self, response: Any, content_type: str | None) -> None:
        self._response = response
        self.content_type = content_type

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._response.content.iter_chunked(_CHUNK_SIZE):
                yield chunk
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        close = getattr(self._response, "close", None)
        if close is not None:
            close()


class ExportZipStream:
    """ZIP de exportación ya cerrado en disco, listo para servirse por trozos.

    Cuando existe la instancia el paquete está completo y `content_length` es el
    real. El temporal se borra al terminar de leerlo o con `aclose()`."""

    def __init__(self, path: str, size: int) -> None:
        self._path = path
        self.content_length: int | None = size

    async def aclose(self) -> None:
        from storage.services.export_service import remove_quietly

        remove_quietly(self._path)

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        try:
            with open(self._path, "rb") as fh:
                while chunk := await asyncio.to_thread(fh.read, 1024 * 1024):
                    yield chunk
        finally:
            await self.aclose()


# ── Subidas ──────────────────────────────────────────────────────────────────


async def _upload(
    *,
    tenant_slug: str,
    filename: str,
    mime_type: str | None,
    data: bytes,
    uploaded_by: uuid.UUID | None,
    formatos_admitidos: frozenset[str] | None = None,
) -> UploadedFile:
    async with _tenant_conn() as conn:
        try:
            response, _created, _schedule = await StorageService(conn, tenant_slug).upload(
                filename=filename,
                mime_type=mime_type,
                content=data,
                uploaded_by=uploaded_by,
                formatos_admitidos=formatos_admitidos,
            )
        except (UnsupportedFormatError, PdfaNonConformantError) as exc:
            raise StorageError(f"no se guardó '{filename}': {exc}") from exc
    return UploadedFile._from_response(response)


async def upload_file(
    *,
    tenant_slug: str,
    filename: str,
    mime_type: str | None,
    data: bytes,
    uploaded_by: uuid.UUID | None = None,
) -> UploadedFile:
    """Guarda un anexo con la lista de formatos de anexos (RF-DIG-04)."""
    return await _upload(
        tenant_slug=tenant_slug,
        filename=filename,
        mime_type=mime_type,
        data=data,
        uploaded_by=uploaded_by,
    )


async def upload_public_annex(
    *, tenant_slug: str, filename: str, mime_type: str | None, data: bytes
) -> UploadedFile:
    """Anexo de la VENTANILLA PÚBLICA: lista de anexos, tope propio y sin autor.

    Quien sube aquí no está identificado, de ahí el tope muy por debajo del
    general. No se inventa un `uploaded_by`: atribuirlo a alguien falsearía la
    traza."""
    if len(data) > settings.max_public_upload_bytes:
        raise StorageError(
            f"El archivo ({len(data)} bytes) supera el máximo admitido en la "
            f"ventanilla pública ({settings.max_public_upload_bytes} bytes)."
        )
    return await _upload(
        tenant_slug=tenant_slug,
        filename=filename or "anexo",
        mime_type=mime_type,
        data=data,
        uploaded_by=None,
        formatos_admitidos=settings.allowed_mime_set,
    )


async def upload_system_artifact(
    *,
    tenant_slug: str,
    filename: str,
    mime_type: str | None,
    data: bytes,
    uploaded_by: uuid.UUID | None = None,
) -> UploadedFile:
    """Artefacto que produce el SISTEMA (índice firmado, `.eml` original...).

    Usa la lista de formatos del sistema, no la de anexos: ensanchar la de anexos
    dejaría subir XML o correos completos por la puerta de `PERM_RADI`."""
    if len(data) > settings.max_upload_bytes:
        raise StorageError(
            f"El archivo ({len(data)} bytes) supera el máximo admitido "
            f"({settings.max_upload_bytes} bytes)."
        )
    return await _upload(
        tenant_slug=tenant_slug,
        filename=filename,
        mime_type=mime_type,
        data=data,
        uploaded_by=uploaded_by,
        formatos_admitidos=settings.allowed_system_mime_set,
    )


async def upload_signed_xml(
    *,
    tenant_slug: str,
    filename: str,
    content: bytes,
    uploaded_by: uuid.UUID | None = None,
) -> str:
    """Guarda un XML firmado como artefacto de sistema y devuelve su clave
    `{file_id}/{filename}`, el mismo esquema que `StorageService.upload`.

    El nombre de un XML firmado es determinista, así que la deduplicación por
    SHA-256 solo junta re-subidas del mismo contenido con el mismo nombre y la
    clave reconstruida sigue siendo la real."""
    archivo = await upload_system_artifact(
        tenant_slug=tenant_slug,
        filename=filename,
        mime_type="application/xml",
        data=content,
        uploaded_by=uploaded_by,
    )
    return f"{archivo.file_id}/{filename}"


# ── Lecturas ─────────────────────────────────────────────────────────────────


def _file_id_of(object_key: str) -> uuid.UUID:
    """`{file_id}/{filename}` -> `file_id`."""
    return uuid.UUID(object_key.split("/", 1)[0])


async def _read_bytes(tenant_slug: str, file_id: uuid.UUID) -> bytes | None:
    async with _tenant_conn() as conn:
        record = await FileRepository(conn, tenant_slug).get_by_id(file_id)
    if record is None:
        return None
    respuesta = await get_minio().get_object(record.bucket, record.object_key)
    try:
        return await respuesta.read()
    finally:
        close = getattr(respuesta, "close", None)
        if close:
            close()


async def read_object(*, tenant_slug: str, object_key: str) -> bytes:
    """Los bytes de `object_key` tal cual salen del almacén. Lanza `StorageError`
    si no existe o no se pudo leer."""
    try:
        data = await _read_bytes(tenant_slug, _file_id_of(object_key))
    except Exception as exc:
        raise StorageError(f"no se pudo leer {object_key}: {exc}") from exc
    if data is None:
        raise StorageError(f"no existe {object_key}")
    return data


async def read_signed_bytes(*, tenant_slug: str, object_key: str) -> bytes | None:
    """Best-effort: los bytes sin decodificar (para recotejar una huella) o `None`."""
    try:
        return await read_object(tenant_slug=tenant_slug, object_key=object_key)
    except StorageError as exc:
        logger.warning("No se pudo leer un componente firmado: %s", exc)
        return None


async def read_signed_xml(*, tenant_slug: str, object_key: str) -> str | None:
    """Best-effort: el XML firmado como texto, o `None`. Quien llama no declara
    conforme una firma que no pudo verificar (RF-FIR-15)."""
    data = await read_signed_bytes(tenant_slug=tenant_slug, object_key=object_key)
    return None if data is None else data.decode("utf-8", errors="replace")


async def open_file_stream(*, tenant_slug: str, file_id: uuid.UUID) -> FileStream | None:
    """Stream de los bytes de `file_id`; `None` si no existe.

    La conexión se suelta antes de servir el primer byte: solo hace falta para la
    fila. Lanza `StorageError` si el objeto no se pudo abrir."""
    try:
        async with _tenant_conn() as conn:
            record = await FileRepository(conn, tenant_slug).get_by_id(file_id)
        if record is None:
            return None
        response = await get_minio().get_object(record.bucket, record.object_key)
    except Exception as exc:
        raise StorageError(f"no se pudo abrir el contenido de {file_id}: {exc}") from exc
    return FileStream(response, record.mime_type or "application/octet-stream")


async def read_file(
    *, tenant_slug: str, file_id: uuid.UUID, max_bytes: int
) -> StoredFile | FileUnavailable:
    """Metadatos y bytes de un fichero si caben en `max_bytes` y casan con su huella."""
    try:
        async with _tenant_conn() as conn:
            meta = await StorageService(conn, tenant_slug).get_metadata(file_id)
        if meta is None:
            return FileUnavailable("file_not_found")
        if meta.file_size > max_bytes:
            return FileUnavailable("too_large")
        contenido = await _read_bytes(tenant_slug, file_id)
    except Exception:
        logger.warning("No se pudo leer el archivo %s", file_id, exc_info=True)
        return FileUnavailable("storage")
    if contenido is None:
        return FileUnavailable("file_not_found")
    if len(contenido) > max_bytes:
        return FileUnavailable("too_large")
    huella = (meta.sha256 or "").strip().lower()
    if hashlib.sha256(contenido).hexdigest() != huella:
        return FileUnavailable("content_mismatch")
    return StoredFile(
        id=file_id,
        filename=(meta.filename or "documento").strip(),
        media_type=(meta.mime_type or "application/octet-stream").strip(),
        size_bytes=len(contenido),
        sha256=huella,
        contenido=contenido,
    )


async def get_file_hashes(*, tenant_slug: str, file_ids: list[str]) -> dict[str, str]:
    """SHA-256 de un lote de ficheros, por id. Best-effort: `{}` ante cualquier fallo.

    Vincular un documento a su expediente no puede quedar preso de esta lectura, y
    un documento sin huella el índice ya sabe declararlo (`sin_huella_declarada`)."""
    if not file_ids:
        return {}
    try:
        ids = [uuid.UUID(str(f)) for f in file_ids]
        async with _tenant_conn() as conn:
            return await FileRepository(conn, tenant_slug).hashes_for_ids(ids)
    except Exception:
        logger.warning(
            "No se obtuvieron las huellas de %d anexos; quedan sin huella declarada",
            len(file_ids),
            exc_info=True,
        )
        return {}


async def get_file_sha256(*, tenant_slug: str, file_id: uuid.UUID) -> str | None:
    """SHA-256 que el almacén calculó al subir `file_id`; `None` si no lo conoce.

    No se degrada: firmar sin saber QUÉ se firma es peor que no firmar, así que un
    fallo lanza `StorageError`."""
    try:
        async with _tenant_conn() as conn:
            hashes = await FileRepository(conn, tenant_slug).hashes_for_ids([file_id])
    except Exception as exc:
        raise StorageError(f"no se pudo leer la huella de {file_id}: {exc}") from exc
    return hashes.get(str(file_id))


async def get_file_provenance(
    *, tenant_slug: str, file_id: uuid.UUID
) -> FileProvenance | None:
    """Quién subió `file_id`, su huella y quién lo referencia; `None` si no existe.

    Un fallo lanza `StorageError`: asentar un acuse sin cotejar de quién es sería
    registrar una prueba que no lo es."""
    try:
        async with _tenant_conn() as conn:
            datos = await FileRepository(conn, tenant_slug).procedencia(file_id)
    except Exception as exc:
        raise StorageError(f"no se pudo leer la procedencia de {file_id}: {exc}") from exc
    if datos is None:
        return None
    autor = datos.get("uploaded_by")
    return FileProvenance(
        sha256=datos.get("sha256"),  # type: ignore[arg-type]
        uploaded_by=uuid.UUID(str(autor)) if autor else None,
        referencias=[str(r) for r in datos.get("referencias") or []],  # type: ignore[attr-defined]
    )


# ── Borrados ─────────────────────────────────────────────────────────────────


async def purge_for_disposition(
    *,
    tenant_slug: str,
    file_ids: list[uuid.UUID],
    expediente_id: str,
    motivo: str,
    actor: uuid.UUID | None,
) -> list[uuid.UUID]:
    """Borra físicamente objetos y filas por disposición final (RF-TRD-07 fase 5).

    Devuelve los realmente purgados; los no encontrados se dan por ausentes (un
    reintento tiene que converger). Por archivo y no en bloque, pero un candado WORM
    o una constancia que cita un anexo abortan la purga ENTERA: soltar el resto
    dejaría un expediente medio destruido. Lanza `StoragePurgeError`."""
    if not file_ids:
        return []
    try:
        payload = PurgaDisposicionRequest(
            file_ids=file_ids, expediente_id=expediente_id, motivo=motivo, actor=actor
        )
    except ValidationError as exc:
        raise StoragePurgeError(f"petición de purga inválida: {exc}") from exc
    purgados: list[uuid.UUID] = []
    try:
        async with _tenant_conn() as conn:
            service = StorageService(conn, tenant_slug)
            await service.verificar_sin_citas(list(payload.file_ids))
            for file_id in payload.file_ids:
                ok = await service.purge_for_disposition(
                    file_id, payload.expediente_id, payload.motivo, actor=payload.actor
                )
                if ok:
                    purgados.append(file_id)
    except StoragePurgeError:
        raise
    except Exception as exc:  # candado WORM, constancia, fallo del almacén
        raise StoragePurgeError(f"purga no completada: {exc}") from exc
    return purgados


async def purge_orphans(
    *,
    tenant_slug: str,
    file_ids: list[uuid.UUID],
    referencia: str,
    motivo: str,
    subido_desde: datetime,
    actor: uuid.UUID | None = None,
) -> list[PurgaHuerfano]:
    """Borrado SEGURO de binarios que nada referencia (AREA-09-14).

    Un archivo a la vez: el fallo de uno no frena a los demás. Solo `error` se
    reintenta; `referenciado`, `protegido_worm` y `preexistente` son decisiones
    correctas y `no_encontrado` es el estado buscado. Lanza `StorageError` si la
    petición entera no se pudo atender: nunca se da por purgado lo no confirmado."""
    try:
        payload = PurgaHuerfanosRequest(
            file_ids=file_ids,
            referencia=referencia,
            motivo=motivo,
            subido_desde=subido_desde,
            actor=actor,
        )
        resultados: list[PurgaHuerfano] = []
        async with _tenant_conn() as conn:
            service = StorageService(conn, tenant_slug)
            for file_id in dict.fromkeys(payload.file_ids):  # sin duplicados, en orden
                resultados.append(
                    await service.purge_orphan(
                        file_id,
                        referencia=payload.referencia,
                        motivo=payload.motivo,
                        actor=payload.actor,
                        subido_desde=payload.subido_desde,
                    )
                )
    except Exception as exc:
        raise StorageError(f"purga de huérfanos no completada: {exc}") from exc
    return resultados


# ── Render del documento de salida (E23, RF-PRO-03) ─────────────────────────


async def render_document(
    *, cuerpo_html: str, pagina: dict[str, Any], perfil: str | None = None
) -> RenderedDocument:
    """PDF/A del cuerpo ya combinado y saneado. El almacén pinta; no decide qué dice.

    Sin renderizador no se degrada a «radicar sin documento»: un oficio que la
    entidad cree haber producido y no existe es peor que un error visible."""
    renderer = get_document_renderer(settings)
    if renderer is None:
        raise RenderUnavailableError(
            "Este despliegue no tiene renderizador de documentos configurado (RENDER_ENGINE)"
        )
    if not cuerpo_html:
        raise RenderFailedError("cuerpo vacío")
    try:
        resultado = await renderer.render(
            cuerpo_html, pagina=pagina or {}, perfil=perfil or settings.render_pdfa_profile
        )
    except Exception as exc:
        raise RenderFailedError(f"el renderizador falló: {exc}") from exc
    if resultado.outcome != "generado" or not resultado.contenido:
        raise RenderFailedError(str(resultado.detalle.get("motivo", "no se pudo generar")))
    return RenderedDocument(
        contenido=resultado.contenido,
        formato=str(resultado.formato or ""),
        herramienta=str(resultado.herramienta or ""),
    )


# ── Protección WORM (E10) ────────────────────────────────────────────────────


def _retention_fields(
    retencion_anios: int | None,
    fecha_inicio: date | None,
    retencion_diferida: str | None,
) -> dict[str, Any]:
    """Las DOS fuentes exclusivas de la retención (ADR-043): calendario
    (`retencion_anios`+`fecha_inicio`) o `retencion_diferida`. Mezclarlas o no dar
    ninguna es un error de programación del llamante y se lanza `ValueError`, en vez
    de confundirse con un fallo transitorio."""
    if retencion_diferida is not None:
        if retencion_anios is not None or fecha_inicio is not None:
            raise ValueError("retencion_diferida es excluyente con retencion_anios/fecha_inicio")
        return {"retencion_diferida": retencion_diferida}
    if retencion_anios is None or fecha_inicio is None:
        raise ValueError("falta retencion_anios+fecha_inicio o retencion_diferida")
    return {"retencion_anios": retencion_anios, "fecha_inicio": fecha_inicio}


@dataclass(slots=True)
class WormProtectionOutcome:
    """Resultado de un intento de protección WORM: exactamente uno de `ok`
    (con `worm_object_id`, incluida la respuesta idempotente), `disabled`
    (preservación apagada) o `transient` (cualquier otro fallo, reintentable)."""

    ok: bool
    worm_object_id: uuid.UUID | None = None
    retain_until: datetime | None = None
    disabled: bool = False
    transient: bool = False

    @property
    def protected_object_id(self) -> uuid.UUID:
        """El objeto WORM confirmado. Falla en voz alta si `ok=True` no lo trae, en
        vez de dejar el espejo de protección marcado con `NULL`."""
        if self.worm_object_id is None:
            raise ValueError("WormProtectionOutcome(ok=True) sin worm_object_id")
        return self.worm_object_id


async def _protect(
    tenant_slug: str,
    body: dict[str, Any],
    *,
    actor: str | None,
    force_index: bool,
    contexto: str,
) -> WormProtectionOutcome:
    try:
        payload = ProtegerIndiceRequest(**body)
        if force_index:
            # Alias fijo del índice: el tipo no lo decide el llamante.
            payload = payload.model_copy(update={"tipo": "indice"})
        async with _tenant_conn() as conn:
            response, _created = await PreservationWormService(
                conn, tenant_slug
            ).proteger_indice(payload, actor=actor)
    except PreservationDisabledError:
        logger.warning("Protección WORM deshabilitada (PRESERVATION_ENABLED=false, %s)", contexto)
        return WormProtectionOutcome(ok=False, disabled=True)
    except Exception:
        logger.warning("Protección WORM no completada (%s, transient)", contexto, exc_info=True)
        return WormProtectionOutcome(ok=False, transient=True)
    return WormProtectionOutcome(
        ok=True, worm_object_id=response.id, retain_until=response.retain_until
    )


async def protect_index(
    *,
    tenant_slug: str,
    actor_id: str,
    file_id: str,
    expediente_id: str,
    retencion_anios: int | None = None,
    fecha_inicio: date | None = None,
    retencion_diferida: str | None = None,
) -> WormProtectionOutcome:
    """Protege el índice firmado, atribuido a la persona que cerró el expediente."""
    body = {
        "file_id": file_id,
        "expediente_id": expediente_id,
        **_retention_fields(retencion_anios, fecha_inicio, retencion_diferida),
    }
    return await _protect(tenant_slug, body, actor=str(actor_id), force_index=True, contexto="close")


async def protect_index_as_system(
    *,
    tenant_slug: str,
    file_id: str,
    expediente_id: str,
    retencion_anios: int | None = None,
    fecha_inicio: date | None = None,
    retencion_diferida: str | None = None,
) -> WormProtectionOutcome:
    """Igual que `protect_index`, como acto de sistema (reconciliación)."""
    body = {
        "file_id": file_id,
        "expediente_id": expediente_id,
        **_retention_fields(retencion_anios, fecha_inicio, retencion_diferida),
    }
    return await _protect(
        tenant_slug,
        body, actor=str(SYSTEM_RECONCILER_ID), force_index=True, contexto="reconciliation"
    )


async def protect_artifact(
    *,
    tenant_slug: str,
    actor_id: str,
    file_id: str,
    expediente_id: str,
    tipo: str,
    retencion_anios: int,
    fecha_inicio: date,
    # De QUÉ es `expediente_id`: el acta de eliminación pertenece a un LOTE.
    objeto_tipo: str = "expediente",
) -> WormProtectionOutcome:
    """Protege un artefacto firmado de `tipo` dado, atribuido a una persona."""
    body = {
        "file_id": file_id,
        "expediente_id": expediente_id,
        "tipo": tipo,
        "retencion_anios": retencion_anios,
        "fecha_inicio": fecha_inicio,
        "objeto_tipo": objeto_tipo,
    }
    return await _protect(tenant_slug, body, actor=str(actor_id), force_index=False, contexto=f"close:{tipo}")


async def protect_artifact_as_system(
    *,
    tenant_slug: str,
    file_id: str,
    expediente_id: str,
    tipo: str,
    retencion_anios: int,
    fecha_inicio: date,
    objeto_tipo: str = "expediente",
) -> WormProtectionOutcome:
    """Igual que `protect_artifact`, como acto de sistema (reconciliación)."""
    body = {
        "file_id": file_id,
        "expediente_id": expediente_id,
        "tipo": tipo,
        "retencion_anios": retencion_anios,
        "fecha_inicio": fecha_inicio,
        "objeto_tipo": objeto_tipo,
    }
    return await _protect(
        tenant_slug,
        body,
        actor=str(SYSTEM_RECONCILER_ID),
        force_index=False,
        contexto=f"reconciliation:{tipo}",
    )


@dataclass(slots=True)
class WormRenewalOutcome:
    """Resultado de una renovación de retención WORM: `ok` (`renovado` distingue
    extensión real de no-op; `retain_until` es el vigente), `not_found` (nada
    protegido que renovar), `disabled` o `transient`."""

    ok: bool
    retain_until: datetime | None = None
    renovado: bool = False
    not_found: bool = False
    disabled: bool = False
    transient: bool = False


async def renew_retention(
    *,
    tenant_slug: str,
    expediente_id: str,
    tipo: str,
    retencion_anios: int,
    fecha_inicio: date,
    file_id: str | None = None,
) -> WormRenewalOutcome:
    """Extiende (nunca acorta) la retención de un artefacto ya protegido de una serie
    de Conservación Total. Acto de sistema: solo archive, dueño de la TRD, decide qué
    es CT y cuándo renovar. `file_id` identifica el objeto en los tipos con varios
    artefactos por expediente; sin él se renovaría solo el más reciente."""
    contexto = f"renovacion:{tipo}"
    body: dict[str, Any] = {
        "expediente_id": expediente_id,
        "tipo": tipo,
        "retencion_anios": retencion_anios,
        "fecha_inicio": fecha_inicio,
    }
    if file_id is not None:
        body["file_id"] = file_id
    try:
        payload = RenovarRetencionRequest(**body)
        async with _tenant_conn() as conn:
            response = await PreservationWormService(conn, tenant_slug).renovar_retencion(
                payload, actor=str(SYSTEM_RECONCILER_ID)
            )
    except WormObjectNotFoundError:
        logger.warning("Sin objeto WORM protegido para renovar (%s)", contexto)
        return WormRenewalOutcome(ok=False, not_found=True)
    except PreservationDisabledError:
        logger.warning("Protección WORM deshabilitada al renovar (%s)", contexto)
        return WormRenewalOutcome(ok=False, disabled=True)
    except Exception:
        logger.warning("Renovación WORM no completada (%s, transient)", contexto, exc_info=True)
        return WormRenewalOutcome(ok=False, transient=True)
    return WormRenewalOutcome(
        ok=True, retain_until=response.retain_until, renovado=bool(response.renovado)
    )


async def renew_artifact(
    *,
    tenant_slug: str,
    file_id_anterior: uuid.UUID,
    file_id_nuevo: uuid.UUID,
    sha256_nuevo: str,
    firma_id: uuid.UUID,
    ordinal: int,
) -> dict[str, Any]:
    """Registra la renovación del sello de archivo de una firma XAdES-LTA (B10 §8):
    el XML nuevo hereda la política WORM del anterior y queda el evento PREMIS
    `RENOVACION`. Idempotente por `firma_id` + `ordinal`. Lanza `StorageError`."""
    try:
        payload = RenovarArtefactoRequest(
            file_id_anterior=file_id_anterior,
            file_id_nuevo=file_id_nuevo,
            sha256_nuevo=sha256_nuevo,
            firma_id=firma_id,
            ordinal=ordinal,
        )
        async with _tenant_conn() as conn:
            response = await PreservationWormService(conn, tenant_slug).renovar_artefacto(
                payload, actor=str(SYSTEM_RECONCILER_ID)
            )
    except Exception as exc:
        raise StorageError(f"no se registró la renovación del artefacto: {exc}") from exc
    return response.model_dump(mode="json")


# ── Retención legal (ADR-043) ────────────────────────────────────────────────


def _hold_error(exc: Exception) -> StorageLegalHoldError:
    """El status y el código que archive ya sabía traducir."""
    if isinstance(exc, StorageLegalHoldError):
        return exc
    if isinstance(exc, ValidationError):
        return StorageLegalHoldError(422, f"petición inválida: {exc}")
    if isinstance(exc, PreservationDisabledError):
        return StorageLegalHoldError(503, "la protección WORM está desactivada", "preservation_disabled")
    mapa: list[tuple[type[Exception], int, str]] = [
        (HoldNoEncontradoError, 404, "hold_not_found"),
        (HoldMotivoNoCoincideError, 409, "motivo_no_coincide"),
        (HoldYaLevantadoError, 409, "hold_ya_levantado"),
        (HoldActorRequeridoError, 422, "actor_requerido"),
        (InvalidRetentionError, 503, "invalid_retention"),
    ]
    for tipo, status, code in mapa:
        if isinstance(exc, tipo):
            return StorageLegalHoldError(status, str(exc), code)
    return StorageLegalHoldError(503, f"fallo del almacén: {exc}")


def _hold_actor(actor_id: str | None) -> tuple[str, uuid.UUID | None]:
    """Con persona, el acto es suyo; sin ella, de SISTEMA y sin `fijado_por`."""
    if not actor_id:
        return str(SYSTEM_RECONCILER_ID), None
    try:
        return str(actor_id), uuid.UUID(str(actor_id))
    except ValueError:
        return str(actor_id), None


async def set_legal_hold(
    *,
    tenant_slug: str,
    actor_id: str | None,
    expediente_id: str,
    motivo_tipo: str,
    fundamento: str,
    acta_ref: str,
) -> dict[str, Any]:
    """Fija UNA retención legal por motivo. No exige objetos WORM: los que nazcan
    después la heredan. Idempotente para `valoracion_pendiente`. Devuelve
    `{hold_id, creado, objetos_afectados}`. No es best-effort: un freeze que no se
    aplicó no puede darse por declarado (`StorageLegalHoldError`)."""
    actor, actor_uuid = _hold_actor(actor_id)
    try:
        payload = HoldFijarRequest(
            expediente_id=expediente_id,
            motivo_tipo=motivo_tipo,
            fundamento=fundamento,
            acta_ref=acta_ref,
        )
        async with _tenant_conn() as conn:
            res = await LegalHoldService(conn, tenant_slug).fijar(
                payload, actor=actor, actor_id=actor_uuid
            )
    except Exception as exc:
        raise _hold_error(exc) from exc
    return res.model_dump(mode="json")


async def lift_legal_hold(
    *,
    tenant_slug: str,
    actor_id: str | None,
    hold_id: uuid.UUID | str,
    motivo_tipo_esperado: str,
    fundamento: str,
    acta_ref: str,
) -> dict[str, Any]:
    """Levanta UN hold confirmando su motivo. El almacén se libera solo si no queda
    otro hold activo del expediente. Devuelve `{hold_id, objetos_liberados_s3,
    holds_restantes}`."""
    actor, actor_uuid = _hold_actor(actor_id)
    try:
        payload = HoldLevantarRequest(
            motivo_tipo_esperado=motivo_tipo_esperado,
            fundamento=fundamento,
            acta_ref=acta_ref,
        )
        async with _tenant_conn() as conn:
            res = await LegalHoldService(conn, tenant_slug).levantar(
                uuid.UUID(str(hold_id)), payload, actor=actor, actor_id=actor_uuid
            )
    except Exception as exc:
        raise _hold_error(exc) from exc
    return res.model_dump(mode="json")


async def list_legal_holds(*, tenant_slug: str, expediente_id: str) -> list[dict[str, Any]]:
    """Todos los holds del expediente (activos y levantados), más recientes primero.
    El no-read-up lo aplica archive antes de llamar: devuelve el fundamento."""
    try:
        if not expediente_id:
            raise StorageLegalHoldError(422, "expediente_id vacío")
        async with _tenant_conn() as conn:
            items = await LegalHoldService(conn, tenant_slug).listar(
                canonicalize_expediente_id(expediente_id)
            )
    except Exception as exc:
        raise _hold_error(exc) from exc
    return [h.model_dump(mode="json") for h in items]


async def set_deferred_retention(
    *,
    tenant_slug: str,
    expediente_id: str,
    retencion_anios: int,
    fecha_inicio: date,
) -> dict[str, Any]:
    """Fija COMPLIANCE por PRIMERA vez en los objetos `valoracion_pendiente` del
    expediente (valoración CT). Nunca toca filas con calendario. Idempotente."""
    try:
        payload = FijarRetencionRequest(
            expediente_id=expediente_id,
            retencion_anios=retencion_anios,
            fecha_inicio=fecha_inicio,
        )
        async with _tenant_conn() as conn:
            res = await PreservationWormService(conn, tenant_slug).fijar_retencion_diferida(
                payload, actor=str(SYSTEM_RECONCILER_ID)
            )
    except Exception as exc:
        raise _hold_error(exc) from exc
    return res.model_dump(mode="json")


# ── AIP (E10 Increment B, C-6) ───────────────────────────────────────────────


@dataclass(slots=True)
class AipPackagingOutcome:
    """Resultado de un empaquetado del AIP: `ok` (con `aip_id`), `ya_existe` (el
    almacén ya tiene un AIP del expediente; `aip_id` es el vigente y quien llama
    reconcilia), `fixity_mismatch` (un anexo ya no casa con su huella de subida),
    `disabled` o `transient`."""

    ok: bool
    aip_id: uuid.UUID | None = None
    retain_until: datetime | None = None
    disabled: bool = False
    fixity_mismatch: bool = False
    transient: bool = False
    ya_existe: bool = False


async def package_aip(
    *,
    tenant_slug: str,
    expediente_id: str,
    documentos: list[dict],
    indice_file_id: uuid.UUID | None,
    retencion_anios: int | None = None,
    fecha_inicio: date | None = None,
    retencion_diferida: str | None = None,
    pdfa_profile: str | None = None,
) -> AipPackagingOutcome:
    """Empaqueta el AIP (BagIt + PREMIS v3) y lo sube al bucket WORM, como acto de
    sistema. Caro (descarga y re-hashea cada anexo): solo lo dispara la
    reconciliación. `pdfa_profile=None` cae al perfil global."""
    body: dict[str, Any] = {
        "expediente_id": expediente_id,
        "documentos": documentos,
        **_retention_fields(retencion_anios, fecha_inicio, retencion_diferida),
    }
    if indice_file_id is not None:
        body["indice_file_id"] = indice_file_id
    if pdfa_profile is not None:
        body["pdfa_profile"] = pdfa_profile
    try:
        payload = AipCreate(**body)
        async with _tenant_conn() as conn:
            response = await AipService(conn, tenant_slug).empaquetar_aip(
                payload, actor=str(SYSTEM_RECONCILER_ID)
            )
    except AipYaExisteError as exc:
        logger.warning(
            "El AIP de %s ya existe (aip_id=%s); se reconcilia", expediente_id, exc.aip_id
        )
        return AipPackagingOutcome(ok=False, ya_existe=True, aip_id=exc.aip_id)
    except (FixityMismatchError, RetencionDiferidaSinHoldError):
        logger.warning(
            "fixity_mismatch al empaquetar el AIP de %s (un anexo mutó desde que se vinculó)",
            expediente_id,
        )
        return AipPackagingOutcome(ok=False, fixity_mismatch=True)
    except PreservationDisabledError:
        logger.warning("Empaquetado de AIP deshabilitado (expediente_id=%s)", expediente_id)
        return AipPackagingOutcome(ok=False, disabled=True)
    except Exception:
        logger.warning(
            "Empaquetado del AIP de %s no completado (transient)", expediente_id, exc_info=True
        )
        return AipPackagingOutcome(ok=False, transient=True)
    return AipPackagingOutcome(ok=True, aip_id=response.id, retain_until=response.retain_until)


@dataclass(slots=True)
class AipRepackagingOutcome:
    """Resultado de reempaquetar el AIP con el índice renovado (C-6, ADR-042)."""

    ok: bool
    aip_id: uuid.UUID | None = None
    version: int | None = None
    sustituye_a: uuid.UUID | None = None
    retain_until: datetime | None = None
    ya_sustituido: bool = False
    fixity_mismatch: bool = False
    indice_invalido: bool = False
    not_found: bool = False
    disabled: bool = False
    transient: bool = False


async def repackage_aip(
    *,
    tenant_slug: str,
    expediente_id: str,
    aip_anterior_id: uuid.UUID,
    indice_file_id: uuid.UUID,
    indice_xml_sha256: str,
    firma_id: str,
    ordinal: int,
) -> AipRepackagingOutcome:
    """AIP versión n+1 construido desde el bag anterior con el índice renovado.
    Idempotente por `indice_xml_sha256`: un reintento devuelve el mismo AIP."""
    try:
        payload = AipReempaquetarRequest(
            expediente_id=expediente_id,
            aip_anterior_id=aip_anterior_id,
            indice_file_id=indice_file_id,
            indice_xml_sha256=indice_xml_sha256,
            firma_id=firma_id,
            ordinal=ordinal,
        )
        async with _tenant_conn() as conn:
            resultado, _creado = await AipService(conn, tenant_slug).reempaquetar_aip(
                payload, actor=str(SYSTEM_RECONCILER_ID)
            )
    except (AipYaSustituidoError, asyncpg.UniqueViolationError):
        # Dos reempaquetados a la vez: el índice único decide y el segundo reintenta.
        return AipRepackagingOutcome(ok=False, ya_sustituido=True)
    except (
        AipAnteriorFixityMismatchError,
        FixityMismatchError,
        RetencionDiferidaSinHoldError,
    ):
        return AipRepackagingOutcome(ok=False, fixity_mismatch=True)
    except (IndiceNoEsRenovacionError, ValidationError):
        return AipRepackagingOutcome(ok=False, indice_invalido=True)
    except (AipNoEncontradoError, FileNotFoundForPreservationError):
        return AipRepackagingOutcome(ok=False, not_found=True)
    except PreservationDisabledError:
        return AipRepackagingOutcome(ok=False, disabled=True)
    except Exception:  # retención inválida, bucket sin Object Lock, PREMIS inválido...
        logger.warning("Reempaquetado del AIP de %s no completado", expediente_id, exc_info=True)
        return AipRepackagingOutcome(ok=False, transient=True)
    if resultado.sustituye_a is None:
        logger.warning("Reempaquetado del AIP de %s sin sustituye_a", expediente_id)
        return AipRepackagingOutcome(ok=False, transient=True)
    return AipRepackagingOutcome(
        ok=True,
        aip_id=resultado.id,
        version=int(resultado.version),
        sustituye_a=resultado.sustituye_a,
        retain_until=resultado.retain_until,
    )


async def get_aip(*, tenant_slug: str, expediente_id: str) -> dict[str, Any] | None:
    """El AIP vigente (cabeza de la cadena `sustituye_a`) o `None` si no hay.

    El no-read-up lo aplica archive, dueño de la clasificación del expediente, antes
    de llamar. Un fallo del almacén lanza `StorageError`: quien llama distingue «no
    hay AIP» de «no se pudo saber»."""
    from storage.schemas.preservacion import AipResponse

    try:
        async with _tenant_conn() as conn:
            row = await PreservacionRepository(conn, tenant_slug).get_aip_cabeza(expediente_id)
    except Exception as exc:
        raise StorageError(f"no se pudo leer el AIP de {expediente_id}: {exc}") from exc
    if row is None:
        return None
    return AipResponse(**dict(row)).model_dump(mode="json")


# ── Eventos PREMIS ───────────────────────────────────────────────────────────


def _event_response(row: Any) -> EventoResponse:
    import json

    d = dict(row)
    raw = d.get("detalle")
    d["detalle"] = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return EventoResponse(**d)


async def list_events(*, tenant_slug: str, object_refs: list[str]) -> list[dict[str, Any]]:
    """Eventos PREMIS de uno o varios `object_ref`, ordenados por `ts`.

    `[]` si no hay. Lanza `StorageError` ante un fallo: quien llama lo traduce a
    503, ortogonal a la autorización."""
    if not object_refs:
        return []
    try:
        async with _tenant_conn() as conn:
            rows = await PreservacionRepository(conn, tenant_slug).list_eventos(object_refs)
        eventos = [_event_response(r).model_dump(mode="json") for r in rows]
    except Exception as exc:
        raise StorageError(f"no se pudieron leer los eventos de preservación: {exc}") from exc
    vistos = {str(e.get("id")): e for e in eventos}
    return sorted(vistos.values(), key=lambda e: str(e.get("ts")))


async def record_event(
    *,
    tenant_slug: str,
    object_ref: str,
    tipo: str,
    resultado: str,
    detalle: dict,
    agente: str | None,
    dedup_key: str,
    hash_after: str | None = None,
) -> bool:
    """Registra un evento PREMIS sin persona detrás (AREA-07-05).

    Idempotente por `dedup_key`: si ya hay un evento del mismo objeto y tipo con esa
    clave no se duplica (la reconciliación reintenta y el registro es append-only).
    Best-effort: `False` ante cualquier fallo, nunca lanza."""
    try:
        payload = EventoInternalCreate(
            object_ref=object_ref,
            tipo=tipo,
            resultado=resultado,
            detalle=detalle,
            agente=agente,
            dedup_key=dedup_key,
            hash_after=hash_after,
        )
        datos = payload.model_dump(exclude={"agente", "dedup_key"})
        async with _tenant_conn() as conn:
            repo = PreservacionRepository(conn, tenant_slug)
            existente = await repo.find_evento_por_dedup(
                object_ref=payload.object_ref, tipo=payload.tipo, dedup_key=dedup_key
            )
            if existente is not None:
                return True
            datos["detalle"] = {**datos["detalle"], "dedup_key": dedup_key}
            await repo.insert_evento(datos, agente=payload.agente)
    except Exception:
        logger.warning("No se registró el evento PREMIS de %s", object_ref, exc_info=True)
        return False
    return True


# ── Exportación ──────────────────────────────────────────────────────────────


async def open_export_zip(
    *, tenant_slug: str, items: list[dict], extra_files: list[dict]
) -> ExportZipStream:
    """Ensambla el ZIP de exportación de un expediente a partir del inventario que
    archive ya filtró por clearance. No persiste nada.

    Todo fallo ocurre aquí, antes del primer byte: el ZIP se cierra en un temporal y
    solo entonces se devuelve (nunca un ZIP corto). Lanza `StorageExportError`."""
    from storage.services.export_service import (
        ExportDemasiadoGrandeError,
        ExportEspacioInsuficienteError,
        ExportFixityError,
        ExportService,
    )

    try:
        payload = ExportZipCreate(items=items, extra_files=extra_files)
        async with _tenant_conn() as conn:
            artifact = await ExportService(conn, tenant_slug).build_export_zip(payload)
    except FileNotFoundForPreservationError as exc:
        raise StorageExportError(str(exc), 404) from exc
    except ExportDemasiadoGrandeError as exc:
        raise StorageExportError(str(exc), 413) from exc
    except ExportFixityError as exc:
        raise StorageExportError(
            f"el anexo {exc.file_id} no coincide con su SHA-256 registrado", 409
        ) from exc
    except ExportEspacioInsuficienteError as exc:
        raise StorageExportError(str(exc), 507) from exc
    except Exception as exc:
        raise StorageExportError(f"no se pudo ensamblar el ZIP: {exc}") from exc
    return ExportZipStream(artifact.path, artifact.size)
