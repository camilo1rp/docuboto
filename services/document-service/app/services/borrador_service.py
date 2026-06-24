# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Plantillas y borradores de Salida (paridad legado).

Ciclo del borrador: crear (opc. desde plantilla) → editar → aprobar → RADICAR
(genera el radicado real reutilizando la radicación). Hasta radicar no hay número.
"""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import HTTPException

from app.repositories.borrador_repository import BorradorRepository
from app.schemas.borrador import (
    BorradorCreate,
    BorradorResponse,
    BorradorUpdate,
    PlantillaCreate,
    PlantillaResponse,
)
from app.schemas.document import DocumentCreate, DocumentResponse


def _bo(row) -> BorradorResponse:
    return BorradorResponse(**dict(row))


def _not_found(detail: str) -> HTTPException:
    return HTTPException(
        status_code=404, detail={"error": "not_found", "detail": detail, "status": 404}
    )


class BorradorService:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._repo = BorradorRepository(conn)

    # -- plantillas ----------------------------------------------------

    async def crear_plantilla(self, payload: PlantillaCreate) -> PlantillaResponse:
        row = await self._repo.insert_plantilla(payload.model_dump())
        return PlantillaResponse(**dict(row))

    async def listar_plantillas(self, activo: bool | None) -> list[PlantillaResponse]:
        return [PlantillaResponse(**dict(r)) for r in await self._repo.list_plantillas(activo)]

    async def borrar_plantilla(self, plantilla_id: uuid.UUID) -> None:
        if not await self._repo.delete_plantilla(plantilla_id):
            raise _not_found(f"Plantilla {plantilla_id} no encontrada")

    # -- borradores ----------------------------------------------------

    async def crear(
        self, payload: BorradorCreate, creado_por: uuid.UUID | None
    ) -> BorradorResponse:
        cuerpo = payload.cuerpo
        if cuerpo is None and payload.plantilla_id is not None:
            pl = await self._repo.get_plantilla(payload.plantilla_id)
            if pl is None:
                raise _not_found("Plantilla no encontrada")
            cuerpo = pl["cuerpo"]
        row = await self._repo.insert_borrador(
            {
                "doc_type": payload.doc_type,
                "subject": payload.subject,
                "dest_dept": payload.dest_dept,
                "cuerpo": cuerpo,
                "plantilla_id": payload.plantilla_id,
                "creado_por": creado_por,
            }
        )
        return _bo(row)

    async def get(self, borrador_id: uuid.UUID) -> BorradorResponse:
        row = await self._repo.get_borrador(borrador_id)
        if row is None:
            raise _not_found(f"Borrador {borrador_id} no encontrado")
        return _bo(row)

    async def listar(self, estado: str | None) -> list[BorradorResponse]:
        return [_bo(r) for r in await self._repo.list_borradores(estado)]

    async def _require_editable(self, borrador_id: uuid.UUID):
        row = await self._repo.get_borrador(borrador_id)
        if row is None:
            raise _not_found(f"Borrador {borrador_id} no encontrado")
        if row["estado"] == "radicado":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "borrador_radicado",
                    "detail": "El borrador ya fue radicado",
                    "status": 409,
                },
            )
        return row

    async def editar(self, borrador_id: uuid.UUID, payload: BorradorUpdate) -> BorradorResponse:
        await self._require_editable(borrador_id)
        row = await self._repo.update_borrador(borrador_id, payload.model_dump(exclude_unset=True))
        return _bo(row)

    async def aprobar(self, borrador_id: uuid.UUID) -> BorradorResponse:
        row = await self._require_editable(borrador_id)
        if row["estado"] != "borrador":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "estado_invalido",
                    "detail": f"No se puede aprobar desde '{row['estado']}'",
                    "status": 409,
                },
            )
        return _bo(await self._repo.set_estado(borrador_id, "aprobado"))

    async def radicar(
        self, borrador_id: uuid.UUID, document_service, registered_by: uuid.UUID | None
    ) -> DocumentResponse:
        """Convierte el borrador en radicado real (reutiliza la radicación)."""
        row = await self._repo.get_borrador(borrador_id)
        if row is None:
            raise _not_found(f"Borrador {borrador_id} no encontrado")
        if row["estado"] == "radicado":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "borrador_radicado",
                    "detail": "El borrador ya fue radicado",
                    "status": 409,
                },
            )
        payload = DocumentCreate(
            doc_type=row["doc_type"],
            subject=row["subject"],
            dest_dept=row["dest_dept"],
            observations=row["cuerpo"],
            metadata={"from_borrador": str(borrador_id)},
        )
        result = await document_service.create(payload, registered_by)
        await self._repo.set_estado(borrador_id, "radicado", radicado_id=result.id)
        return result
