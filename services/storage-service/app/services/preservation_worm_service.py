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

import hashlib
import io
import logging
import uuid
from datetime import datetime

import asyncpg
from miniopy_async import Minio, S3Error  # type: ignore[attr-defined]
from miniopy_async.retention import Retention
from orpycamcp_common import audit

from app.core.config import settings
from app.core.minio_client import (
    ObjectLockNotEnabledError,
    ensure_object_lock_bucket,
    get_minio,
    verificar_retencion_aplicada,
)
from app.repositories.file_repository import FileRepository
from app.repositories.preservacion_repository import PreservacionRepository
from app.repositories.preservacion_worm_repository import PreservacionWormRepository
from app.schemas.preservacion import (
    LegalHoldRequest,
    LegalHoldResponse,
    ProtegerIndiceRequest,
    ProtegerIndiceResponse,
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
    "FileNotFoundForPreservationError",
    "FixityMismatchError",
    "InvalidRetentionError",
    "PreservationDisabledError",
    "PreservationWormService",
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
        retain_until = payload.resolve_retain_until()
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
        por-(expediente, tipo) — todo el `check->put_object->insert` corre
        serializado contra otra protección concurrente del MISMO artefacto
        (ver la nota del llamador). `retain_until` ya viene validado."""
        # Idempotencia (E10 Inc.1/Inc.2, cableado): chequeo ANTES de tocar
        # fixity/MinIO. Respaldo en código del índice único parcial
        # `ux_preservacion_worm_objeto_expediente_indice` / `_expediente_acta`
        # (migraciones 009/010).
        existing = await self._worm_repo.get_by_expediente_and_tipo(
            payload.expediente_id, payload.tipo
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

        # El almacén dijo que sí. Ahora se comprueba.
        #
        # Sin esto, la fila de `preservacion_worm_objeto` y el evento PREMIS
        # `FIJACION` en `ok` se escribían sobre la PALABRA del almacén: si aceptaba
        # la orden y no la cumplía, el sistema quedaba atestiguando una protección
        # inexistente, y el acta de transferencia —firmada— la daría por buena.
        # Fail-closed, igual que la fixity de arriba: si no se puede verificar, no
        # se asienta.
        await verificar_retencion_aplicada(
            minio,
            bucket,
            object_key,
            version_id=version_id,
            modo_esperado=mode,
            hasta_esperado=retain_until,
        )

        row = await self._worm_repo.insert(
            expediente_id=payload.expediente_id,
            tipo=payload.tipo,
            bucket=bucket,
            object_key=object_key,
            version_id=version_id,
            sha256=actual_sha256,
            retain_until=retain_until,
            retention_mode=mode,
            legal_hold=payload.legal_hold,
            file_id=payload.file_id,
            objeto_tipo=payload.objeto_tipo,
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
                "tipo": payload.tipo,
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
            tipo=payload.tipo,
            objeto_tipo=payload.objeto_tipo,
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
            payload.expediente_id, payload.tipo
        )
        if existing is None:
            raise WormObjectNotFoundError(
                f"Sin objeto WORM protegido para expediente_id={payload.expediente_id} "
                f"tipo={payload.tipo}"
            )

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
                    payload.expediente_id, payload.tipo
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
            )
        return _worm_row_to_renovacion_response(row, renovado=True)


    # -- AREA-08-07: legal hold como acto explícito, con motivo y asiento ---

    async def fijar_legal_hold(
        self, payload: LegalHoldRequest, actor: str | None
    ) -> LegalHoldResponse:
        """Fija o levanta el legal hold de TODOS los objetos WORM del
        expediente (`objeto_tipo='expediente'`): sobre la `version_id` exacta de
        cada fila en el almacén, sobre la RÉPLICA del AIP (si existe), en la
        fila (`legal_hold`) y con asiento en `audit_log`.

        Serializado por advisory lock de sesión `(tenant, expediente)`: dos
        actos concurrentes (fijar y levantar) calculaban sus «pendientes» cada
        uno por su lado y la fila podía acabar distinta del Object Lock.

        El almacén no es transaccional, así que el ORDEN de cada sentido es el
        que deja el error en el lado conservador:

        - FIJAR: almacén primero (un exceso de hold es seguro), luego fila +
          asiento juntos en una `conn.transaction()`.
        - LEVANTAR: asiento de SOLICITUD primero (tx propia, antes de tocar el
          almacén), luego almacén, luego fila + asiento de levantamiento. Si algo
          falla tras liberar, queda constancia de que se intentó y la fila sigue
          en `legal_hold=true` (el gate de disposición bloquea): reintentable, y
          nunca un objeto liberado sin ningún asiento.

        Filas que ya están en el estado pedido no se tocan ni se asientan."""
        if not settings.preservation_enabled:
            raise PreservationDisabledError(
                "La protección WORM está desactivada (PRESERVATION_ENABLED=false)"
            )
        lock_key = _worm_lock_key(self._tenant_slug, payload.expediente_id, "legal_hold")
        await self._conn.execute("SELECT pg_advisory_lock($1)", lock_key)
        try:
            return await self._fijar_legal_hold_locked(payload, actor)
        finally:
            await self._conn.execute("SELECT pg_advisory_unlock($1)", lock_key)

    async def _fijar_legal_hold_locked(
        self, payload: LegalHoldRequest, actor: str | None
    ) -> LegalHoldResponse:
        filas = await self._worm_repo.list_for_expediente(payload.expediente_id)
        if not filas:
            raise WormObjectNotFoundError(
                f"Sin objetos WORM protegidos para expediente_id={payload.expediente_id}"
            )
        pendientes = [f for f in filas if bool(f["legal_hold"]) != payload.activo]

        def _base(f: asyncpg.Record) -> dict[str, object]:
            return {
                "expediente_id": payload.expediente_id,
                "tipo": f["tipo"],
                "worm_object_id": str(f["id"]),
                "motivo": payload.motivo,
                "acta_ref": payload.acta_ref,
            }

        if not payload.activo:
            # Constancia ANTES de liberar nada en el almacén.
            async with self._conn.transaction():
                for f in pendientes:
                    await audit.append(
                        self._conn,
                        tenant_slug=self._tenant_slug,
                        service=_SERVICE,
                        action="worm.legal_hold_levantamiento_solicitado",
                        object_type="file",
                        object_ref=str(f["file_id"]) if f["file_id"] else payload.expediente_id,
                        actor=actor,
                        payload=_base(f),
                    )

        minio = get_minio()
        copias: dict[uuid.UUID, list[str]] = {}
        try:
            for f in pendientes:
                copias[f["id"]] = await self._aplicar_hold_almacen(minio, f, payload.activo)
        except Exception as exc:
            if not payload.activo:
                await self._asentar_fallo_levantamiento(payload, actor, exc)
            raise

        accion = "worm.legal_hold_fijado" if payload.activo else "worm.legal_hold_levantado"
        async with self._conn.transaction():
            for f in pendientes:
                await self._worm_repo.set_legal_hold(f["id"], activo=payload.activo)
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action=accion,
                    object_type="file",
                    object_ref=str(f["file_id"]) if f["file_id"] else payload.expediente_id,
                    actor=actor,
                    payload={**_base(f), "copias_alcanzadas": copias[f["id"]]},
                )
        return LegalHoldResponse(
            expediente_id=payload.expediente_id,
            activo=payload.activo,
            objetos_afectados=len(pendientes),
            objetos_sin_cambio=len(filas) - len(pendientes),
        )

    async def _aplicar_hold_almacen(
        self, minio: Minio, fila: asyncpg.Record, activo: bool
    ) -> list[str]:
        """Aplica/levanta el hold en el objeto primario (`version_id` exacta) y,
        para el AIP, en su réplica. Devuelve las copias alcanzadas
        (`bucket/key`). La réplica es opcional por diseño (best-effort al crear
        el AIP): su AUSENCIA (`NoSuchKey`/`NoSuchBucket`) se tolera; cualquier
        otro error aborta el acto (reintentable) para no declarar un freeze que
        deja una copia sin bloquear."""
        op = "enable_object_legal_hold" if activo else "disable_object_legal_hold"
        await getattr(minio, op)(fila["bucket"], fila["object_key"], version_id=fila["version_id"])
        alcanzadas = [f"{fila['bucket']}/{fila['object_key']}"]
        if fila["tipo"] == "aip":
            client, bucket = replica_target(minio, self._tenant_slug)
            try:
                await getattr(client, op)(bucket, fila["object_key"])
                alcanzadas.append(f"{bucket}/{fila['object_key']}")
            except S3Error as exc:
                if exc.code not in _REPLICA_AUSENTE:
                    raise
                logger.info(
                    "Sin réplica del AIP que alcanzar (bucket=%s, key=%s): %s",
                    bucket,
                    fila["object_key"],
                    exc.code,
                )
        return alcanzadas

    async def _asentar_fallo_levantamiento(
        self, payload: LegalHoldRequest, actor: str | None, exc: Exception
    ) -> None:
        """Constancia (best-effort) de que el levantamiento falló en el almacén:
        la fila sigue en `legal_hold=true` y el acto es reintentable."""
        try:
            async with self._conn.transaction():
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="worm.legal_hold_levantamiento_fallido",
                    object_type="expediente",
                    object_ref=payload.expediente_id,
                    actor=actor,
                    payload={
                        "expediente_id": payload.expediente_id,
                        "motivo": payload.motivo,
                        "acta_ref": payload.acta_ref,
                        "error": type(exc).__name__,
                    },
                )
        except Exception:  # noqa: BLE001 — la excepción original es la que importa.
            logger.exception("No se pudo asentar el fallo del levantamiento de legal hold")


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
