# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Retención legal (legal hold) de un expediente, POR MOTIVO (C-5 / C7, ADR-043, AREA-08-07).

Reemplaza el booleano por objeto (`POST /expedientes/{id}/legal-hold`, retirado sin
compatibilidad): «levantar» con cualquier motivo liberaba TODO, incluido un hold judicial.
Ahora cada acto es una fila del registro de storage (`preservacion_legal_hold`), se levanta POR
`hold_id` repitiendo el `motivo_tipo`, y el objeto solo se libera en S3 cuando no queda ningún
hold activo del expediente.

Archive decide el permiso (router) y la VISIBILIDAD del expediente (clearance en el `WHERE` de
la lectura: 404 neutro) y orquesta el acto en storage con token interno + identidad firmada. Si
storage falla no hay asiento de archive: el acto no ocurrió."""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import HTTPException, status
from orpycamcp_common import audit

from app.clients import storage_client
from app.repositories.archive_repository import ArchiveRepository
from app.schemas.retencion_legal import (
    LegalHoldFijado,
    LegalHoldFijarRequest,
    LegalHoldItem,
    LegalHoldLevantado,
    LegalHoldLevantarRequest,
)

_SERVICE = "archive-service"


def _no_encontrado(expediente_id: uuid.UUID) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "error": "expediente_not_found",
            "detail": f"Expediente {expediente_id} no encontrado",
            "status": 404,
        },
    )


def _hold_no_encontrado() -> HTTPException:
    # Mismo cuerpo para «no existe» y «es de otro expediente»: no es un oráculo de ids.
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "error": "hold_not_found",
            "detail": "No existe esa retención legal en el expediente",
            "status": 404,
        },
    )


def _desde_storage(exc: storage_client.StorageLegalHoldError) -> HTTPException:
    """Traduce el fallo de storage. 404/409/422 conservan su código (`motivo_no_coincide`,
    `hold_ya_levantado`, `hold_de_sistema`...); todo lo demás es 503 `storage_unavailable`."""
    if exc.status in (404, 409, 422):
        return HTTPException(
            status_code=exc.status,
            detail={
                "error": exc.code or "retencion_legal_rechazada",
                "detail": str(exc),
                "status": exc.status,
            },
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"error": "storage_unavailable", "detail": str(exc), "status": 503},
    )


class RetencionLegalService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug
        self._repo = ArchiveRepository(conn, tenant_slug)

    async def _expediente_visible(self, expediente_id: uuid.UUID, max_clearance: int):
        exp = await self._repo.get_expediente_by_id(expediente_id, max_clearance=max_clearance)
        if exp is None:
            raise _no_encontrado(expediente_id)
        return exp

    async def _holds(self, expediente_id: uuid.UUID) -> list[dict]:
        try:
            return await storage_client.listar_legal_holds(
                tenant_slug=self._tenant_slug, expediente_id=str(expediente_id)
            )
        except storage_client.StorageLegalHoldError as exc:
            raise _desde_storage(exc) from exc

    async def listar(self, expediente_id: uuid.UUID, max_clearance: int) -> list[LegalHoldItem]:
        await self._expediente_visible(expediente_id, max_clearance)
        return [LegalHoldItem.model_validate(h) for h in await self._holds(expediente_id)]

    async def fijar(
        self,
        expediente_id: uuid.UUID,
        payload: LegalHoldFijarRequest,
        max_clearance: int,
        *,
        actor: str,
        canal: str = "system",
    ) -> LegalHoldFijado:
        exp = await self._expediente_visible(expediente_id, max_clearance)
        if payload.motivo_tipo == "valoracion_pendiente":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "hold_de_sistema",
                    "detail": (
                        "'valoracion_pendiente' lo fija el sistema al proteger un fondo "
                        "acumulado vencido y lo levanta la valoración de la TVD"
                    ),
                    "status": 422,
                },
            )
        try:
            res = await storage_client.fijar_legal_hold(
                tenant_slug=self._tenant_slug,
                actor_id=actor,
                expediente_id=str(exp.id),
                motivo_tipo=payload.motivo_tipo,
                fundamento=payload.fundamento,
                acta_ref=payload.acta_ref,
            )
        except storage_client.StorageLegalHoldError as exc:
            raise _desde_storage(exc) from exc

        async with self._conn.transaction():
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.expediente_legal_hold_fijado",
                object_type="expediente",
                object_ref=exp.code,
                actor=actor,
                canal=canal,
                payload={
                    "hold_id": str(res["hold_id"]),
                    "motivo_tipo": payload.motivo_tipo,
                    "acta_ref": payload.acta_ref,
                    "objetos_afectados": int(res["objetos_afectados"]),
                },
            )
        return LegalHoldFijado(
            hold_id=uuid.UUID(str(res["hold_id"])),
            objetos_afectados=int(res["objetos_afectados"]),
        )

    async def levantar(
        self,
        expediente_id: uuid.UUID,
        hold_id: uuid.UUID,
        payload: LegalHoldLevantarRequest,
        max_clearance: int,
        *,
        actor: str,
        canal: str = "system",
    ) -> LegalHoldLevantado:
        exp = await self._expediente_visible(expediente_id, max_clearance)
        # El hold se busca ENTRE LOS DEL EXPEDIENTE: un hold_id ajeno no llega a storage y es
        # indistinguible de uno inexistente (404 neutro).
        hold = next(
            (h for h in await self._holds(expediente_id) if str(h.get("hold_id")) == str(hold_id)),
            None,
        )
        if hold is None:
            raise _hold_no_encontrado()
        if hold.get("motivo_tipo") == "valoracion_pendiente":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "hold_de_sistema",
                    "detail": (
                        "La retención 'valoracion_pendiente' solo la levanta la valoración "
                        "de la TVD por el Comité"
                    ),
                    "status": 409,
                },
            )
        if hold.get("levantado_at") is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "hold_ya_levantado",
                    "detail": "Esa retención legal ya fue levantada",
                    "status": 409,
                },
            )
        try:
            res = await storage_client.levantar_legal_hold(
                tenant_slug=self._tenant_slug,
                actor_id=actor,
                hold_id=hold_id,
                motivo_tipo_esperado=payload.motivo_tipo,
                fundamento=payload.fundamento,
                acta_ref=payload.acta_ref,
            )
        except storage_client.StorageLegalHoldError as exc:
            raise _desde_storage(exc) from exc

        async with self._conn.transaction():
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.expediente_legal_hold_levantado",
                object_type="expediente",
                object_ref=exp.code,
                actor=actor,
                canal=canal,
                payload={
                    "hold_id": str(hold_id),
                    "motivo_tipo": payload.motivo_tipo,
                    "acta_ref": payload.acta_ref,
                    "objetos_liberados_s3": int(res["objetos_liberados_s3"]),
                    "holds_restantes": int(res["holds_restantes"]),
                },
            )
        return LegalHoldLevantado(
            hold_id=hold_id,
            objetos_liberados_s3=int(res["objetos_liberados_s3"]),
            holds_restantes=int(res["holds_restantes"]),
        )
