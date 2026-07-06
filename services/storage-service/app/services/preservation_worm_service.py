# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Protección WORM del índice electrónico firmado (E10 Increment A, RF-PRE-03,
AGN 001/2024 Art. 4.3.2.6).

Sube una copia INMUTABLE (MinIO Object Lock) del índice electrónico firmado
(E15/E06), ya presente en el bucket `-documents` vía el flujo normal de subida,
a un bucket dedicado por tenant con retención por-objeto. storage-service NO
conoce la TRD: el llamador (archive-service, dueño de la TRD/RF-04) aporta
`retain_until` ya calculado (o los insumos para derivarlo).

El AIP real (BagIt+PREMIS, Increment B) NO se protege aquí todavía — solo el
índice (`tipo="indice"`); el esquema de `preservacion_worm_objeto` ya admite
`tipo="aip"` para cuando ese incremento llegue.
"""

from __future__ import annotations

import hashlib
import io
import logging
from datetime import UTC, datetime, timedelta

import asyncpg
from miniopy_async.retention import Retention
from orpycamcp_common import audit

from app.core.config import settings
from app.core.minio_client import ObjectLockNotEnabledError, ensure_object_lock_bucket, get_minio
from app.repositories.file_repository import FileRepository
from app.repositories.preservacion_repository import PreservacionRepository
from app.repositories.preservacion_worm_repository import PreservacionWormRepository
from app.schemas.preservacion import ProtegerIndiceRequest, ProtegerIndiceResponse

logger = logging.getLogger(__name__)

_SERVICE = "storage-service"
_BUCKET_PREFIX = "orpycamcp"


class PreservationDisabledError(Exception):
    """PRESERVATION_ENABLED=false — la protección WORM está desactivada."""


class FileNotFoundForPreservationError(Exception):
    """El `file_id` indicado no existe en este tenant."""


class FixityMismatchError(Exception):
    """El SHA-256 recalculado no coincide con el registrado al subir el archivo.

    El objeto NO se sube a WORM en este caso — no tiene sentido fijar
    inmutablemente algo cuya integridad ya está en duda.
    """


class InvalidRetentionError(Exception):
    """`retain_until` ausente, no futuro, o fuera de los límites configurados."""


def preservation_bucket_name(tenant_slug: str) -> str:
    """Nombre del bucket WORM del tenant. `tenant_slug` SIEMPRE viene del claim
    JWT re-derivado por el gateway (`X-Tenant-Slug`, validado por
    `core.clearance.require_permission`/`core.database.tenant_schema`) —
    nunca del body de la petición."""
    return f"{_BUCKET_PREFIX}-{tenant_slug}-{settings.preservation_bucket_suffix}"


def _validate_retain_until(retain_until: datetime) -> None:
    now = datetime.now(tz=UTC)
    if retain_until <= now:
        raise InvalidRetentionError("retain_until debe ser una fecha futura")
    max_dt = now + timedelta(days=365 * settings.preservation_max_retention_years)
    if retain_until > max_dt:
        raise InvalidRetentionError(
            f"retain_until excede el máximo configurado "
            f"({settings.preservation_max_retention_years} años)"
        )
    min_dt = now + timedelta(days=settings.preservation_min_retention_days)
    if retain_until < min_dt:
        raise InvalidRetentionError(
            "retain_until implica menos de "
            f"{settings.preservation_min_retention_days} días de retención "
            "(piso de seguridad WORM)"
        )


class PreservationWormService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug
        self._file_repo = FileRepository(conn, tenant_slug)
        self._worm_repo = PreservacionWormRepository(conn, tenant_slug)
        self._evt_repo = PreservacionRepository(conn, tenant_slug)

    async def proteger_indice(
        self, payload: ProtegerIndiceRequest, actor: str | None
    ) -> ProtegerIndiceResponse:
        if not settings.preservation_enabled:
            raise PreservationDisabledError(
                "La protección WORM está desactivada (PRESERVATION_ENABLED=false)"
            )

        retain_until = payload.resolve_retain_until()
        _validate_retain_until(retain_until)

        # `files` no enlaza a expediente/documento en este incremento: no hay
        # forma de verificar aquí que `file_id` sea el índice firmado DEL
        # `expediente_id` declarado. Ese vínculo lo garantiza el llamador
        # (contrato del gateway: solo archive-service invoca este endpoint) —
        # ver el docstring de `ProtegerIndiceRequest` (hallazgo de auditoría
        # Increment A). `expediente_id` ya llega canonicalizado por el schema.
        record = await self._file_repo.get_by_id_any_state(payload.file_id)
        if record is None:
            raise FileNotFoundForPreservationError(str(payload.file_id))

        minio = get_minio()

        # --- Fixity: recalcula el SHA-256 ANTES de fijar nada en WORM ----------
        obj = await minio.get_object(record.bucket, record.object_key)
        try:
            data = await obj.read()
        finally:
            close = getattr(obj, "close", None)
            if close is not None:
                close()
        actual_sha256 = hashlib.sha256(data).hexdigest()

        if actual_sha256 != record.sha256:
            await self._evt_repo.insert_evento(
                {
                    "object_ref": payload.expediente_id,
                    "tipo": "FIJACION",
                    "resultado": "fallo",
                    "hash_before": record.sha256,
                    "hash_after": actual_sha256,
                    "detalle": {
                        "file_id": str(payload.file_id),
                        "motivo": "sha256_mismatch",
                    },
                },
                agente=actor,
            )
            # Un mismatch de fixity sobre el artefacto de mayor valor legal
            # (el índice firmado) es el evento más sensible de este flujo —
            # debe quedar también en la cadena inmutable hash-encadenada
            # (`audit_log`), no solo en `preservacion_evento` (hallazgo de
            # auditoría Increment A; constitución inv. 12 / trazabilidad MoReq).
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="file.fixity_fallo",
                object_type="file",
                object_ref=str(payload.file_id),
                actor=actor,
                payload={
                    "expediente_id": payload.expediente_id,
                    "hash_esperado": record.sha256,
                    "hash_obtenido": actual_sha256,
                },
            )
            raise FixityMismatchError(
                f"SHA-256 no coincide: esperado {record.sha256}, obtenido {actual_sha256}"
            )

        # --- Provisioning idempotente del bucket WORM (nombre del claim) -------
        bucket = preservation_bucket_name(self._tenant_slug)
        try:
            await ensure_object_lock_bucket(minio, bucket)
        except ObjectLockNotEnabledError:
            logger.error(
                "Bucket WORM %s existe SIN Object Lock — aborta protección "
                "(tenant=%s, expediente=%s)",
                bucket,
                self._tenant_slug,
                payload.expediente_id,
            )
            raise

        mode = settings.preservation_retention_mode
        retention = Retention(mode, retain_until)
        object_key = record.object_key

        write_result = await minio.put_object(
            bucket,
            object_key,
            io.BytesIO(data),
            length=len(data),
            content_type="application/xml",
            retention=retention,
            legal_hold=payload.legal_hold,
        )
        version_id = getattr(write_result, "version_id", None)

        row = await self._worm_repo.insert(
            expediente_id=payload.expediente_id,
            tipo="indice",
            bucket=bucket,
            object_key=object_key,
            version_id=version_id,
            sha256=actual_sha256,
            retain_until=retain_until,
            retention_mode=mode,
            legal_hold=payload.legal_hold,
        )

        await self._evt_repo.insert_evento(
            {
                "object_ref": payload.expediente_id,
                "tipo": "FIJACION",
                "resultado": "ok",
                "hash_after": actual_sha256,
                "detalle": {"file_id": str(payload.file_id)},
            },
            agente=actor,
        )
        await self._evt_repo.insert_evento(
            {
                "object_ref": payload.expediente_id,
                "tipo": "WORM",
                "resultado": "ok",
                "hash_after": actual_sha256,
                "detalle": {
                    "bucket": bucket,
                    "object_key": object_key,
                    "retain_until": retain_until.isoformat(),
                    "retention_mode": mode,
                    "legal_hold": payload.legal_hold,
                },
            },
            agente=actor,
        )
        await audit.append(
            self._conn,
            tenant_slug=self._tenant_slug,
            service=_SERVICE,
            action="file.worm_protegido",
            object_type="file",
            object_ref=str(payload.file_id),
            actor=actor,
            payload={
                "expediente_id": payload.expediente_id,
                "bucket": bucket,
                "object_key": object_key,
                "retain_until": retain_until.isoformat(),
                "retention_mode": mode,
                "legal_hold": payload.legal_hold,
            },
        )

        return ProtegerIndiceResponse(
            id=row["id"],
            expediente_id=payload.expediente_id,
            tipo="indice",
            bucket=bucket,
            object_key=object_key,
            version_id=version_id,
            sha256=actual_sha256,
            retain_until=retain_until,
            retention_mode=mode,
            legal_hold=payload.legal_hold,
            created_at=row["created_at"],
        )
