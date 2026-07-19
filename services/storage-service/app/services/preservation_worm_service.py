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
from datetime import datetime

import asyncpg
from miniopy_async.retention import Retention
from orpycamcp_common import audit

from app.core.config import settings
from app.core.minio_client import ObjectLockNotEnabledError, ensure_object_lock_bucket, get_minio
from app.repositories.file_repository import FileRepository
from app.repositories.preservacion_repository import PreservacionRepository
from app.repositories.preservacion_worm_repository import PreservacionWormRepository
from app.schemas.preservacion import ProtegerIndiceRequest, ProtegerIndiceResponse
from app.services.retention_policy import InvalidRetentionError, validate_retain_until

logger = logging.getLogger(__name__)

_SERVICE = "storage-service"
_BUCKET_PREFIX = "orpycamcp"

# Reexportado: módulos que ya hacían `from app.services.preservation_worm_service
# import InvalidRetentionError` (routers/preservacion.py) siguen funcionando sin
# tocar sus imports — la definición real vive en `retention_policy.py` (E10
# Increment B: `aip_service.py` también la necesita, sin duplicar el piso/techo).
__all__ = [
    "FileNotFoundForPreservationError",
    "FixityMismatchError",
    "InvalidRetentionError",
    "PreservationDisabledError",
    "PreservationWormService",
    "preservation_bucket_name",
]


class PreservationDisabledError(Exception):
    """PRESERVATION_ENABLED=false — la protección WORM está desactivada."""


class FileNotFoundForPreservationError(Exception):
    """El `file_id` indicado no existe en este tenant."""


class FixityMismatchError(Exception):
    """El SHA-256 recalculado no coincide con el registrado al subir el archivo.

    El objeto NO se sube a WORM en este caso — no tiene sentido fijar
    inmutablemente algo cuya integridad ya está en duda.
    """


def preservation_bucket_name(tenant_slug: str) -> str:
    """Nombre del bucket WORM del tenant. `tenant_slug` SIEMPRE viene del claim
    JWT re-derivado por el gateway (`X-Tenant-Slug`, validado por
    `core.clearance.require_permission`/`core.database.tenant_schema`) —
    nunca del body de la petición."""
    return f"{_BUCKET_PREFIX}-{tenant_slug}-{settings.preservation_bucket_suffix}"


class PreservationWormService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug
        self._file_repo = FileRepository(conn, tenant_slug)
        self._worm_repo = PreservacionWormRepository(conn, tenant_slug)
        self._evt_repo = PreservacionRepository(conn, tenant_slug)

    async def proteger_indice(
        self, payload: ProtegerIndiceRequest, actor: str | None
    ) -> tuple[ProtegerIndiceResponse, bool]:
        """Devuelve ``(respuesta, created)``: ``created=False`` significa que
        el índice de este expediente YA estaba protegido bajo WORM — la
        llamada fue idempotente (no se re-subió nada), mismo contrato que
        ``StorageService.upload`` (dedup por SHA-256). El llamador HTTP
        (``routers/preservacion.py``) traduce ``created`` a 201/200."""
        if not settings.preservation_enabled:
            raise PreservationDisabledError(
                "La protección WORM está desactivada (PRESERVATION_ENABLED=false)"
            )

        # Validación de retención (pura, sin BD/MinIO) PRIMERO — un payload con
        # `retain_until` fuera de rango debe fallar 400 sin tocar nada, mismo
        # invariante que ya probaban los tests existentes
        # (`mock_conn.fetchrow.assert_not_called()`). El chequeo de
        # idempotencia de abajo SÍ toca BD, así que va después de este filtro
        # barato, no antes.
        retain_until = payload.resolve_retain_until()
        validate_retain_until(retain_until)

        # Serialización por-expediente (hallazgo Media de auditoría de
        # seguridad): el chequeo de idempotencia de abajo impide filas
        # duplicadas (índice único parcial `ux_..._expediente_indice`), pero NO
        # un segundo `put_object` COMPLIANCE bajo carrera — el intento síncrono
        # del cierre y una barrida de reconciliación concurrente pueden pasar
        # ambos el `get_by_expediente_and_tipo` (ninguno commiteó aún) y subir
        # DOS versiones inmutables irreversibles del mismo índice. Un advisory
        # lock de SESIÓN por `(schema, expediente)` serializa el
        # check->put->insert: el segundo llamador espera y luego ve la fila ->
        # corte-circuito idempotente, sin re-subir. Lock de SESIÓN (no `xact`)
        # porque el camino de fallo de fixity DEBE persistir su evento/asiento
        # (un rollback de `pg_advisory_xact_lock` los perdería). Se libera en el
        # `finally`, también ante excepción.
        lock_key = _worm_lock_key(self._tenant_slug, payload.expediente_id)
        await self._conn.execute("SELECT pg_advisory_lock($1)", lock_key)
        try:
            return await self._proteger_indice_locked(payload, actor, retain_until)
        finally:
            await self._conn.execute("SELECT pg_advisory_unlock($1)", lock_key)

    async def _proteger_indice_locked(
        self,
        payload: ProtegerIndiceRequest,
        actor: str | None,
        retain_until: datetime,
    ) -> tuple[ProtegerIndiceResponse, bool]:
        """Cuerpo de :meth:`proteger_indice` bajo el advisory lock de sesión
        por-expediente — todo el `check->put_object->insert` corre serializado
        contra otra protección concurrente del MISMO índice (ver la nota del
        llamador). `retain_until` ya viene validado."""
        # Idempotencia (E10 Inc.1, cableado): chequeo ANTES de tocar fixity/
        # MinIO. Respaldo en código del índice único parcial
        # `ux_preservacion_worm_objeto_expediente_indice` (migración 009).
        existing = await self._worm_repo.get_by_expediente_and_tipo(
            payload.expediente_id, "indice"
        )
        if existing is not None:
            logger.info(
                "Protección WORM ya existe para expediente_id=%s tipo=indice "
                "(idempotente, sin re-subir; worm_id=%s)",
                payload.expediente_id,
                existing["id"],
            )
            return _worm_row_to_response(existing), False

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
            file_id=payload.file_id,
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
            file_id=payload.file_id,
        ), True


def _worm_lock_key(tenant_slug: str, expediente_id: str) -> int:
    """Clave estable (bigint firmado de 64 bits) para el advisory lock de
    sesión que serializa la protección WORM por `(tenant, expediente)`. SHA-256
    determinista (NO `hash()`, que varía por proceso) → 8 bytes → int con
    signo, en el rango de `pg_advisory_lock(bigint)`."""
    digest = hashlib.sha256(f"worm:indice:{tenant_slug}:{expediente_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _worm_row_to_response(row) -> ProtegerIndiceResponse:
    """Reconstruye la respuesta a partir de una fila YA EXISTENTE de
    `preservacion_worm_objeto` (camino idempotente) — misma forma que la
    respuesta del camino de creación, campo a campo desde la fila persistida
    en vez de las variables locales de una subida en curso."""
    return ProtegerIndiceResponse(
        id=row["id"],
        expediente_id=row["expediente_id"],
        tipo=row["tipo"],
        bucket=row["bucket"],
        object_key=row["object_key"],
        version_id=row["version_id"],
        sha256=row["sha256"],
        retain_until=row["retain_until"],
        retention_mode=row["retention_mode"],
        legal_hold=row["legal_hold"],
        created_at=row["created_at"],
        file_id=row["file_id"],
    )
