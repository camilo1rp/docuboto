# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Retenciones legales por MOTIVO (C-5/C7, ADR-043; antes AREA-08-07).

Sustituye al acto único `fijar_legal_hold(activo=True|False)` del ``PreservationWormService``, que
manejaba UN booleano por objeto: levantar «con cualquier motivo» liberaba TODO, y un levantamiento
por valoración habría liberado también un hold judicial. Ahora:

- **Fijar** registra un hold (`preservacion_legal_hold`) y pone el legal hold en TODOS los objetos
  WORM del expediente que aún no lo tengan. NO exige objetos (se puede congelar lo que todavía no
  tiene AIP/índice: los objetos que nazcan después heredan el hold al protegerse).
- **Levantar** es POR `hold_id` y exige repetir el `motivo_tipo` (409 si no coincide): un acto no
  puede levantar el hold de otro. El almacén se libera SOLO cuando, tras el levantamiento, no queda
  ningún hold activo del expediente.

Todo bajo el advisory lock de sesión EXCLUSIVO `(tenant, expediente, "legal_hold")`; quien escribe
un objeto protegido toma la misma clave en modo compartido (`legal_hold_compartido`).

El ORDEN de cada sentido deja el error del lado conservador (el almacén no es transaccional):
- FIJAR: almacén primero (un exceso de hold es seguro), luego registro + filas + asiento en UNA tx.
- LEVANTAR: asiento de SOLICITUD primero (tx propia), luego almacén, luego registro + filas +
  asiento final en UNA tx. Si algo falla tras liberar, queda constancia de que se intentó y el hold
  sigue activo (el gate de disposición bloquea): reintentable, y nunca un objeto liberado sin
  ningún asiento.
"""

from __future__ import annotations

import logging
import uuid

import asyncpg
from miniopy_async import Minio, S3Error  # type: ignore[attr-defined]
from orpycamcp_common import audit

from app.core.config import settings
from app.core.constants import canal_del_acto
from app.core.minio_client import get_minio, verificar_legal_hold_aplicado
from app.repositories.legal_hold_repository import LegalHoldRepository
from app.repositories.preservacion_worm_repository import PreservacionWormRepository
from app.schemas.preservacion import (
    HoldFijarRequest,
    HoldFijarResponse,
    HoldItem,
    HoldLevantarRequest,
    HoldLevantarResponse,
)
from app.services.aip_replica_service import replica_target
from app.services.preservation_worm_service import (
    PreservationDisabledError,
    _worm_lock_key,
)

logger = logging.getLogger(__name__)

_SERVICE = "storage-service"
_MOTIVO_SISTEMA = "valoracion_pendiente"

# Códigos S3 que significan «esa copia no existe»: la réplica del AIP es best-effort al crearse.
_REPLICA_AUSENTE = frozenset({"NoSuchKey", "NoSuchBucket", "NoSuchVersion"})


class HoldNoEncontradoError(Exception):
    """No existe el hold (o no es de ese expediente)."""


class HoldMotivoNoCoincideError(Exception):
    """`motivo_tipo_esperado` no es el motivo del hold: un acto no levanta el hold de otro."""


class HoldYaLevantadoError(Exception):
    """El hold ya estaba levantado (se levanta una sola vez, sin vuelta)."""


class HoldActorRequeridoError(Exception):
    """Un hold que no es de sistema necesita una persona identificada detrás del acto."""


class LegalHoldService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug
        self._holds = LegalHoldRepository(conn, tenant_slug)
        self._worm_repo = PreservacionWormRepository(conn, tenant_slug)

    # -- lectura ---------------------------------------------------------

    async def listar(self, expediente_id: str) -> list[HoldItem]:
        return [_item(r) for r in await self._holds.list_for_expediente(expediente_id)]

    # -- fijar -----------------------------------------------------------

    async def fijar(
        self, payload: HoldFijarRequest, *, actor: str | None, actor_id: uuid.UUID | None
    ) -> HoldFijarResponse:
        if not settings.preservation_enabled:
            raise PreservationDisabledError(
                "La protección WORM está desactivada (PRESERVATION_ENABLED=false)"
            )
        if payload.motivo_tipo != _MOTIVO_SISTEMA and actor_id is None:
            raise HoldActorRequeridoError(
                "un hold judicial/disciplinario/fiscal/administrativo exige X-User-Id"
            )
        lock_key = _worm_lock_key(self._tenant_slug, payload.expediente_id, "legal_hold")
        await self._conn.execute("SELECT pg_advisory_lock($1)", lock_key)
        try:
            return await self._fijar_locked(payload, actor, actor_id)
        finally:
            await self._conn.execute("SELECT pg_advisory_unlock($1)", lock_key)

    async def _fijar_locked(
        self, payload: HoldFijarRequest, actor: str | None, actor_id: uuid.UUID | None
    ) -> HoldFijarResponse:
        if payload.motivo_tipo == _MOTIVO_SISTEMA:
            existente = await self._holds.valoracion_activa(payload.expediente_id)
            if existente is not None:
                # Idempotente: la barrida de archive reintenta (at-least-once).
                return HoldFijarResponse(hold_id=existente["id"], creado=False, objetos_afectados=0)

        filas = await self._worm_repo.list_for_expediente(payload.expediente_id)
        pendientes = [f for f in filas if not bool(f["legal_hold"])]

        copias: dict[uuid.UUID, list[str]] = {}
        if pendientes:
            minio = get_minio()
            for f in pendientes:
                copias[f["id"]] = await self._aplicar_hold_almacen(minio, f, activo=True)
                await verificar_legal_hold_aplicado(
                    minio, f["bucket"], f["object_key"], version_id=f["version_id"]
                )

        async with self._conn.transaction():
            hold = await self._holds.insert(
                expediente_id=payload.expediente_id,
                motivo_tipo=payload.motivo_tipo,
                fundamento=payload.fundamento,
                acta_ref=payload.acta_ref,
                fijado_por=actor_id,
            )
            for f in pendientes:
                await self._worm_repo.aplicar_estado_hold(f["id"], legal_hold=True, valorada=False)
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="worm.legal_hold_fijado",
                    object_type="file",
                    object_ref=str(f["file_id"]) if f["file_id"] else payload.expediente_id,
                    actor=actor,
                    payload={
                        "expediente_id": payload.expediente_id,
                        "hold_id": str(hold["id"]),
                        "motivo_tipo": payload.motivo_tipo,
                        "tipo": f["tipo"],
                        "worm_object_id": str(f["id"]),
                        "acta_ref": payload.acta_ref,
                        "copias_alcanzadas": copias[f["id"]],
                    },
                    canal=canal_del_acto(actor),
                )
            # El acto en sí, aunque no haya objetos que alcanzar (H2): la constancia del hold.
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="preservacion.legal_hold_fijado",
                object_type="expediente",
                object_ref=payload.expediente_id,
                actor=actor,
                payload={
                    "hold_id": str(hold["id"]),
                    "motivo_tipo": payload.motivo_tipo,
                    "fundamento": payload.fundamento,
                    "acta_ref": payload.acta_ref,
                    "objetos_afectados": len(pendientes),
                },
                canal=canal_del_acto(actor),
            )
        return HoldFijarResponse(hold_id=hold["id"], creado=True, objetos_afectados=len(pendientes))

    # -- levantar --------------------------------------------------------

    async def levantar(
        self,
        hold_id: uuid.UUID,
        payload: HoldLevantarRequest,
        *,
        actor: str | None,
        actor_id: uuid.UUID | None,
    ) -> HoldLevantarResponse:
        if not settings.preservation_enabled:
            raise PreservationDisabledError(
                "La protección WORM está desactivada (PRESERVATION_ENABLED=false)"
            )
        if payload.motivo_tipo_esperado != _MOTIVO_SISTEMA and actor_id is None:
            raise HoldActorRequeridoError(
                "levantar un hold judicial/disciplinario/fiscal/administrativo exige X-User-Id"
            )
        previo = await self._holds.get(hold_id)
        if previo is None:
            raise HoldNoEncontradoError(str(hold_id))
        lock_key = _worm_lock_key(self._tenant_slug, previo["expediente_id"], "legal_hold")
        await self._conn.execute("SELECT pg_advisory_lock($1)", lock_key)
        try:
            return await self._levantar_locked(hold_id, payload, actor, actor_id)
        finally:
            await self._conn.execute("SELECT pg_advisory_unlock($1)", lock_key)

    async def _levantar_locked(
        self,
        hold_id: uuid.UUID,
        payload: HoldLevantarRequest,
        actor: str | None,
        actor_id: uuid.UUID | None,
    ) -> HoldLevantarResponse:
        # Releído BAJO el lock: el estado que cuenta es el de ahora, no el de antes de esperarlo.
        hold = await self._holds.get(hold_id)
        if hold is None:
            raise HoldNoEncontradoError(str(hold_id))
        if hold["levantado_at"] is not None:
            raise HoldYaLevantadoError(str(hold_id))
        if hold["motivo_tipo"] != payload.motivo_tipo_esperado:
            raise HoldMotivoNoCoincideError(
                f"el hold {hold_id} es de otro motivo que el confirmado"
            )
        expediente_id = hold["expediente_id"]
        es_valoracion = hold["motivo_tipo"] == _MOTIVO_SISTEMA

        restantes = await self._holds.contar_activos_excepto(expediente_id, hold_id)
        filas = await self._worm_repo.list_for_expediente(expediente_id)
        # El almacén se libera solo si no queda NINGÚN otro hold: un levantamiento no libera el
        # hold de otro motivo.
        a_liberar = [f for f in filas if bool(f["legal_hold"])] if restantes == 0 else []

        def _base(f: asyncpg.Record) -> dict[str, object]:
            return {
                "expediente_id": expediente_id,
                "hold_id": str(hold_id),
                "motivo_tipo": hold["motivo_tipo"],
                "tipo": f["tipo"],
                "worm_object_id": str(f["id"]),
                "fundamento": payload.fundamento,
                "acta_ref": payload.acta_ref,
            }

        if a_liberar:
            # Constancia ANTES de liberar nada en el almacén.
            async with self._conn.transaction():
                for f in a_liberar:
                    await audit.append(
                        self._conn,
                        tenant_slug=self._tenant_slug,
                        service=_SERVICE,
                        action="worm.legal_hold_levantamiento_solicitado",
                        object_type="file",
                        object_ref=str(f["file_id"]) if f["file_id"] else expediente_id,
                        actor=actor,
                        payload=_base(f),
                        canal=canal_del_acto(actor),
                    )

        copias: dict[uuid.UUID, list[str]] = {}
        try:
            if a_liberar:
                minio = get_minio()
                for f in a_liberar:
                    copias[f["id"]] = await self._aplicar_hold_almacen(minio, f, activo=False)
        except Exception as exc:
            await self._asentar_fallo_levantamiento(hold, payload, actor, exc)
            raise

        async with self._conn.transaction():
            levantado = await self._holds.levantar(
                hold_id,
                levantado_por=actor_id,
                fundamento=payload.fundamento,
                acta_ref=payload.acta_ref,
            )
            if levantado is None:  # pragma: no cover — bajo el lock exclusivo no debe ocurrir
                raise HoldYaLevantadoError(str(hold_id))
            for f in a_liberar:
                await self._worm_repo.aplicar_estado_hold(
                    f["id"], legal_hold=False, valorada=es_valoracion
                )
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="worm.legal_hold_levantado",
                    object_type="file",
                    object_ref=str(f["file_id"]) if f["file_id"] else expediente_id,
                    actor=actor,
                    payload={**_base(f), "copias_alcanzadas": copias[f["id"]]},
                    canal=canal_del_acto(actor),
                )
            if es_valoracion and restantes > 0:
                # Queda otro hold (p. ej. judicial): el objeto sigue con hold, pero la valoración YA
                # se aplicó -> deja de estar «pendiente» (CHECK ck_pwo_diferida_exige_hold intacto).
                for f in filas:
                    if f.get("retencion_origen") == "valoracion_pendiente":
                        await self._worm_repo.aplicar_estado_hold(
                            f["id"], legal_hold=True, valorada=True
                        )
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="preservacion.legal_hold_levantado",
                object_type="expediente",
                object_ref=expediente_id,
                actor=actor,
                payload={
                    "hold_id": str(hold_id),
                    "motivo_tipo": hold["motivo_tipo"],
                    "fundamento": payload.fundamento,
                    "acta_ref": payload.acta_ref,
                    "objetos_liberados_s3": len(a_liberar),
                    "holds_restantes": restantes,
                },
                canal=canal_del_acto(actor),
            )
        return HoldLevantarResponse(
            hold_id=hold_id, objetos_liberados_s3=len(a_liberar), holds_restantes=restantes
        )

    # -- almacén ---------------------------------------------------------

    async def _aplicar_hold_almacen(
        self, minio: Minio, fila: asyncpg.Record, *, activo: bool
    ) -> list[str]:
        """Aplica/levanta el hold en el objeto primario (`version_id` exacta) y, para el AIP, en su
        réplica. Devuelve las copias alcanzadas (`bucket/key`). La réplica es opcional por diseño
        (best-effort al crear el AIP): su AUSENCIA (`NoSuchKey`/`NoSuchBucket`) se tolera; cualquier
        otro error aborta el acto (reintentable) para no declarar un freeze que deja una copia sin
        bloquear."""
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
        self,
        hold: asyncpg.Record,
        payload: HoldLevantarRequest,
        actor: str | None,
        exc: Exception,
    ) -> None:
        """Constancia (best-effort) de que el levantamiento falló en el almacén: el hold sigue
        activo y el acto es reintentable."""
        try:
            async with self._conn.transaction():
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="worm.legal_hold_levantamiento_fallido",
                    object_type="expediente",
                    object_ref=hold["expediente_id"],
                    actor=actor,
                    payload={
                        "expediente_id": hold["expediente_id"],
                        "hold_id": str(hold["id"]),
                        "motivo_tipo": hold["motivo_tipo"],
                        "acta_ref": payload.acta_ref,
                        "error": type(exc).__name__,
                    },
                    canal=canal_del_acto(actor),
                )
        except Exception:  # noqa: BLE001 — la excepción original es la que importa.
            logger.exception("No se pudo asentar el fallo del levantamiento de legal hold")


def _item(r: asyncpg.Record) -> HoldItem:
    return HoldItem(
        hold_id=r["id"],
        expediente_id=r["expediente_id"],
        motivo_tipo=r["motivo_tipo"],
        fundamento=r["fundamento"],
        acta_ref=r["acta_ref"],
        fijado_por=r["fijado_por"],
        fijado_at=r["fijado_at"],
        levantado_por=r["levantado_por"],
        levantado_at=r["levantado_at"],
        levantado_fundamento=r["levantado_fundamento"],
        levantado_acta_ref=r["levantado_acta_ref"],
    )
