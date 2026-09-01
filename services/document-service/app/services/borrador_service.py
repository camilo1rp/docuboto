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
from typing import Any

import asyncpg
from fastapi import HTTPException

from app.core.combinacion import combinar, desconocidos
from app.repositories.borrador_repository import BorradorRepository
from app.schemas.borrador import (
    BorradorCreate,
    BorradorResponse,
    BorradorUpdate,
    CombinarRequest,
    CombinarResponse,
    PlantillaCreate,
    PlantillaResponse,
)
from app.schemas.document import DocumentCreate, DocumentResponse
from app.services.contexto_combinacion import ContextoCombinacion


def _bo(row, faltantes: list[str] | None = None) -> BorradorResponse:
    return BorradorResponse(**dict(row), faltantes=faltantes or [])


def _marcadores_desconocidos(cuerpo: str) -> HTTPException:
    """422 con la lista, no un 'no válido' a secas.

    Quien escribe la plantilla necesita saber QUÉ marcador sobra: un rechazo sin
    la lista obliga a buscarlo a ojo en un cuerpo de varias páginas, y el camino
    corto entonces es borrar marcadores buenos hasta que pase.
    """
    faltan = desconocidos(cuerpo)
    return HTTPException(
        status_code=422,
        detail={
            "error": "marcador_desconocido",
            "detail": (
                "La plantilla usa marcadores que no existen en el vocabulario: "
                + ", ".join(f"*{m}*" for m in faltan)
            ),
            "marcadores": faltan,
            "status": 422,
        },
    )


def _not_found(detail: str) -> HTTPException:
    return HTTPException(
        status_code=404, detail={"error": "not_found", "detail": detail, "status": 404}
    )


class BorradorService:
    def __init__(
        self,
        conn: asyncpg.Connection,
        tenant_slug: str = "",
        tenant_client: Any = None,
    ) -> None:
        self._conn = conn
        self._repo = BorradorRepository(conn)
        self._slug = tenant_slug
        self._tenant_client = tenant_client

    # -- plantillas ----------------------------------------------------

    async def crear_plantilla(self, payload: PlantillaCreate) -> PlantillaResponse:
        # RF-PRO-01: el marcador desconocido se rechaza AL GUARDAR, no al usar.
        # En el legado no fallaba nunca: se quedaba impreso en el oficio que
        # recibía el ciudadano, y para entonces ya no había a quién preguntarle.
        if desconocidos(payload.cuerpo):
            raise _marcadores_desconocidos(payload.cuerpo)
        row = await self._repo.insert_plantilla(payload.model_dump())
        return PlantillaResponse(**dict(row))

    async def listar_plantillas(self, activo: bool | None) -> list[PlantillaResponse]:
        return [PlantillaResponse(**dict(r)) for r in await self._repo.list_plantillas(activo)]

    async def combinar_plantilla(
        self,
        plantilla_id: uuid.UUID,
        payload: CombinarRequest,
        *,
        max_clearance: int,
        funcionario: dict[str, Any] | None = None,
    ) -> CombinarResponse:
        """Devuelve el cuerpo de la plantilla YA combinado, con sus faltantes.

        Existe porque la pantalla necesita enseñar el texto resuelto en el
        editor antes de guardar nada, y la combinación no puede bajar al
        navegador: un marcador resuelto en el cliente es un dato que el cliente
        puede cambiar, y el documento que da fe no puede depender de eso (D-05).
        """
        pl = await self._repo.get_plantilla(plantilla_id)
        if pl is None:
            raise _not_found("Plantilla no encontrada")
        contexto = await ContextoCombinacion(self._conn, self._slug).construir(
            radicado_padre_id=payload.radicado_padre_id,
            max_clearance=max_clearance,
            dependencia_codigo=payload.dest_dept_code,
            funcionario=funcionario,
            tenant_client=self._tenant_client,
        )
        resultado = combinar(pl["cuerpo"], contexto)
        return CombinarResponse(cuerpo=resultado.texto, faltantes=resultado.faltantes)

    async def borrar_plantilla(self, plantilla_id: uuid.UUID) -> None:
        if not await self._repo.delete_plantilla(plantilla_id):
            raise _not_found(f"Plantilla {plantilla_id} no encontrada")

    # -- borradores ----------------------------------------------------

    async def crear(
        self,
        payload: BorradorCreate,
        creado_por: uuid.UUID | None,
        *,
        max_clearance: int,
        funcionario: dict[str, Any] | None = None,
    ) -> BorradorResponse:
        """Crea el borrador y, si viene de plantilla, COMBINA los campos.

        `max_clearance` es obligatorio y sin default a propósito: el contexto de
        combinación lee el radicado padre, y un default que significara «sin
        restricción» sería fail-OPEN — el mismo modo de fallo que dejó
        `get/set_disposition` sin gate.
        """
        cuerpo = payload.cuerpo
        faltantes: list[str] = []

        if cuerpo is None and payload.plantilla_id is not None:
            pl = await self._repo.get_plantilla(payload.plantilla_id)
            if pl is None:
                raise _not_found("Plantilla no encontrada")
            # Aquí estaba la brecha: `cuerpo = pl["cuerpo"]` copiaba la plantilla
            # LITERAL, marcadores incluidos. La plantilla era texto muerto.
            contexto = await ContextoCombinacion(self._conn, self._slug).construir(
                radicado_padre_id=payload.radicado_padre_id,
                max_clearance=max_clearance,
                dependencia_codigo=payload.dest_dept_code,
                funcionario=funcionario,
                tenant_client=self._tenant_client,
            )
            resultado = combinar(pl["cuerpo"], contexto)
            cuerpo, faltantes = resultado.texto, resultado.faltantes

        row = await self._repo.insert_borrador(
            {
                "doc_type": payload.doc_type,
                "subject": payload.subject,
                "dest_dept": payload.dest_dept,
                "cuerpo": cuerpo,
                "plantilla_id": payload.plantilla_id,
                "radicado_padre_id": payload.radicado_padre_id,
                "creado_por": creado_por,
            }
        )
        return _bo(row, faltantes)

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
        # La aprobación es REQUISITO, y hasta ahora solo la exigía el navegador.
        #
        # El ciclo documentado en la cabecera de este módulo es crear → editar →
        # aprobar → RADICAR, y la pantalla de borradores lo respeta
        # (`canRadicar = estado === "aprobado"`). Pero el servicio solo
        # comprobaba que no estuviera ya radicado, así que un `POST
        # /borradores/{id}/radicar` directo saltaba la aprobación y consumía el
        # número igualmente. Un control que vive solo en el cliente no es un
        # control: el cliente es la parte hostil (D-05).
        #
        # No rompe nada de lo que hoy funciona — la única interfaz que llama a
        # esta ruta no la ofrece sin aprobar — y convierte la marca de
        # aprobación en lo que su nombre dice que es.
        if row["estado"] != "aprobado":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "borrador_sin_aprobar",
                    "detail": (
                        "El borrador debe aprobarse antes de radicarlo: radicar "
                        "consume un número inmutable."
                    ),
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
