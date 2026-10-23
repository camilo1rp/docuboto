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

Sube una copia INMUTABLE (MinIO Object Lock) de un artefacto YA FIRMADO — el
índice electrónico (E15/E06, `tipo="indice"`) o, desde E10 Inc.2 wiring, el
acta de transferencia (H-H/E06, `tipo="acta_transferencia"`) — ya presente en
el bucket `-documents` vía el flujo normal de subida, a un bucket dedicado por
tenant con retención por-objeto. storage-service NO conoce la TRD: el
llamador (archive-service, dueño de la TRD/RF-04) aporta `retain_until` ya
calculado (o los insumos para derivarlo).

`proteger_indice` (nombre conservado por compatibilidad histórica: Increment A
solo protegía el índice) es hoy el método GENÉRICO que ambos `tipo` comparten
— lee `payload.tipo` en vez de asumir `"indice"` (E10 Inc.2 wiring), tanto
para el chequeo de idempotencia como para el `INSERT`/advisory lock. El AIP
real (BagIt+PREMIS, Increment B) NO se protege aquí: usa su propio flujo
(`AipService`, schema `AipCreate`) — el esquema de `preservacion_worm_objeto`
ya admite `tipo="aip"`, pero `ProtegerIndiceRequest.tipo` deliberadamente no
lo acepta (semántica de NO-singleton distinta, ver `schemas/preservacion.py`).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import logging
from collections.abc import AsyncIterator
from datetime import datetime

import asyncpg
from miniopy_async import Minio, S3Error  # type: ignore[attr-defined]
from miniopy_async.retention import Retention
from orpycamcp_common import audit

from app.core.config import settings
from app.core.constants import canal_del_acto
from app.core.minio_client import (
    ObjectLockNotEnabledError,
    ensure_object_lock_bucket,
    get_minio,
    verificar_legal_hold_aplicado,
    verificar_retencion_aplicada,
)
from app.repositories.file_repository import FileRepository
from app.repositories.preservacion_repository import PreservacionRepository
from app.repositories.preservacion_worm_repository import PreservacionWormRepository
from app.schemas.preservacion import (
    FijarRetencionRequest,
    FijarRetencionResponse,
    ProtegerIndiceRequest,
    ProtegerIndiceResponse,
    RenovarArtefactoRequest,
    RenovarArtefactoResponse,
    RenovarRetencionRequest,
    RenovarRetencionResponse,
)
from app.services.aip_replica_service import replica_target
from app.services.retention_policy import InvalidRetentionError, validate_retain_until

logger = logging.getLogger(__name__)

_SERVICE = "storage-service"
_BUCKET_PREFIX = "orpycamcp"

# Códigos S3 que significan «esa copia no existe» (la réplica del AIP es
# best-effort al crearse): se toleran al fijar/levantar un legal hold.
_REPLICA_AUSENTE = frozenset({"NoSuchKey", "NoSuchBucket", "NoSuchVersion"})

# Reexportado: módulos que ya hacían `from app.services.preservation_worm_service
# import InvalidRetentionError` (routers/preservacion.py) siguen funcionando sin
# tocar sus imports — la definición real vive en `retention_policy.py` (E10
# Increment B: `aip_service.py` también la necesita, sin duplicar el piso/techo).
__all__ = [
    "ArtefactoNoEsDeFirmaError",
    "FileNotFoundForPreservationError",
    "FixityMismatchError",
    "InvalidRetentionError",
    "PreservationDisabledError",
    "PreservationWormService",
    "RetencionDiferidaSinHoldError",
    "SinRetencionQueRenovarError",
    "WormObjectNotFoundError",
    "WormSingletonConflictError",
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


class WormSingletonConflictError(Exception):
    """El artefacto singleton `(expediente_id, tipo)` ya está protegido bajo
    WORM con un `file_id` DISTINTO del que llega (AREA-08-16). No se sube nada:
    la protección COMPLIANCE es irreversible y no se «corrige» después."""


class WormObjectNotFoundError(Exception):
    """No existe fila `preservacion_worm_objeto` para `(expediente_id, tipo)`
    -- nada que renovar (E10 debt: renovación de retención WORM para series
    CT). Distinto de `FileNotFoundForPreservationError` (esa es sobre
    `files.id`, no sobre la fila WORM ya protegida)."""


class RetencionDiferidaSinHoldError(Exception):
    """Se pidió proteger con `retencion_diferida` (o se hereda una) y el expediente NO tiene un hold
    activo `valoracion_pendiente` (C-5/C7): un objeto sin retención y sin hold quedaría borrable. No
    se escribe nada."""


class SinRetencionQueRenovarError(Exception):
    """La fila WORM no tiene `retain_until` (retención diferida o valorada sin retención): no hay
    fecha que extender. Su primera retención la fija `fijar_retencion_diferida` (C-5/C7)."""


class ArtefactoNoEsDeFirmaError(Exception):
    """La renovación del ``ArchiveTimeStamp`` (B10 §8) nombra como anterior algo que no es un
    artefacto de firma, o un nuevo que no es XML: no se hereda ninguna política."""


def _es_xml(mime_type: str | None) -> bool:
    m = (mime_type or "").split(";")[0].strip().lower()
    return m in ("application/xml", "text/xml") or m.endswith("+xml")


def _es_tipo_de_firma(tipo: str) -> bool:
    """XML firmado que una renovación de sello de archivo puede sustituir: el índice, sus
    renovaciones y las actas selladas. El AIP (un paquete) no lo es."""
    return tipo in ("indice", "firma_lta_renovada") or tipo.startswith("acta_")


def preservation_bucket_name(tenant_slug: str) -> str:
    """Nombre del bucket WORM del tenant. `tenant_slug` SIEMPRE viene del claim
    JWT re-derivado por el gateway (`X-Tenant-Slug`, validado por
    `core.clearance.require_permission`/`core.database.tenant_schema`) —
    nunca del body de la petición."""
    return f"{_BUCKET_PREFIX}-{tenant_slug}-{settings.preservation_bucket_suffix}"


@contextlib.asynccontextmanager
async def legal_hold_compartido(
    conn: asyncpg.Connection, tenant_slug: str, expediente_id: str
) -> AsyncIterator[None]:
    """Advisory lock de SESIÓN **compartido** sobre `(tenant, expediente, "legal_hold")` (C-5/C7).

    Fijar y levantar un hold toman la MISMA clave en modo exclusivo. Quien escribe un objeto
    protegido toma la compartida desde que LEE «¿hay hold activo?» hasta que inserta la fila: sin
    esto, un hold fijado entre la lectura y el INSERT dejaba un objeto sin hold (el fijar no veía
    la fila aún) y un hold levantado en esa ventana dejaba uno con hold de más. Compartida y no
    exclusiva: varias protecciones del mismo expediente no tienen por qué esperarse entre sí.

    Se emite por `fetch` y no por `execute`/`fetchval`: los tests existentes fijan secuencias de
    `fetchval`/`fetchrow` y cuentan los `execute` de `pg_advisory_lock` (mismo motivo que
    `PreservacionRepository.current_num_copias`)."""
    key = _worm_lock_key(tenant_slug, expediente_id, "legal_hold")
    await conn.fetch("SELECT pg_advisory_lock_shared($1)", key)
    try:
        yield
    finally:
        await conn.fetch("SELECT pg_advisory_unlock_shared($1)", key)


def _iso(valor: datetime | None) -> str | None:
    return valor.isoformat() if valor is not None else None


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
        el artefacto (`payload.tipo`) de este expediente YA estaba protegido
        bajo WORM — la llamada fue idempotente (no se re-subió nada), mismo
        contrato que ``StorageService.upload`` (dedup por SHA-256). El
        llamador HTTP (``routers/preservacion.py``) traduce ``created`` a
        201/200."""
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
        retain_until = payload.resolve_retain_until_opcional()
        if retain_until is not None:
            validate_retain_until(retain_until)

        # Serialización por-(expediente, tipo) (hallazgo Media de auditoría de
        # seguridad): el chequeo de idempotencia de abajo impide filas
        # duplicadas (índice único parcial `ux_..._expediente_indice` /
        # `ux_..._expediente_acta`, E10 Inc.2 wiring), pero NO un segundo
        # `put_object` COMPLIANCE bajo carrera — el intento síncrono del cierre
        # y una barrida de reconciliación concurrente pueden pasar ambos el
        # `get_by_expediente_and_tipo` (ninguno commiteó aún) y subir DOS
        # versiones inmutables irreversibles del mismo artefacto. Un advisory
        # lock de SESIÓN por `(schema, tipo, expediente)` serializa el
        # check->put->insert: el segundo llamador espera y luego ve la fila ->
        # corte-circuito idempotente, sin re-subir. `tipo` en la clave del lock
        # (E10 Inc.2 wiring): sin él, proteger el índice y el acta del MISMO
        # expediente en paralelo se serializarían innecesariamente entre sí
        # (namespaces distintos, artefactos distintos). Lock de SESIÓN (no
        # `xact`) porque el camino de fallo de fixity DEBE persistir su
        # evento/asiento (un rollback de `pg_advisory_xact_lock` los perdería).
        # Se libera en el `finally`, también ante excepción.
        lock_key = _worm_lock_key(self._tenant_slug, payload.expediente_id, payload.tipo)
        await self._conn.execute("SELECT pg_advisory_lock($1)", lock_key)
        try:
            # C-5/C7: la lectura «¿hay hold activo?» y el INSERT de la fila van bajo el lock
            # COMPARTIDO del hold (ver `legal_hold_compartido`).
            async with legal_hold_compartido(
                self._conn, self._tenant_slug, payload.expediente_id
            ):
                return await self._proteger_indice_locked(payload, actor, retain_until)
        finally:
            await self._conn.execute("SELECT pg_advisory_unlock($1)", lock_key)

    async def _proteger_indice_locked(
        self,
        payload: ProtegerIndiceRequest,
        actor: str | None,
        retain_until: datetime | None,
    ) -> tuple[ProtegerIndiceResponse, bool]:
        """Cuerpo de :meth:`proteger_indice` bajo el advisory lock de sesión
        por-(expediente, tipo) — todo el `check->put_object->insert` corre
        serializado contra otra protección concurrente del MISMO artefacto
        (ver la nota del llamador). `retain_until` ya viene validado."""
        # Idempotencia (E10 Inc.1/Inc.2, cableado): chequeo ANTES de tocar
        # fixity/MinIO. Respaldo en código del índice único parcial
        # `ux_preservacion_worm_objeto_expediente_indice` / `_expediente_acta`
        # (migraciones 009/010).
        existing = await self._worm_repo.get_by_expediente_and_tipo(
            payload.expediente_id, payload.tipo, payload.file_id
        )
        if existing is not None:
            # Defensa en profundidad (AREA-08-16): la idempotencia es por
            # (expediente, tipo), NO por file_id. Sin esta comparación, quien
            # llegara primero con OTRO file_id ocupaba el singleton y el
            # llamador legítimo recibía 200 «ya protegido» sobre un archivo que
            # no es el suyo. Filas anteriores a la migración 009 no guardan
            # file_id (None): no hay con qué comparar y se conserva el 200.
            if existing["file_id"] is not None and existing["file_id"] != payload.file_id:
                raise WormSingletonConflictError(
                    f"expediente_id={payload.expediente_id} tipo={payload.tipo} ya está "
                    f"protegido con otro file_id"
                )
            logger.info(
                "Protección WORM ya existe para expediente_id=%s tipo=%s "
                "(idempotente, sin re-subir; worm_id=%s)",
                payload.expediente_id,
                payload.tipo,
                existing["id"],
            )
            return _worm_row_to_response(existing), False

        # C-5/C7: toda protección NACE con `legal_hold = existe un hold activo del expediente`. Lo
        # resuelve el servidor, bajo el lock compartido del hold; nunca el llamador. Los lotes
        # (acta de eliminación) no tienen holds: los holds son de expedientes.
        holds = (
            await self._worm_repo.holds_activos(payload.expediente_id)
            if payload.objeto_tipo == "expediente"
            else []
        )
        hold_activo = bool(holds)
        if retain_until is None and "valoracion_pendiente" not in holds:
            raise RetencionDiferidaSinHoldError(
                f"expediente_id={payload.expediente_id}: retencion_diferida exige un hold "
                "activo `valoracion_pendiente`"
            )

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
                        "tipo": payload.tipo,
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
                canal=canal_del_acto(actor),
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

        origen = "calendario" if retain_until is not None else "valoracion_pendiente"
        mode = settings.preservation_retention_mode if retain_until is not None else None
        retention = (
            Retention(mode, retain_until) if retain_until is not None and mode is not None else None
        )
        object_key = record.object_key

        write_result = await minio.put_object(
            bucket,
            object_key,
            io.BytesIO(data),
            length=len(data),
            content_type="application/xml",
            retention=retention,
            legal_hold=hold_activo,
        )
        version_id = getattr(write_result, "version_id", None)

        # El almacén dijo que sí. Ahora se comprueba.
        #
        # Sin esto, la fila de `preservacion_worm_objeto` y el evento PREMIS
        # `FIJACION` en `ok` se escribían sobre la PALABRA del almacén: si aceptaba
        # la orden y no la cumplía, el sistema quedaba atestiguando una protección
        # inexistente, y el acta de transferencia —firmada— la daría por buena.
        # Fail-closed, igual que la fixity de arriba: si no se puede verificar, no
        # se asienta.
        if retain_until is not None and mode is not None:
            await verificar_retencion_aplicada(
                minio,
                bucket,
                object_key,
                version_id=version_id,
                modo_esperado=mode,
                hasta_esperado=retain_until,
            )
        if hold_activo:
            # Retención diferida: el hold es lo ÚNICO que protege al objeto; con retención, es el
            # hold heredado de otro acto. En ambos casos se relee en vez de creer al almacén.
            await verificar_legal_hold_aplicado(minio, bucket, object_key, version_id=version_id)

        row = await self._worm_repo.insert(
            expediente_id=payload.expediente_id,
            tipo=payload.tipo,
            bucket=bucket,
            object_key=object_key,
            version_id=version_id,
            sha256=actual_sha256,
            retain_until=retain_until,
            retention_mode=mode,
            legal_hold=hold_activo,
            file_id=payload.file_id,
            objeto_tipo=payload.objeto_tipo,
            retencion_origen=origen,
        )

        await self._evt_repo.insert_evento(
            {
                "object_ref": payload.expediente_id,
                "tipo": "FIJACION",
                "resultado": "ok",
                "hash_after": actual_sha256,
                "detalle": {"file_id": str(payload.file_id), "tipo": payload.tipo},
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
                    "tipo": payload.tipo,
                    "bucket": bucket,
                    "object_key": object_key,
                    "retain_until": _iso(retain_until),
                    "retention_mode": mode,
                    "legal_hold": hold_activo,
                    "retencion_origen": origen,
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
                "tipo": payload.tipo,
                "bucket": bucket,
                "object_key": object_key,
                "retain_until": _iso(retain_until),
                "retention_mode": mode,
                "legal_hold": hold_activo,
                "retencion_origen": origen,
            },
            canal=canal_del_acto(actor),
        )

        return ProtegerIndiceResponse(
            id=row["id"],
            expediente_id=payload.expediente_id,
            tipo=payload.tipo,
            objeto_tipo=payload.objeto_tipo,
            bucket=bucket,
            object_key=object_key,
            version_id=version_id,
            sha256=actual_sha256,
            retain_until=retain_until,
            retention_mode=mode,
            legal_hold=hold_activo,
            retencion_origen=origen,
            created_at=row["created_at"],
            file_id=payload.file_id,
        ), True


    # -- E10 debt: renovación de retención WORM para series CT ----------

    async def renovar_retencion(
        self, payload: RenovarRetencionRequest, actor: str | None
    ) -> RenovarRetencionResponse:
        """Extiende `retain_until` de un artefacto YA protegido bajo WORM
        (`payload.tipo`, de `payload.expediente_id`) -- E10 debt: series de
        disposición Conservación Total (CT) deben conservarse para siempre,
        pero el lock COMPLIANCE original vence en una fecha fija. El llamador
        (archive-service, dueño de la TRD) resuelve `retencion_anios`/
        `fecha_inicio=hoy` (ventana rodante) SOLO para series CT -- este
        servicio no conoce la disposición, confía en el contrato del
        llamador (mismo criterio que `proteger_indice`/`file_id` frente a
        `expediente_id`, ver `ProtegerIndiceRequest`).

        Reusa el MISMO advisory lock de sesión por `(schema, tipo,
        expediente)` que `proteger_indice` (`_worm_lock_key`) -- serializa
        una renovación concurrente con una protección inicial o con otra
        renovación del MISMO artefacto (dos barridas de reconciliación
        solapadas, por ejemplo)."""
        if not settings.preservation_enabled:
            raise PreservationDisabledError(
                "La protección WORM está desactivada (PRESERVATION_ENABLED=false)"
            )

        nueva = payload.resolve_retain_until()
        validate_retain_until(nueva)

        lock_key = _worm_lock_key(self._tenant_slug, payload.expediente_id, payload.tipo)
        await self._conn.execute("SELECT pg_advisory_lock($1)", lock_key)
        try:
            return await self._renovar_retencion_locked(payload, actor, nueva)
        finally:
            await self._conn.execute("SELECT pg_advisory_unlock($1)", lock_key)

    async def _renovar_retencion_locked(
        self,
        payload: RenovarRetencionRequest,
        actor: str | None,
        nueva: datetime,
    ) -> RenovarRetencionResponse:
        """Cuerpo de :meth:`renovar_retencion` bajo el advisory lock de
        sesión por-(expediente, tipo). `nueva` ya viene validada."""
        existing = await self._worm_repo.get_by_expediente_and_tipo(
            payload.expediente_id, payload.tipo, payload.file_id
        )
        if existing is None:
            raise WormObjectNotFoundError(
                f"Sin objeto WORM protegido para expediente_id={payload.expediente_id} "
                f"tipo={payload.tipo}"
            )

        # B10 (SEG #3 / ARCH H5): los XML de una firma XAdES-LTA renovada (`renueva_a`) son
        # evidencia del MISMO artefacto y tienen que vivir al menos tanto como él. Se extienden
        # ANTES que la raíz y también cuando la raíz ya está al día (no-op): si una pasada
        # anterior extendió la raíz y se cortó a medias, la siguiente no vuelve a llamar aquí
        # por ella (archive solo mira la raíz) y el XML vigente quedaría con la fecha vieja.
        if existing["retain_until"] is None:
            # C-5/C7: retención diferida o valorada sin retención -- no hay fecha que extender, y
            # `nueva <= None` reventaría. La primera retención la fija `fijar_retencion_diferida`.
            raise SinRetencionQueRenovarError(
                f"expediente_id={payload.expediente_id} tipo={payload.tipo} no tiene retain_until "
                f"({existing.get('retencion_origen', 'sin_origen')}): no hay retención que renovar"
            )
        await self._extender_cadena_renovada(existing, nueva, actor)

        if nueva <= existing["retain_until"]:
            # No-op idempotente (E10 debt): la fecha resuelta NO supera la ya
            # persistida -- no se llama a MinIO (COMPLIANCE de todos modos lo
            # rechazaría, pero no tiene sentido intentar un PUT de retención
            # que no cambia nada). Mismo criterio de idempotencia silenciosa
            # que `proteger_indice` con una fila ya existente.
            logger.info(
                "Renovación WORM no-op (nueva<=actual) expediente_id=%s tipo=%s "
                "actual=%s nueva=%s",
                payload.expediente_id,
                payload.tipo,
                existing["retain_until"],
                nueva,
            )
            return _worm_row_to_renovacion_response(existing, renovado=False)

        minio = get_minio()
        retention = Retention(existing["retention_mode"], nueva)
        # COMPLIANCE en MinIO/S3 rechaza acortar la retención de un objeto
        # (defensa en profundidad del lado del servidor) -- el chequeo
        # `nueva <= existing["retain_until"]` de arriba ya evita ENVIAR un
        # intento de acortar, este `set_object_retention` solo puede
        # EXTENDER. `retention_mode` se reusa de la fila persistida, nunca
        # del payload (`RenovarRetencionRequest` no lo declara): el modo no
        # cambia entre protección y renovación.
        await minio.set_object_retention(
            existing["bucket"],
            existing["object_key"],
            retention,
            version_id=existing["version_id"],
        )

        # Y se relee, igual que al proteger. El comentario de arriba dice que esta
        # llamada «ya confirmó la extensión del lado del servidor» — eso es
        # exactamente lo que una respuesta 200 NO demuestra, y lo que este
        # read-back convierte en comprobado. Sin él, una renovación que el almacén
        # acepta y no aplica dejaría el registro diciendo que el expediente está
        # protegido hasta una fecha que solo existe en nuestra base.
        await verificar_retencion_aplicada(
            minio,
            existing["bucket"],
            existing["object_key"],
            version_id=existing["version_id"],
            modo_esperado=existing["retention_mode"],
            hasta_esperado=nueva,
        )

        # Atomicidad del asiento (remediación de auditoría de seguridad, Baja):
        # el UPDATE de `renovar()`, el evento PREMIS RENOVACION y el asiento
        # `file.worm_retencion_renovada` deben comprometerse JUNTOS -- antes
        # corrían como tres `execute`/`fetchrow` sueltos, así que un fallo de
        # proceso entre el UPDATE y el asiento de auditoría dejaba la
        # extensión SIN traza (Ley 594/2000 art. 21, trazabilidad íntegra).
        # La llamada a MinIO de arriba queda DELIBERADAMENTE FUERA: ya
        # confirmó la extensión del lado del servidor y un `ROLLBACK` de
        # Postgres no puede deshacerla (mismo criterio que `proteger_indice`
        # con `put_object`).
        async with self._conn.transaction():
            row = await self._worm_repo.renovar(existing["id"], retain_until=nueva)
            if row is None:
                # Defensa en profundidad (no debería alcanzarse bajo el advisory
                # lock de sesión, ver el docstring de `renovar`): si ocurriera,
                # se re-lee la fila para responder con el estado REAL persistido
                # en vez de asumir a ciegas que la extensión que se acaba de
                # confirmar en MinIO ya está en BD. Solo lectura -- no rompe la
                # atomicidad del bloque anterior porque no hubo escritura previa
                # en esta rama.
                logger.error(
                    "renovar() no afectó ninguna fila bajo el advisory lock de sesión "
                    "(expediente_id=%s tipo=%s) -- releyendo estado real",
                    payload.expediente_id,
                    payload.tipo,
                )
                refreshed = await self._worm_repo.get_by_expediente_and_tipo(
                    payload.expediente_id, payload.tipo, payload.file_id
                )
                assert refreshed is not None
                return _worm_row_to_renovacion_response(refreshed, renovado=False)

            await self._evt_repo.insert_evento(
                {
                    "object_ref": payload.expediente_id,
                    "tipo": "RENOVACION",
                    "resultado": "ok",
                    "detalle": {
                        "tipo": payload.tipo,
                        "retain_until_anterior": existing["retain_until"].isoformat(),
                        "retain_until_nuevo": nueva.isoformat(),
                    },
                },
                agente=actor,
            )
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="file.worm_retencion_renovada",
                object_type="file",
                object_ref=(
                    str(existing["file_id"]) if existing["file_id"] else payload.expediente_id
                ),
                actor=actor,
                payload={
                    "expediente_id": payload.expediente_id,
                    "tipo": payload.tipo,
                    "retain_until_anterior": existing["retain_until"].isoformat(),
                    "retain_until_nuevo": nueva.isoformat(),
                },
                canal=canal_del_acto(actor),
            )
        return _worm_row_to_renovacion_response(row, renovado=True)


    async def _extender_cadena_renovada(
        self, raiz: asyncpg.Record, nueva: datetime, actor: str | None
    ) -> int:
        """Extiende a ``nueva`` cada fila que renueva (directa o transitivamente) a ``raiz``.
        Devuelve cuántas extendió. Mismo orden que la raíz: almacén, relectura, y fila + PREMIS +
        asiento juntos en una transacción."""
        extendidas = 0
        for fila in await self._worm_repo.cadena_descendiente(raiz["id"]):
            # Una fila sin `retain_until` (diferida, C-5/C7) no se extiende: se fija con la raíz
            # en `fijar_retencion_diferida`, que las alcanza a todas por expediente.
            if fila["retain_until"] is None or nueva <= fila["retain_until"]:
                continue
            minio = get_minio()
            await minio.set_object_retention(
                fila["bucket"],
                fila["object_key"],
                Retention(fila["retention_mode"], nueva),
                version_id=fila["version_id"],
            )
            await verificar_retencion_aplicada(
                minio,
                fila["bucket"],
                fila["object_key"],
                version_id=fila["version_id"],
                modo_esperado=fila["retention_mode"],
                hasta_esperado=nueva,
            )
            async with self._conn.transaction():
                row = await self._worm_repo.renovar(fila["id"], retain_until=nueva)
                if row is None:  # pragma: no cover — bajo el advisory lock no debe ocurrir
                    continue
                ref = str(fila["file_id"]) if fila["file_id"] else str(fila["id"])
                await self._evt_repo.insert_evento(
                    {
                        "object_ref": ref,
                        "tipo": "RENOVACION",
                        "resultado": "ok",
                        "detalle": {
                            "tipo": fila["tipo"],
                            "subtipo": "retencion_en_cascada",
                            "renueva_a": str(raiz["id"]),
                            "retain_until_anterior": fila["retain_until"].isoformat(),
                            "retain_until_nuevo": nueva.isoformat(),
                        },
                    },
                    agente=actor,
                )
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="file.worm_retencion_renovada",
                    object_type="file",
                    object_ref=ref,
                    actor=actor,
                    payload={
                        "expediente_id": fila["expediente_id"],
                        "tipo": fila["tipo"],
                        "en_cascada_de": str(raiz["id"]),
                        "retain_until_anterior": fila["retain_until"].isoformat(),
                        "retain_until_nuevo": nueva.isoformat(),
                    },
                    canal=canal_del_acto(actor),
                )
            extendidas += 1
        return extendidas

    # -- B10 §8: renovación del ArchiveTimeStamp de una firma XAdES-LTA -----

    async def renovar_artefacto(
        self, payload: RenovarArtefactoRequest, actor: str | None
    ) -> RenovarArtefactoResponse:
        """Registra que signature-service renovó el ``ArchiveTimeStamp`` de una firma LTA.

        El XML renovado es un objeto NUEVO (``file_id_nuevo``), no una versión del anterior: el
        anterior está bajo COMPLIANCE (no se puede sobrescribir, y se perdería la evidencia) y
        ``version_id`` es el ancla de su fila WORM. Si el anterior estaba bajo WORM, el nuevo
        hereda su política (mismo ``retain_until``, modo y legal hold) y queda ligado a él por
        ``renueva_a``. SIEMPRE se registra el evento PREMIS ``RENOVACION``.

        Idempotente por ``dedup_key = lta:{firma_id}:{ordinal}``; la fila WORM, el evento PREMIS
        y el asiento van en UNA transacción (el ``put_object`` COMPLIANCE queda fuera, como en
        ``proteger_indice``: ya es irreversible)."""
        lock_key = _worm_lock_key(self._tenant_slug, str(payload.firma_id), "firma_lta_renovada")
        await self._conn.execute("SELECT pg_advisory_lock($1)", lock_key)
        try:
            return await self._renovar_artefacto_locked(payload, actor)
        finally:
            await self._conn.execute("SELECT pg_advisory_unlock($1)", lock_key)

    async def _renovar_artefacto_locked(
        self, payload: RenovarArtefactoRequest, actor: str | None
    ) -> RenovarArtefactoResponse:
        """Envoltorio: libera el lock compartido del hold que el cuerpo toma cuando ya sabe DE QUÉ
        expediente es el anterior (no se puede saber antes sin una lectura extra)."""
        retenidos: list[str] = []
        try:
            return await self._renovar_artefacto_cuerpo(payload, actor, retenidos)
        finally:
            for exp in retenidos:
                await self._conn.fetch(
                    "SELECT pg_advisory_unlock_shared($1)",
                    _worm_lock_key(self._tenant_slug, exp, "legal_hold"),
                )

    async def _renovar_artefacto_cuerpo(
        self, payload: RenovarArtefactoRequest, actor: str | None, retenidos: list[str]
    ) -> RenovarArtefactoResponse:
        dedup_key = f"lta:{payload.firma_id}:{payload.ordinal}"
        ref_nuevo = str(payload.file_id_nuevo)

        previo = await self._evt_repo.find_evento_por_dedup(
            object_ref=ref_nuevo, tipo="RENOVACION", dedup_key=dedup_key
        )
        if previo is not None:
            fila = await self._worm_repo.get_by_file_id(payload.file_id_nuevo)
            return RenovarArtefactoResponse(
                protegido=fila is not None,
                retain_until=fila["retain_until"] if fila is not None else None,
                evento_id=previo["id"],
                ya_registrada=True,
            )

        anterior = await self._file_repo.get_by_id_any_state(payload.file_id_anterior)
        nuevo = await self._file_repo.get_by_id_any_state(payload.file_id_nuevo)
        if anterior is None or nuevo is None:
            raise FileNotFoundForPreservationError(
                str(payload.file_id_anterior if anterior is None else payload.file_id_nuevo)
            )
        if nuevo.sha256 != payload.sha256_nuevo:
            raise FixityMismatchError(
                f"SHA-256 no coincide: declarado {payload.sha256_nuevo}, registrado {nuevo.sha256}"
            )

        # El anterior tiene que ser un artefacto de FIRMA (XML firmado) y el nuevo, XML. La
        # ruta solo la alcanza `X-Internal-Token`, pero heredar una retención COMPLIANCE no debe
        # depender de que el llamador diga la verdad sobre qué sustituye a qué.
        if not _es_xml(anterior.mime_type) or not _es_xml(nuevo.mime_type):
            raise ArtefactoNoEsDeFirmaError(
                f"el anterior ({anterior.mime_type}) y el nuevo ({nuevo.mime_type}) deben ser XML"
            )
        fila_anterior = await self._worm_repo.get_by_file_id(payload.file_id_anterior)
        if fila_anterior is not None and not _es_tipo_de_firma(fila_anterior["tipo"]):
            raise ArtefactoNoEsDeFirmaError(
                f"el objeto WORM anterior es de tipo {fila_anterior['tipo']!r}, no un artefacto de firma"
            )
        protege = fila_anterior is not None and settings.preservation_enabled
        # Hereda el MAYOR retain_until de la cadena (raíz incluida): el anterior puede ir por
        # detrás si la raíz se extendió después (series de conservación total).
        retain_heredado: datetime | None = None
        hold_heredado = False
        origen_heredado = "calendario"
        if protege:
            assert fila_anterior is not None
            # C-5/C7: el objeto nuevo hereda el hold ACTIVO del expediente del anterior (no el
            # booleano de la fila anterior): se lee y se inserta bajo el lock compartido del hold.
            await self._conn.fetch(
                "SELECT pg_advisory_lock_shared($1)",
                _worm_lock_key(self._tenant_slug, fila_anterior["expediente_id"], "legal_hold"),
            )
            retenidos.append(fila_anterior["expediente_id"])
            candidatos = [
                f
                for f in (
                    fila_anterior["retain_until"],
                    await self._worm_repo.retain_until_de_la_raiz(fila_anterior["id"]),
                )
                if f is not None
            ]
            retain_heredado = max(candidatos) if candidatos else None
            if fila_anterior["objeto_tipo"] == "expediente":
                holds = await self._worm_repo.holds_activos(fila_anterior["expediente_id"])
            else:
                holds = []
            # Unión con el booleano de la fila anterior: el invariante dice que coinciden, pero si
            # no (fila sin registro), perder un hold es el error caro y heredarlo de más no.
            hold_heredado = bool(holds) or bool(fila_anterior["legal_hold"])
            if retain_heredado is None:
                # Anterior sin retención (C-5/C7): hereda su ORIGEN. Si sigue pendiente de
                # valoración, el hold tiene que seguir vivo o el objeto nuevo quedaría borrable.
                origen_heredado = fila_anterior.get("retencion_origen", "valoracion_pendiente")
                if origen_heredado == "valoracion_pendiente" and "valoracion_pendiente" not in holds:
                    raise RetencionDiferidaSinHoldError(
                        f"expediente_id={fila_anterior['expediente_id']}: el anterior espera "
                        "valoración y su hold `valoracion_pendiente` ya no está activo"
                    )

        bucket = version_id = None
        if protege:
            assert fila_anterior is not None
            minio = get_minio()
            obj = await minio.get_object(nuevo.bucket, nuevo.object_key)
            try:
                data = await obj.read()
            finally:
                close = getattr(obj, "close", None)
                if close is not None:
                    close()
            actual = hashlib.sha256(data).hexdigest()
            if actual != nuevo.sha256:
                raise FixityMismatchError(
                    f"SHA-256 no coincide: esperado {nuevo.sha256}, obtenido {actual}"
                )
            bucket = preservation_bucket_name(self._tenant_slug)
            await ensure_object_lock_bucket(minio, bucket)
            write_result = await minio.put_object(
                bucket,
                nuevo.object_key,
                io.BytesIO(data),
                length=len(data),
                content_type="application/xml",
                retention=(
                    Retention(fila_anterior["retention_mode"], retain_heredado)
                    if retain_heredado is not None
                    else None
                ),
                legal_hold=hold_heredado,
            )
            version_id = getattr(write_result, "version_id", None)
            if retain_heredado is not None:
                await verificar_retencion_aplicada(
                    minio,
                    bucket,
                    nuevo.object_key,
                    version_id=version_id,
                    modo_esperado=fila_anterior["retention_mode"],
                    hasta_esperado=retain_heredado,
                )
            if hold_heredado:
                await verificar_legal_hold_aplicado(
                    minio, bucket, nuevo.object_key, version_id=version_id
                )

        async with self._conn.transaction():
            if protege:
                assert fila_anterior is not None
                await self._worm_repo.insert(
                    expediente_id=fila_anterior["expediente_id"],
                    tipo="firma_lta_renovada",
                    bucket=bucket or "",
                    object_key=nuevo.object_key,
                    version_id=version_id,
                    sha256=nuevo.sha256,
                    retain_until=retain_heredado,
                    retention_mode=(
                        fila_anterior["retention_mode"] if retain_heredado is not None else None
                    ),
                    legal_hold=hold_heredado,
                    file_id=payload.file_id_nuevo,
                    objeto_tipo=fila_anterior["objeto_tipo"],
                    renueva_a=fila_anterior["id"],
                    retencion_origen=origen_heredado if retain_heredado is None else "calendario",
                )
            evento = await self._evt_repo.insert_evento(
                {
                    "object_ref": ref_nuevo,
                    "tipo": "RENOVACION",
                    "resultado": "ok",
                    "hash_before": anterior.sha256,
                    "hash_after": nuevo.sha256,
                    "detalle": {
                        "subtipo": "archive_timestamp",
                        "firma_id": str(payload.firma_id),
                        "ordinal": payload.ordinal,
                        "file_id_anterior": str(payload.file_id_anterior),
                        "protegido": protege,
                        "dedup_key": dedup_key,
                    },
                },
                agente=actor,
            )
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="file.lta_renovada",
                object_type="file",
                object_ref=ref_nuevo,
                actor=actor,
                payload={
                    "firma_id": str(payload.firma_id),
                    "ordinal": payload.ordinal,
                    "file_id_anterior": str(payload.file_id_anterior),
                    "protegido": protege,
                    "retain_until": (
                        retain_heredado.isoformat() if retain_heredado is not None else None
                    ),
                },
                canal=canal_del_acto(actor),
            )
        return RenovarArtefactoResponse(
            protegido=protege,
            retain_until=retain_heredado,
            evento_id=evento["id"],
        )

    # -- C-5/C7: primera retención de un fondo que esperaba la valoración (CT) --------------

    async def fijar_retencion_diferida(
        self, payload: FijarRetencionRequest, actor: str | None
    ) -> FijarRetencionResponse:
        """`valoracion_pendiente -> calendario`: fija COMPLIANCE por PRIMERA vez en las filas del
        expediente que se escribieron solo con legal hold (valoración CT del Comité).

        - Solo toca filas `retain_until IS NULL AND retencion_origen='valoracion_pendiente'`: una
          fila `calendario` NUNCA se reasigna (la retención no se acorta; se extiende por
          `renovar_retencion`). Es el control contra un llamador que pasara aquí un expediente ya
          protegido.
        - El orden es el que deja el error del lado conservador: almacén (retención sobre la
          `version_id` exacta, y sobre la RÉPLICA del AIP) -> relectura -> fila + PREMIS + asiento
          en UNA transacción. Si se corta tras el almacén, la fila sigue `valoracion_pendiente`
          (con hold: nada queda desprotegido) y el reintento repite el PUT, que sobre una
          retención ya igual o mayor es idempotente.
        - Serializado con fijar/levantar hold (lock EXCLUSIVO de la misma clave): el levantamiento
          de la valoración pasa las filas pendientes a `valorada_sin_retencion` y no puede
          intercalarse con esto.
        - La fecha pasa por `validate_retain_until` (piso y techo de storage)."""
        if not settings.preservation_enabled:
            raise PreservationDisabledError(
                "La protección WORM está desactivada (PRESERVATION_ENABLED=false)"
            )
        nueva = payload.resolve_retain_until()
        validate_retain_until(nueva)

        lock_key = _worm_lock_key(self._tenant_slug, payload.expediente_id, "legal_hold")
        await self._conn.execute("SELECT pg_advisory_lock($1)", lock_key)
        try:
            return await self._fijar_retencion_diferida_locked(payload, actor, nueva)
        finally:
            await self._conn.execute("SELECT pg_advisory_unlock($1)", lock_key)

    async def _fijar_retencion_diferida_locked(
        self, payload: FijarRetencionRequest, actor: str | None, nueva: datetime
    ) -> FijarRetencionResponse:
        mode = settings.preservation_retention_mode
        fijadas = 0
        for fila in await self._worm_repo.pendientes_de_valoracion(payload.expediente_id):
            minio = get_minio()
            await minio.set_object_retention(
                fila["bucket"],
                fila["object_key"],
                Retention(mode, nueva),
                version_id=fila["version_id"],
            )
            await verificar_retencion_aplicada(
                minio,
                fila["bucket"],
                fila["object_key"],
                version_id=fila["version_id"],
                modo_esperado=mode,
                hasta_esperado=nueva,
            )
            if fila["tipo"] == "aip":
                await self._retener_replica(minio, fila, mode, nueva)
            async with self._conn.transaction():
                row = await self._worm_repo.fijar_retencion_primera_vez(
                    fila["id"], retain_until=nueva, retention_mode=mode
                )
                if row is None:  # pragma: no cover — bajo el lock exclusivo no debe ocurrir
                    continue
                if fila["tipo"] == "aip":
                    await self._worm_repo.sincronizar_aip_retencion(
                        bucket=fila["bucket"],
                        object_key=fila["object_key"],
                        retain_until=nueva,
                        retention_mode=mode,
                    )
                ref = str(fila["file_id"]) if fila["file_id"] else str(fila["id"])
                await self._evt_repo.insert_evento(
                    {
                        "object_ref": payload.expediente_id,
                        "tipo": "WORM",
                        "resultado": "ok",
                        "detalle": {
                            "subtipo": "retencion_fijada_por_valoracion",
                            "tipo": fila["tipo"],
                            "worm_object_id": str(fila["id"]),
                            "retain_until": nueva.isoformat(),
                            "retention_mode": mode,
                        },
                    },
                    agente=actor,
                )
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="file.worm_retencion_fijada",
                    object_type="file",
                    object_ref=ref,
                    actor=actor,
                    payload={
                        "expediente_id": payload.expediente_id,
                        "tipo": fila["tipo"],
                        "worm_object_id": str(fila["id"]),
                        "retencion_origen_anterior": "valoracion_pendiente",
                        "retain_until": nueva.isoformat(),
                        "retention_mode": mode,
                    },
                    canal=canal_del_acto(actor),
                )
            fijadas += 1
        restantes = await self._worm_repo.pendientes_de_valoracion(payload.expediente_id)
        return FijarRetencionResponse(
            expediente_id=payload.expediente_id,
            retain_until=nueva,
            filas_fijadas=fijadas,
            filas_pendientes_restantes=len(restantes),
        )

    async def _retener_replica(
        self, minio: Minio, fila: asyncpg.Record, mode: str, nueva: datetime
    ) -> None:
        """Retención sobre la réplica del AIP (que solo tenía hold). Su AUSENCIA se tolera (la
        réplica es best-effort); cualquier otro error aborta el acto (reintentable)."""
        client, bucket = replica_target(minio, self._tenant_slug)
        try:
            await client.set_object_retention(bucket, fila["object_key"], Retention(mode, nueva))
        except S3Error as exc:
            if exc.code not in _REPLICA_AUSENTE:
                raise
            logger.info(
                "Sin réplica del AIP a la que fijar retención (bucket=%s, key=%s): %s",
                bucket,
                fila["object_key"],
                exc.code,
            )


def _worm_lock_key(tenant_slug: str, expediente_id: str, tipo: str = "indice") -> int:
    """Clave estable (bigint firmado de 64 bits) para el advisory lock de
    sesión que serializa la protección WORM por `(tenant, tipo, expediente)`.
    `tipo` en la clave (E10 Inc.2 wiring, antes fija a `"indice"`): proteger el
    índice y el acta del MISMO expediente son artefactos independientes, no
    deben competir por el mismo lock. Default `"indice"` preserva el contrato
    histórico del único caller que existía antes de esta generalización (el
    caller real, `proteger_indice()`, siempre pasa `payload.tipo`
    explícitamente — el default solo importa para invocaciones legacy/tests
    que aún no conocen `tipo`). SHA-256 determinista (NO `hash()`, que varía
    por proceso) → 8 bytes → int con signo, en el rango de
    `pg_advisory_lock(bigint)`."""
    digest = hashlib.sha256(f"worm:{tipo}:{tenant_slug}:{expediente_id}".encode()).digest()
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
        objeto_tipo=row["objeto_tipo"],
        bucket=row["bucket"],
        object_key=row["object_key"],
        version_id=row["version_id"],
        sha256=row["sha256"],
        retain_until=row["retain_until"],
        retention_mode=row["retention_mode"],
        legal_hold=row["legal_hold"],
        retencion_origen=row.get("retencion_origen", "calendario"),
        created_at=row["created_at"],
        file_id=row["file_id"],
    )


def _worm_row_to_renovacion_response(row, *, renovado: bool) -> RenovarRetencionResponse:
    """Construye la respuesta de `renovar_retencion` a partir de una fila de
    `preservacion_worm_objeto` -- usada tanto en el camino no-op (fila SIN
    tocar) como en el de extensión confirmada (fila YA actualizada por
    `PreservacionWormRepository.renovar`). `renovado` lo decide el llamador
    según cuál de los dos caminos se ejecutó, la fila en sí no lo distingue."""
    return RenovarRetencionResponse(
        id=row["id"],
        expediente_id=row["expediente_id"],
        tipo=row["tipo"],
        bucket=row["bucket"],
        object_key=row["object_key"],
        retain_until=row["retain_until"],
        retention_mode=row["retention_mode"],
        renovado=renovado,
        renovaciones=row["renovaciones"],
        renovado_at=row["renovado_at"],
    )
