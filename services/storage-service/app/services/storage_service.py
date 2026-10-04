# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Storage service — business logic for file upload, retrieval, and deletion.

Responsibilities:
- SHA-256 deduplication per tenant before uploading to MinIO
- Bucket provisioning (create if not exists) per tenant
- Presigned URL generation for client-side downloads
- Soft-delete (logical deletion) — does NOT remove from MinIO
- Physical purge (TRD final disposition only) — removes from MinIO + DB row

Soft-delete vs. purge (D-04, constitución inv. 12):
  `delete()`               → marks deleted_at, audits the act. No MinIO call.
  `purge_for_disposition()` → removes the MinIO object THEN hard-deletes the row.
                              Only for TRD final-disposition workflows. Gated by
                              the WORM lock (E10 Increment A): blocked while the
                              expediente's protected índice is still under active
                              retention or legal hold — see `WormLockActiveError`.
"""

import hashlib
import io
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta

import asyncpg
from orpycamcp_common import audit

from app.core.config import settings
from app.core.minio_client import Minio, get_minio
from app.core.preservacion_tipos import CONTROL_INSTRUMENT_TIPOS
from app.models.file_record import FileRecord
from app.repositories.file_repository import FileRepository
from app.repositories.preservacion_worm_repository import PreservacionWormRepository
from app.schemas.preservacion import canonicalize_expediente_id
from app.schemas.storage import (
    FileResponse,
    IntegrityResponse,
    PresignedUrlResponse,
)
from app.services.pdfa_ingest_service import (
    PdfaNonConformantError,
    looks_like_pdf,
    record_ingest_pdfa_result,
    validate_ingest_content,
)

_BUCKET_PREFIX = "orpycamcp"
_SERVICE = "storage-service"


@dataclass(frozen=True)
class PurgaHuerfano:
    file_id: uuid.UUID
    # purgado | no_encontrado | preexistente | referenciado | protegido_worm | error
    resultado: str
    detalle: str | None = None


class WormLockActiveError(Exception):
    """Se intentó purgar un archivo de un expediente con protección WORM activa.

    Bloquea la disposición final mientras exista, para ESTE `expediente_id`, un
    objeto en `preservacion_worm_objeto` con `retain_until` futuro o
    `legal_hold = true` (E10 Increment A, AGN 001/2024 Art. 4.3.2.6). Retención
    WORM y disparo de disposición derivan de la MISMA TRD y normalmente expiran
    juntos — este gate es la red de seguridad contra la eliminación prematura,
    no el mecanismo primario que decide CUÁNDO disponer.
    """


class UnsupportedFormatError(Exception):
    """El MIME type del archivo no está en la lista de formatos admitidos."""


class NotCurrentVersionError(Exception):
    """Solo se puede reemplazar la versión vigente de un anexo."""


def _bucket_name(tenant_slug: str) -> str:
    return f"{_BUCKET_PREFIX}-{tenant_slug}-documents"


def _check_format(mime_type: str | None, allowed: frozenset[str] | None = None) -> None:
    """Valida el formato contra la lista que corresponda.

    `allowed` explícito = artefacto del sistema (lista propia, solo alcanzable
    desde el endpoint sellado con token interno). Sin él, la lista de anexos de
    usuario (RF-DIG-04). Son dos listas a propósito: ver `config.py`.
    """
    permitidos = settings.allowed_mime_set if allowed is None else allowed
    if "*" in permitidos:
        return
    if mime_type not in permitidos:
        raise UnsupportedFormatError(f"Formato no admitido: {mime_type or 'desconocido'}")


class StorageService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._repo = FileRepository(conn, tenant_slug)
        self._tenant_slug = tenant_slug
        self._bucket = _bucket_name(tenant_slug)

    async def upload(
        self,
        filename: str,
        mime_type: str | None,
        content: bytes,
        uploaded_by: uuid.UUID | None,
        *,
        formatos_admitidos: frozenset[str] | None = None,
    ) -> tuple[FileResponse, bool, bool]:
        """
        Upload a file, deduplicating by SHA-256 within the tenant.

        Returns (FileResponse, created, schedule_pdfa_validation):
        - `created=False` means the SAME AUTHOR had already uploaded that content and
          it was deduplicated — caller should respond with 200. Never across
          authors (AREA-08-09: it would leak existence and the original filename). Deduplicated files
          keep whatever PDF/A verdict (if any) the ORIGINAL upload produced;
          this call never re-validates them.
        - `schedule_pdfa_validation=True` tells the caller (router) to
          enqueue `app.services.pdfa_ingest_service.run_ingest_validation_background`
          as a `BackgroundTask` AFTER responding — only true for a NEW
          upload, gate `PRESERVATION_PDFA_VALIDATION_ENABLED` on, mode
          advisory, and content sniffed as PDF (E10 C2b follow-up, E07).

        Rechaza formatos no admitidos (RF-DIG-04) antes de tocar MinIO/BD.

        Validación PDF/A en ingesta (E10 Increment C2b follow-up, RF-PRE-02):
        subordinada al master gate `PRESERVATION_PDFA_VALIDATION_ENABLED`
        (False -> comportamiento IDÉNTICO al de antes de este follow-up,
        ningún archivo toca `app.services.pdfa`). Con el gate activo y
        contenido sniffed como PDF (magic bytes, nunca `mime_type`):
        - modo **strict** (`PRESERVATION_PDFA_INGEST_MODE=strict`): valida
          SÍNCRONO aquí mismo, ANTES de subir a MinIO/persistir la fila —
          `non_conformant` levanta `PdfaNonConformantError` (el router lo
          traduce a 422) y la subida NUNCA se persiste. Cualquier otro
          resultado (conformant/not_evaluated) se registra DESPUÉS de crear
          la fila (ya con `file_id` real).
        - modo **advisory** (default): NO valida aquí — marca la fila
          `pdfa_status='pendiente'` y devuelve `schedule_pdfa_validation=True`
          para que el router encole el worker asíncrono best-effort. Nunca
          bloquea la respuesta al cliente.
        """
        _check_format(mime_type, formatos_admitidos)
        sha256 = hashlib.sha256(content).hexdigest()

        # Dedup SOLO del mismo autor (AREA-08-09): contra otro autor la subida crea
        # su propia fila (201, filename y autor propios) y no revela si el
        # contenido ya existía. Sin autor identificado (ventanilla pública,
        # sistema) no se deduplica nunca. La comparación en Python repite la del
        # SQL a propósito: si alguien afloja la consulta, esto sigue cerrando.
        existing = (
            await self._repo.find_by_sha256(sha256, uploaded_by)
            if uploaded_by is not None
            else None
        )
        if existing is not None and existing.uploaded_by == uploaded_by:
            # La marca de dedup toma el bloqueo de fila: serializa con la purga de
            # huérfanos (ver `FileRepository.mark_dedup_hit`). `None` = la purga lo
            # borró entre la búsqueda y la marca: el contenido se sube de nuevo.
            reutilizado = await self._repo.mark_dedup_hit(existing.id)
            if reutilizado is not None:
                return _to_response(reutilizado), False, False

        is_pdf = looks_like_pdf(content)
        pdfa_gate_active = settings.preservation_pdfa_validation_enabled and is_pdf
        strict_mode = pdfa_gate_active and settings.preservation_pdfa_ingest_mode == "strict"

        strict_result = None
        if strict_mode:
            strict_result = await validate_ingest_content(content)
            if strict_result.outcome == "non_conformant":
                # Rechazo ANTES de tocar MinIO/BD (modo estricto, opt-in):
                # ningún objeto se persiste — se audita contra el SHA-256
                # (no hay `file_id` porque el archivo nunca se crea).
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="file.pdfa_rejected_ingest",
                    object_type="file",
                    object_ref=sha256,
                    actor=str(uploaded_by) if uploaded_by else None,
                    payload={
                        "filename": filename,
                        "profile": strict_result.profile,
                        "validator_agent": strict_result.validator_agent,
                        "detalle": strict_result.detail,
                    },
                    canal=audit.CANAL_PETICION,
                )
                raise PdfaNonConformantError(
                    f"El archivo {filename!r} no es PDF/A conforme "
                    f"(perfil {strict_result.profile}); rechazado por "
                    "PRESERVATION_PDFA_INGEST_MODE=strict"
                )

        minio = get_minio()
        await _ensure_bucket(minio, self._bucket)

        file_id = uuid.uuid4()
        object_key = f"{file_id}/{filename}"

        await minio.put_object(
            self._bucket,
            object_key,
            io.BytesIO(content),
            length=len(content),
            content_type=mime_type or "application/octet-stream",
        )

        record = await self._repo.create(
            file_id=file_id,
            filename=filename,
            mime_type=mime_type,
            file_size=len(content),
            sha256=sha256,
            bucket=self._bucket,
            object_key=object_key,
            uploaded_by=uploaded_by,
        )

        schedule_pdfa_validation = False
        if pdfa_gate_active:
            if strict_mode:
                assert strict_result is not None  # conformant/not_evaluated: ya se validó arriba
                await record_ingest_pdfa_result(
                    self._conn,
                    self._tenant_slug,
                    file_id,
                    strict_result,
                    agente=str(uploaded_by) if uploaded_by else "ingesta-http",
                )
            else:
                # Advisory (default): NO se valida aquí — solo se marca
                # "pendiente" para que el estado sea distinguible de "nunca
                # evaluado" mientras el worker best-effort corre.
                await self._repo.mark_pdfa_pending(file_id, profile=settings.pdfa_profile)
                schedule_pdfa_validation = True

        return _to_response(record), True, schedule_pdfa_validation

    async def replace(
        self,
        file_id: uuid.UUID,
        filename: str,
        mime_type: str | None,
        content: bytes,
        uploaded_by: uuid.UUID | None,
    ) -> tuple[FileResponse, bool] | None:
        """Reemplaza un anexo creando una NUEVA versión inmutable (RF-DIG-01).

        Devuelve None si el anexo no existe. (response, created): created=False
        si el contenido es idéntico a la versión vigente (no se crea versión).
        Lanza NotCurrentVersionError si `file_id` no es la versión vigente, y
        UnsupportedFormatError si el formato no está admitido.
        """
        current = await self._repo.get_by_id(file_id)
        if current is None:
            return None
        if not current.is_current:
            raise NotCurrentVersionError(
                f"{file_id} no es la versión vigente; reemplace la versión actual"
            )

        _check_format(mime_type)
        sha256 = hashlib.sha256(content).hexdigest()
        if sha256 == current.sha256:
            # Contenido idéntico: no se crea una versión nueva.
            return _to_response(current), False

        minio = get_minio()
        await _ensure_bucket(minio, self._bucket)
        new_id = uuid.uuid4()
        object_key = f"{new_id}/{filename}"
        await minio.put_object(
            self._bucket,
            object_key,
            io.BytesIO(content),
            length=len(content),
            content_type=mime_type or "application/octet-stream",
        )
        record = await self._repo.create_version(
            previous=current,
            file_id=new_id,
            filename=filename,
            mime_type=mime_type,
            file_size=len(content),
            sha256=sha256,
            bucket=self._bucket,
            object_key=object_key,
            uploaded_by=uploaded_by,
        )
        return _to_response(record), True

    async def list_versions(self, file_id: uuid.UUID) -> list[FileResponse] | None:
        """Lista la cadena de versiones del anexo al que pertenece `file_id`."""
        record = await self._repo.get_by_id(file_id)
        if record is None:
            return None
        root_id = record.root_id or record.id
        records = await self._repo.list_versions(root_id)
        return [_to_response(r) for r in records]

    async def get_metadata(self, file_id: uuid.UUID) -> FileResponse | None:
        record = await self._repo.get_by_id(file_id)
        return _to_response(record) if record is not None else None

    async def get_presigned_url(
        self, file_id: uuid.UUID, expiry_seconds: int
    ) -> PresignedUrlResponse | None:
        record = await self._repo.get_by_id(file_id)
        if record is None:
            return None

        minio = get_minio()
        url = await minio.presigned_get_object(
            record.bucket,
            record.object_key,
            expires=timedelta(seconds=expiry_seconds),
        )
        return PresignedUrlResponse(url=url, expires_in=expiry_seconds)

    async def get_object_stream(
        self, file_id: uuid.UUID, chunk_size: int = 65536
    ) -> tuple[FileRecord, AsyncIterator[bytes]] | None:
        """Devuelve ``(record, generador de bytes)`` para hacer streaming del objeto.

        D-02: alimenta ``GET /files/{id}/content`` — SOLO para mediadores
        internos (document-service, tras su propio gate de clearance
        RF-SEG-08). A diferencia de `verify_integrity` (que carga el objeto
        COMPLETO en memoria para recalcular el hash), aquí se hace streaming
        real sobre ``aiohttp.ClientResponse.content`` (lo que devuelve
        miniopy_async), sin acumular el archivo entero en memoria de
        storage-service.
        """
        record = await self._repo.get_by_id(file_id)
        if record is None:
            return None

        minio = get_minio()
        response = await minio.get_object(record.bucket, record.object_key)

        async def _chunks() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.content.iter_chunked(chunk_size):
                    yield chunk
            finally:
                close = getattr(response, "close", None)
                if close is not None:
                    close()

        return record, _chunks()

    async def verify_integrity(self, file_id: uuid.UUID) -> IntegrityResponse | None:
        """Reverifica el SHA-256 del objeto en MinIO contra el registrado (RF-DIG-02).

        Descarga el objeto, recalcula el hash y lo compara. Devuelve None si el
        archivo no existe; en otro caso un IntegrityResponse (integrity_ok indica
        si coinciden).
        """
        record = await self._repo.get_by_id(file_id)
        if record is None:
            return None

        minio = get_minio()
        obj = await minio.get_object(record.bucket, record.object_key)
        try:
            data = await obj.read()
        finally:
            close = getattr(obj, "close", None)
            if close is not None:
                close()

        actual = hashlib.sha256(data).hexdigest()
        return IntegrityResponse(
            file_id=record.id,
            expected_sha256=record.sha256,
            actual_sha256=actual,
            integrity_ok=(actual == record.sha256),
        )

    async def delete(
        self, file_id: uuid.UUID, deleted_by: uuid.UUID | None = None
    ) -> bool:
        """Soft-delete: marks the file as logically deleted.

        Does NOT call remove_object on MinIO — the object is preserved until
        a TRD final disposition explicitly purges it. Records the act in the
        immutable audit_log via orpycamcp_common.audit (constitución inv. 12).
        """
        record = await self._repo.get_by_id(file_id)
        if record is None:
            return False

        soft_deleted = await self._repo.soft_delete(file_id, deleted_by)
        if not soft_deleted:
            # File was already soft-deleted between get_by_id and soft_delete;
            # treat as not found so the caller returns 404.
            return False

        await audit.append(
            self._conn,
            tenant_slug=self._tenant_slug,
            service=_SERVICE,
            action="file.soft_deleted",
            object_type="file",
            object_ref=str(file_id),
            actor=str(deleted_by) if deleted_by else None,
            payload={"filename": record.filename, "sha256": record.sha256},
            canal=audit.CANAL_PETICION,
        )
        return True

    async def purge_for_disposition(
        self,
        file_id: uuid.UUID,
        expediente_id: str,
        reason: str,
        actor: uuid.UUID | None = None,
    ) -> bool:
        """Physical removal as part of TRD final disposition.

        This is the ONLY place that calls remove_object on MinIO. The file may
        be either active or already soft-deleted when disposition is executed.
        Sequence: WORM gate → remove MinIO object → hard-delete DB row → audit.

        `expediente_id` identifies the case file this purge belongs to — the WORM
        gate (E10 Increment A) rejects the ENTIRE purge for that expediente while
        a CONTENT WORM object (índice/aip) is under active retention, or while
        ANY WORM object (content or control instrument) is under `legal_hold`,
        even if `file_id` itself is not the protected object. Retention on a
        CONTROL INSTRUMENT (acta_transferencia — see
        `app.core.preservacion_tipos.CONTROL_INSTRUMENT_TIPOS`) does NOT block
        content disposition: E10 Inc.2 remedy, the acta survives the content as
        evidence of the act (Ley 594/2000 art. 26 + elimination procedure
        Ac. AGN 004/2013 art. 22) and must not postpone disposition mandated by
        the TRD (Ley 594 arts. 24-26).

        Returns False if no record exists (already purged or never existed).
        Raises `WormLockActiveError` if the expediente is still under WORM lock.
        """
        # Mismo string canónico que `ProtegerIndiceRequest` exige al proteger:
        # el gate de abajo llavea por igualdad exacta contra ese valor
        # (hallazgo de auditoría Increment A) — sin esto, un `expediente_id`
        # con espacios incidentales distintos entre protección y disposición
        # dejaría pasar la purga sin bloquear.
        expediente_id = canonicalize_expediente_id(expediente_id)

        record = await self._repo.get_by_id_any_state(file_id)
        if record is None:
            return False

        worm_repo = PreservacionWormRepository(self._conn, self._tenant_slug)
        active_lock = await worm_repo.active_lock_for_expediente(
            expediente_id, exclude_retention_tipos=tuple(CONTROL_INSTRUMENT_TIPOS)
        )
        if active_lock is not None:
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="file.purga_bloqueada_worm",
                object_type="file",
                object_ref=str(file_id),
                actor=str(actor) if actor else None,
                payload={
                    "expediente_id": expediente_id,
                    "worm_object_id": str(active_lock["id"]),
                    "retain_until": active_lock["retain_until"].isoformat(),
                    "legal_hold": active_lock["legal_hold"],
                },
                canal="system",
            )
            raise WormLockActiveError(
                f"El expediente {expediente_id} tiene un objeto bajo protección "
                f"WORM (retain_until={active_lock['retain_until']}, "
                f"legal_hold={active_lock['legal_hold']}); disposición rechazada"
            )

        minio = get_minio()
        await minio.remove_object(record.bucket, record.object_key)

        await self._repo.purge(file_id)

        await audit.append(
            self._conn,
            tenant_slug=self._tenant_slug,
            service=_SERVICE,
            action="file.purged_disposition",
            object_type="file",
            object_ref=str(file_id),
            actor=str(actor) if actor else None,
            payload={
                "filename": record.filename,
                "sha256": record.sha256,
                "disposition_reason": reason,
            },
            canal="system",
        )
        return True

    async def purge_orphan(
        self,
        file_id: uuid.UUID,
        *,
        referencia: str,
        motivo: str,
        actor: uuid.UUID | None,
        subido_desde: datetime,
    ) -> PurgaHuerfano:
        """Borrado físico SEGURO de un binario que quedó sin radicado (import fallido).

        storage deduplica por SHA-256: el `file_id` que devolvió la subida puede ser
        un objeto que sostiene anexos de otros radicados. Por eso solo se borra si
        TODO esto se cumple, dentro de UNA transacción con la fila bloqueada:
          1. existe y NO es anterior a `subido_desde` (lo creó esa importación; una
             coincidencia de hash con algo preexistente nunca se toca);
          2. nada lo referencia (`FileRepository.referencias`: anexos de cualquier
             radicado, correos, borradores, WORM, migraciones, versiones);
          3. ningún `upload` lo devolvió por deduplicación (`files.dedup_hit_at`, mig. 017):
             un objeto COMPARTIDO por dedup se conserva siempre, aunque la otra
             operación aún no haya commiteado su referencia. La marca la escribe un
             UPDATE que toma el bloqueo de fila, por eso se serializa con el `FOR UPDATE`
             de esta transacción;
          4. el borrado en el almacén tiene éxito. El bucket está VERSIONADO: un
             `remove_object` sin `versionId` NO borra nada protegido, solo crea un
             delete marker; lo que de verdad impide perder un objeto bajo WORM es
             `preservacion_worm_objeto` en `referencias()` (paso 2), no el almacén.
        Fila, objeto y asiento (`file.purgado_huerfano`) van juntos: si el asiento
        falla, la fila se conserva y el reintento repite el borrado del objeto, que
        es idempotente. Nunca lanza por un caso esperado: lo describe en el resultado."""
        repo = self._repo
        async with self._conn.transaction():
            record = await repo.lock_any_state(file_id)
            if record is None:
                return PurgaHuerfano(file_id, "no_encontrado")
            if record.uploaded_at < subido_desde:
                return PurgaHuerfano(file_id, "preexistente")
            if await repo.fue_reutilizado_por_dedup(file_id):
                return PurgaHuerfano(file_id, "referenciado", "files.dedup_hit_at")
            fuentes = await repo.referencias(file_id)
            if fuentes:
                worm = any(f.startswith("preservacion_worm_objeto") for f in fuentes)
                return PurgaHuerfano(
                    file_id, "protegido_worm" if worm else "referenciado", ", ".join(fuentes)
                )
            try:
                await get_minio().remove_object(record.bucket, record.object_key)
            except Exception as exc:  # noqa: BLE001 — red, permisos, almacén: se informa
                return PurgaHuerfano(file_id, "error", f"{type(exc).__name__}: {exc}"[:300])
            await repo.purge(file_id)
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="file.purgado_huerfano",
                object_type="file",
                object_ref=str(file_id),
                actor=str(actor) if actor else None,
                payload={
                    "filename": record.filename,
                    "sha256": record.sha256,
                    "referencia": referencia,
                    "motivo": motivo,
                },
                canal="system",
            )
        return PurgaHuerfano(file_id, "purgado")


async def _ensure_bucket(minio: Minio, bucket: str) -> None:
    exists = await minio.bucket_exists(bucket)
    if not exists:
        await minio.make_bucket(bucket)


def _to_response(record: FileRecord) -> FileResponse:
    return FileResponse(
        id=record.id,
        filename=record.filename,
        mime_type=record.mime_type,
        file_size=record.file_size,
        sha256=record.sha256,
        uploaded_at=record.uploaded_at,
        version=record.version,
        is_current=record.is_current,
    )
