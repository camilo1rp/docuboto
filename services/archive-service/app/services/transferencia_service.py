# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Transferencias documentales (E12).

Ciclo `preparada → enviada → recibida | rechazada`. Precondición: el expediente
debe estar `closed`. Al **recibir** se congela el expediente (`transferred`,
ya no admite documentos). El FUID (E17) acompaña la transferencia como entregable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import asyncpg
from fastapi import HTTPException, status

from app.repositories.archive_repository import ArchiveRepository
from app.repositories.index_repository import IndexRepository
from app.repositories.transferencia_repository import TransferenciaRepository
from app.schemas.fisico import FuidResponse
from app.schemas.transferencia import (
    TransferenciaCreate,
    TransferenciaDecision,
    TransferenciaResponse,
)
from app.services.fisico_service import FisicoService

_VALID = {
    "preparada": {"enviada"},
    "enviada": {"recibida", "rechazada"},
    "recibida": set(),
    "rechazada": set(),
}


def _resp(row) -> TransferenciaResponse:
    return TransferenciaResponse(**dict(row))


class TransferenciaService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._repo = TransferenciaRepository(conn, tenant_slug)
        self._archive = ArchiveRepository(conn, tenant_slug)
        self._index_repo = IndexRepository(conn)
        self._conn = conn
        self._tenant = tenant_slug

    async def create(
        self, payload: TransferenciaCreate, creada_por: str | None
    ) -> TransferenciaResponse:
        exp = await self._archive.get_expediente_by_id(payload.expediente_id)
        if exp is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": str(payload.expediente_id),
                    "status": 404,
                },
            )
        if exp.status != "closed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "expediente_not_closed",
                    "detail": "Solo se transfiere un expediente cerrado",
                    "status": 409,
                },
            )
        row = await self._repo.insert(
            {
                "tipo": payload.tipo,
                "expediente_id": payload.expediente_id,
                "ubicacion_origen_id": payload.ubicacion_origen_id,
                "ubicacion_destino_id": payload.ubicacion_destino_id,
                "observacion": payload.observacion,
                "creada_por": creada_por,
            }
        )
        return _resp(row)

    async def _require(self, transferencia_id: uuid.UUID, target: str):
        existing = await self._repo.get(transferencia_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "transferencia_not_found",
                    "detail": str(transferencia_id),
                    "status": 404,
                },
            )
        if target not in _VALID.get(existing["estado"], set()):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "invalid_status_transition",
                    "detail": f"No se puede pasar de '{existing['estado']}' a '{target}'",
                    "status": 409,
                },
            )
        return existing

    async def enviar(self, transferencia_id: uuid.UUID) -> TransferenciaResponse:
        await self._require(transferencia_id, "enviada")
        row = await self._repo.update_estado(
            transferencia_id,
            {"estado": "enviada", "enviada_at": datetime.now(tz=UTC)},
        )
        return _resp(row)

    async def recibir(
        self, transferencia_id: uuid.UUID, decidida_por: str | None
    ) -> TransferenciaResponse:
        existing = await self._require(transferencia_id, "recibida")
        # El índice electrónico firmado es el instrumento que da fe de
        # integridad del expediente para su transferencia (Acuerdo AGN
        # 001/2024 art. 4.3.2.4) — recibir la transferencia congela el
        # expediente (`transferred`), así que exige el mismo requisito que
        # `ArchiveService.transfer_expediente` (E06 Inc.1).
        current_indice = await self._index_repo.get_current(existing["expediente_id"])
        if current_indice is None or current_indice["estado"] != "firmado":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "indice_no_firmado",
                    "detail": (
                        "El expediente no puede transferirse sin índice electrónico "
                        "firmado (Acuerdo AGN 001/2024 art. 4.3.2.4)"
                    ),
                    "status": 409,
                },
            )
        row = await self._repo.update_estado(
            transferencia_id,
            {
                "estado": "recibida",
                "recibida_at": datetime.now(tz=UTC),
                "decidida_por": decidida_por,
            },
        )
        # Congelación: el expediente pasa a transferred (no admite documentos).
        await self._archive.transition_expediente(existing["expediente_id"], status="transferred")
        return _resp(row)

    async def rechazar(
        self,
        transferencia_id: uuid.UUID,
        payload: TransferenciaDecision,
        decidida_por: str | None,
    ) -> TransferenciaResponse:
        existing = await self._require(transferencia_id, "rechazada")
        row = await self._repo.update_estado(
            transferencia_id,
            {
                "estado": "rechazada",
                "decidida_por": decidida_por,
                "observacion": payload.observacion or existing["observacion"],
            },
        )
        return _resp(row)

    async def list(self, *, estado: str | None, tipo: str | None) -> list[TransferenciaResponse]:
        rows = await self._repo.list(estado=estado, tipo=tipo)
        return [_resp(r) for r in rows]

    async def get(self, transferencia_id: uuid.UUID) -> TransferenciaResponse:
        row = await self._repo.get(transferencia_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "transferencia_not_found",
                    "detail": str(transferencia_id),
                    "status": 404,
                },
            )
        return _resp(row)

    async def fuid(self, transferencia_id: uuid.UUID) -> FuidResponse:
        row = await self._repo.get(transferencia_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "transferencia_not_found",
                    "detail": str(transferencia_id),
                    "status": 404,
                },
            )
        return await FisicoService(self._conn, self._tenant).generate_fuid(
            ubicacion_id=None, trd_serie_id=None, expediente_id=row["expediente_id"]
        )
