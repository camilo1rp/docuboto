# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Servicio del archivo físico (E17, ADR-017).

Topología de ubicaciones (con ruta derivada), unidades de conservación con
signatura topográfica derivada de la ruta + código, y vínculo N:M expediente↔unidad.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import asyncpg
from fastapi import HTTPException, status

from app.repositories.archive_repository import ArchiveRepository
from app.repositories.fisico_repository import FisicoRepository
from app.schemas.fisico import (
    ExpedienteEnUnidad,
    ExpedienteUnidadLink,
    FuidResponse,
    FuidRow,
    MovimientoResponse,
    PrestamoCreate,
    PrestamoResponse,
    UbicacionCreate,
    UbicacionResponse,
    UnidadCreate,
    UnidadDeExpediente,
    UnidadResponse,
)

_FUID_NS = "urn:orpycamcp:fuid:v1"


def _fuid_to_xml(fuid: FuidResponse) -> str:
    root = ET.Element(f"{{{_FUID_NS}}}fuid", attrib={"generado": fuid.generado_ts.isoformat()})
    for r in fuid.rows:
        e = ET.SubElement(root, f"{{{_FUID_NS}}}registro", attrib={"orden": str(r.orden)})
        ET.SubElement(e, f"{{{_FUID_NS}}}expediente").text = r.expediente_code
        ET.SubElement(e, f"{{{_FUID_NS}}}asunto").text = r.asunto
        ET.SubElement(e, f"{{{_FUID_NS}}}serie").text = r.serie_code or ""
        ET.SubElement(e, f"{{{_FUID_NS}}}signatura").text = r.signatura or ""
        ET.SubElement(e, f"{{{_FUID_NS}}}unidad").text = r.unidad_tipo
        ET.SubElement(e, f"{{{_FUID_NS}}}folios").text = str(r.num_folios or "")
        ET.SubElement(e, f"{{{_FUID_NS}}}soporte").text = r.soporte or ""
        ET.SubElement(e, f"{{{_FUID_NS}}}fechaInicial").text = r.fecha_inicial.isoformat()
        ET.SubElement(e, f"{{{_FUID_NS}}}fechaFinal").text = (
            r.fecha_final.isoformat() if r.fecha_final else ""
        )
    return ET.canonicalize(ET.tostring(root, encoding="unicode"))


def _conflict(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error": "conflict", "detail": detail, "status": 409},
    )


def _not_found(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "detail": detail, "status": 404},
    )


class FisicoService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._repo = FisicoRepository(conn, tenant_slug)
        self._archive = ArchiveRepository(conn, tenant_slug)

    # -- ubicaciones ---------------------------------------------------

    async def create_ubicacion(self, payload: UbicacionCreate) -> UbicacionResponse:
        try:
            row = await self._repo.insert_ubicacion(payload.model_dump())
        except asyncpg.UniqueViolationError:
            raise _conflict(f"Ya existe una ubicación con código '{payload.codigo}'")
        except asyncpg.ForeignKeyViolationError:
            raise _not_found("Ubicación padre no encontrada")
        return await self._ubicacion_response(row)

    async def get_ubicacion(self, ubicacion_id: uuid.UUID) -> UbicacionResponse:
        row = await self._repo.get_ubicacion(ubicacion_id)
        if row is None:
            raise _not_found(f"Ubicación {ubicacion_id} no encontrada")
        return await self._ubicacion_response(row)

    async def list_ubicaciones(
        self, *, parent_id: uuid.UUID | None, activo: bool | None
    ) -> list[UbicacionResponse]:
        rows = await self._repo.list_ubicaciones(parent_id=parent_id, activo=activo)
        return [UbicacionResponse(**dict(r)) for r in rows]

    async def _ubicacion_response(self, row) -> UbicacionResponse:
        path = await self._repo.ubicacion_path(row["id"])
        return UbicacionResponse(**dict(row), ruta=" / ".join(path))

    # -- unidades ------------------------------------------------------

    async def create_unidad(self, payload: UnidadCreate) -> UnidadResponse:
        data = payload.model_dump()
        signatura = None
        if payload.ubicacion_id is not None:
            ub = await self._repo.get_ubicacion(payload.ubicacion_id)
            if ub is None:
                raise _not_found("Ubicación no encontrada")
            path = await self._repo.ubicacion_path(payload.ubicacion_id)
            signatura = "-".join([*path, payload.codigo])
        data["signatura"] = signatura
        try:
            row = await self._repo.insert_unidad(data)
        except asyncpg.UniqueViolationError:
            raise _conflict(f"Signatura topográfica duplicada: '{signatura}'")
        except asyncpg.ForeignKeyViolationError:
            raise _not_found("Ubicación o unidad padre no encontrada")
        return UnidadResponse(**dict(row))

    async def get_unidad(self, unidad_id: uuid.UUID) -> UnidadResponse:
        row = await self._repo.get_unidad(unidad_id)
        if row is None:
            raise _not_found(f"Unidad {unidad_id} no encontrada")
        return UnidadResponse(**dict(row))

    async def list_unidades(
        self, *, ubicacion_id: uuid.UUID | None, tipo: str | None
    ) -> list[UnidadResponse]:
        rows = await self._repo.list_unidades(ubicacion_id=ubicacion_id, tipo=tipo)
        return [UnidadResponse(**dict(r)) for r in rows]

    # -- vínculo N:M ---------------------------------------------------

    async def link_expediente(self, unidad_id: uuid.UUID, payload: ExpedienteUnidadLink) -> None:
        if await self._repo.get_unidad(unidad_id) is None:
            raise _not_found(f"Unidad {unidad_id} no encontrada")
        try:
            await self._repo.link_expediente(
                expediente_id=payload.expediente_id,
                unidad_id=unidad_id,
                folio_inicio=payload.folio_inicio,
                folio_fin=payload.folio_fin,
            )
        except asyncpg.UniqueViolationError:
            raise _conflict("El expediente ya está vinculado a esta unidad")
        except asyncpg.ForeignKeyViolationError:
            raise _not_found("Expediente no encontrado")

    async def units_of_expediente(self, expediente_id: uuid.UUID) -> list[UnidadDeExpediente]:
        rows = await self._repo.units_of_expediente(expediente_id)
        return [UnidadDeExpediente(**dict(r)) for r in rows]

    async def expedientes_in_unit(
        self, unidad_id: uuid.UUID, *, user_id: uuid.UUID | None = None
    ) -> list[ExpedienteEnUnidad]:
        # No-read-up (RF-SEG-08): listar los expedientes de una unidad revela
        # la EXISTENCIA de los clasificados/reservados a un no-lector (Ley
        # 1712/2014 art. 19), igual que cualquier otro listado — filtro por
        # fila, mismo patrón que `generate_fuid` más abajo. La ruta gemela
        # `units_of_expediente` (dónde está UN expediente dado su id) es
        # custodia ortogonal y no se toca aquí.
        max_clearance = await self._archive.caller_clearance(user_id)
        rows = await self._repo.expedientes_in_unit(unidad_id, max_clearance=max_clearance)
        return [ExpedienteEnUnidad(**dict(r)) for r in rows]

    # -- préstamos -----------------------------------------------------

    def _prestamo_response(self, row) -> PrestamoResponse:
        hoy = datetime.now(tz=UTC).date()
        vencido = row["estado"] == "prestado" and row["fecha_devolucion_esperada"] < hoy
        return PrestamoResponse(
            id=row["id"],
            unidad_id=row["unidad_id"],
            prestado_a=row["prestado_a"],
            prestado_por=row["prestado_por"],
            fecha_prestamo=row["fecha_prestamo"],
            fecha_devolucion_esperada=row["fecha_devolucion_esperada"],
            fecha_devolucion_real=row["fecha_devolucion_real"],
            estado=row["estado"],
            vencido=vencido,
        )

    async def create_prestamo(
        self, unidad_id: uuid.UUID, payload: PrestamoCreate, performed_by: str | None
    ) -> PrestamoResponse:
        if await self._repo.get_unidad(unidad_id) is None:
            raise _not_found(f"Unidad {unidad_id} no encontrada")
        if await self._repo.active_loan(unidad_id) is not None:
            raise _conflict("La unidad ya está prestada")
        row = await self._repo.insert_prestamo(
            unidad_id=unidad_id,
            prestado_a=payload.prestado_a,
            prestado_por=payload.prestado_por,
            fecha_devolucion_esperada=payload.fecha_devolucion_esperada,
        )
        await self._repo.insert_movimiento(
            unidad_id=unidad_id,
            accion="RETIRADO",
            ubicacion_before=None,
            ubicacion_after=None,
            performed_by=performed_by,
        )
        return self._prestamo_response(row)

    async def devolver_prestamo(
        self, prestamo_id: uuid.UUID, performed_by: str | None
    ) -> PrestamoResponse:
        existing = await self._repo.get_prestamo(prestamo_id)
        if existing is None:
            raise _not_found(f"Préstamo {prestamo_id} no encontrado")
        row = await self._repo.devolver_prestamo(prestamo_id)
        if row is None:
            raise _conflict("El préstamo ya estaba devuelto")
        await self._repo.insert_movimiento(
            unidad_id=row["unidad_id"],
            accion="DEVUELTO",
            ubicacion_before=None,
            ubicacion_after=None,
            performed_by=performed_by,
        )
        return self._prestamo_response(row)

    async def list_prestamos(
        self, *, estado: str | None, unidad_id: uuid.UUID | None
    ) -> list[PrestamoResponse]:
        rows = await self._repo.list_prestamos(estado=estado, unidad_id=unidad_id)
        return [self._prestamo_response(r) for r in rows]

    async def list_movimientos(self, unidad_id: uuid.UUID) -> list[MovimientoResponse]:
        rows = await self._repo.list_movimientos(unidad_id)
        return [MovimientoResponse(**dict(r)) for r in rows]

    # -- FUID (RF-ARF-10) ----------------------------------------------

    async def generate_fuid(
        self,
        *,
        ubicacion_id: uuid.UUID | None,
        trd_serie_id: uuid.UUID | None,
        expediente_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        max_clearance: int | None = None,
    ) -> FuidResponse:
        # No-read-up (RF-SEG-08): el FUID expone asunto/serie/signatura del
        # expediente — es contenido, no metadato de custodia (defensa en
        # profundidad; `TransferenciaService.fuid` ya aplica un 404 total
        # aguas arriba para la puerta single-expediente, pero las puertas
        # multi-expediente de `fisico.py` solo tienen este filtro por fila).
        #
        # `max_clearance` (E12 H-I): por defecto `None` -- la ruta VIVA
        # (llamante humano vía `fuid()`/`generate_fuid_xml`) resuelve el
        # clearance REAL de `user_id`, sin cambios de comportamiento. Un
        # llamante interno que congela el acta de entrega en un acto de
        # CUSTODIA (`TransferenciaService.recibir`, ortogonal a lectura --
        # mismo criterio que `fixity`, `core.clearance` bloque (c)) pasa
        # `CLEARANCE_SIN_RESTRICCION` explícito, sin resolver `caller_clearance`.
        if max_clearance is None:
            max_clearance = await self._archive.caller_clearance(user_id)
        rows = await self._repo.fuid_inventory(
            ubicacion_id=ubicacion_id,
            trd_serie_id=trd_serie_id,
            expediente_id=expediente_id,
            max_clearance=max_clearance,
        )
        fuid_rows = [FuidRow(orden=i, **dict(r)) for i, r in enumerate(rows, start=1)]
        return FuidResponse(generado_ts=datetime.now(tz=UTC), total=len(fuid_rows), rows=fuid_rows)

    async def generate_fuid_xml(
        self,
        *,
        ubicacion_id: uuid.UUID | None,
        trd_serie_id: uuid.UUID | None,
        user_id: uuid.UUID | None = None,
    ) -> str:
        fuid = await self.generate_fuid(
            ubicacion_id=ubicacion_id, trd_serie_id=trd_serie_id, user_id=user_id
        )
        return _fuid_to_xml(fuid)
