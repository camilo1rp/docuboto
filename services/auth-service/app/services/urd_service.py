# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""URD y contexto activo de dependencia (E08)."""

from __future__ import annotations

import uuid

import asyncpg

from app.repositories.urd_repository import URDRepository
from app.schemas.urd import ContextResponse, URDCreate, URDResponse


def _to_urd(row: asyncpg.Record) -> URDResponse:
    return URDResponse(
        id=row["id"],
        user_id=row["user_id"],
        depe_id=row["depe_id"],
        rol_id=row["rol_id"],
        es_principal=row["es_principal"],
        is_active=row["is_active"],
        created_at=row["created_at"],
    )


class URDService:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._repo = URDRepository(conn)

    async def list(self, user_id: uuid.UUID) -> list[URDResponse]:
        return [_to_urd(r) for r in await self._repo.list_by_user(user_id)]

    async def create(self, user_id: uuid.UUID, payload: URDCreate) -> URDResponse:
        row = await self._repo.insert(
            user_id=user_id,
            depe_id=payload.depe_id,
            rol_id=payload.rol_id,
            es_principal=payload.es_principal,
        )
        return _to_urd(row)

    async def delete(self, user_id: uuid.UUID, urd_id: uuid.UUID) -> bool:
        return await self._repo.delete(user_id, urd_id)

    async def context(self, user_id: uuid.UUID) -> ContextResponse:
        active = await self._repo.get_active_depe(user_id)
        available = [_to_urd(r) for r in await self._repo.list_by_user(user_id)]
        return ContextResponse(user_id=user_id, active_depe_id=active, available_depts=available)

    async def switch(
        self, user_id: uuid.UUID, depe_id: int, *, executor_id: uuid.UUID | None
    ) -> ContextResponse | None:
        """Cambia la dependencia activa. None si el usuario no tiene URD activa allí.

        ADR-013: no reemite token; solo persiste el contexto y lo audita.
        """
        urd = await self._repo.get(user_id, depe_id)
        if urd is None or not urd["is_active"]:
            return None
        await self._repo.set_active_depe(user_id, depe_id)
        await self._repo.log_historico(
            executor_id=executor_id,
            target_id=user_id,
            accion="CONTEXT_SWITCH",
            permisos={"depe_id": depe_id},
        )
        return await self.context(user_id)
