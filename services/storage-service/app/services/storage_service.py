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
from datetime import timedelta

import asyncpg
from orpycamcp_common import audit

from app.core.config import settings
from app.core.minio_client import Minio, get_minio
from app.models.file_record import FileRecord
from app.repositories.file_repository import FileRepository
from app.repositories.preservacion_worm_repository import PreservacionWormRepository
from app.schemas.preservacion import canonicalize_expediente_id
from app.schemas.storage import (
    FileResponse,
    IntegrityResponse,
    PresignedUrlResponse,
)

_BUCKET_PREFIX = "orpycamcp"
_SERVICE = "storage-service"


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


def _check_format(mime_type: str | None) -> None:
    allowed = settings.allowed_mime_set
    if "*" in allowed:
        return
    if mime_type not in allowed:
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
    ) -> tuple[FileResponse, bool]:
        """
        Upload a file, deduplicating by SHA-256 within the tenant.

        Returns (FileResponse, created) where created=False means the file
        already existed and was deduplicated — caller should respond with 200.

        Rechaza formatos no admitidos (RF-DIG-04) antes de tocar MinIO/BD.
        """
        _check_format(mime_type)
        sha256 = hashlib.sha256(content).hexdigest()

        existing = await self._repo.find_by_sha256(sha256)
        if existing is not None:
            return _to_response(existing), False

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
        return _to_response(record), True

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
        its protected índice is still under active retention or legal hold, even
        if `file_id` itself is not the protected object: disposition is an
        all-or-nothing act for a given expediente under the same TRD.

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
        active_lock = await worm_repo.active_lock_for_expediente(expediente_id)
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
        )
        return True


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
