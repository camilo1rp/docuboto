# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Vistos buenos secuenciales (paridad legado).

Cadena ordenada de revisores; cada uno actúa solo en su turno (menor orden
pendiente). Un rechazo detiene la cadena. Estado global: en_revision | aprobado |
rechazado.
"""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import HTTPException, status

from app.repositories.visto_bueno_repository import VistoBuenoRepository
from app.schemas.visto_bueno import (
    CadenaCreate,
    CadenaResponse,
    VistoBuenoItem,
    VoBoDecision,
)


def _estado_global(items: list[VistoBuenoItem]) -> str:
    if any(i.estado == "rechazado" for i in items):
        return "rechazado"
    if items and all(i.estado == "aprobado" for i in items):
        return "aprobado"
    return "en_revision"


class VistoBuenoService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._repo = VistoBuenoRepository(conn, tenant_slug)

    async def crear(self, radicado_id: uuid.UUID, payload: CadenaCreate) -> CadenaResponse:
        if await self._repo.exists(radicado_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "cadena_existente",
                    "detail": "El radicado ya tiene una cadena de vistos buenos",
                    "status": 409,
                },
            )
        await self._repo.crear_cadena(radicado_id, payload.revisores)
        return await self.get(radicado_id)

    async def decidir(
        self, radicado_id: uuid.UUID, payload: VoBoDecision, actor: uuid.UUID | None
    ) -> CadenaResponse:
        active = await self._repo.active(radicado_id)
        if active is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "sin_pendiente",
                    "detail": "No hay visto bueno pendiente",
                    "status": 404,
                },
            )
        if actor is not None and active["revisor_id"] != actor:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "no_es_su_turno",
                    "detail": "El visto bueno corresponde a otro revisor",
                    "status": 403,
                },
            )
        estado = "aprobado" if payload.aprobar else "rechazado"
        await self._repo.decide(active["id"], estado, payload.comentario)
        return await self.get(radicado_id)

    async def get(self, radicado_id: uuid.UUID) -> CadenaResponse:
        rows = await self._repo.list(radicado_id)
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "sin_cadena",
                    "detail": "El radicado no tiene cadena de vistos buenos",
                    "status": 404,
                },
            )
        items = [
            VistoBuenoItem(
                orden=r["orden"],
                revisor_id=r["revisor_id"],
                estado=r["estado"],
                comentario=r["comentario"],
                decidido_at=r["decidido_at"],
            )
            for r in rows
        ]
        return CadenaResponse(radicado_id=radicado_id, estado=_estado_global(items), items=items)
