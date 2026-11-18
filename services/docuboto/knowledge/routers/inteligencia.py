# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Rutas internas que tenant-service usa para el acto de configuración.

No las enruta el gateway. Leen la declaración firmada y ordenan un borrado.
No son una consulta del funcionario: el gate es el token interno, y el
cliente PPI solo lo toca el punto de paso.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from orpycamcp_common.tenant import slug_valido
from pydantic import BaseModel, Field

from knowledge.core import aceptacion, paso, salida_http
from knowledge.core.config import settings
from knowledge.core.database import get_tenant_conn
from knowledge.core.internal_auth import require_internal_token

router = APIRouter(
    prefix="/api/v1/internal/intelligence",
    tags=["intelligence-internal"],
    dependencies=[Depends(require_internal_token)],
)

_SHA = r"^[0-9a-f]{64}$"


class AceptacionPedido(BaseModel):
    level: Literal[1, 2, 3]
    sha256: str = Field(pattern=_SHA)


class PurgaPedido(BaseModel):
    reason: Literal["withdrawn", "host_request"]
    actor: uuid.UUID
    levels: list[Literal[1, 2, 3]] | None = None


def _cliente() -> Any:
    return paso.destino_de(settings.ppi_provider_url, salida_http.transporte_de_consulta())


def _falla(code: int, error: str, detail: str) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": error, "detail": detail, "status": code},
    )


@router.get("/capabilities")
async def capabilities(
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict[str, object]:
    """La declaración vigente, ya comprobada. No incluye la firma."""
    declaracion = await aceptacion.leer(conn, _slug(request), _cliente())
    if not declaracion.ok or declaracion.vista is None:
        raise _traducir(declaracion.motivo)
    return declaracion.vista


@router.post("/acceptance", status_code=status.HTTP_204_NO_CONTENT)
async def acceptance(
    pedido: AceptacionPedido,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    """Anota la huella del nivel. Levanta la suspensión si ya están todos."""
    motivo = await aceptacion.aceptar(
        conn, _slug(request), _cliente(), nivel=pedido.level, sha256=pedido.sha256
    )
    if motivo:
        raise _traducir(motivo)


@router.post("/purge")
async def purge(
    pedido: PurgaPedido,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict[str, object]:
    """Borra en el proveedor lo de unos niveles, o todo lo de la entidad."""
    if pedido.reason == "withdrawn" and pedido.levels is not None:
        raise _falla(422, "nivel", "El retiro de la entidad no lleva niveles")
    if pedido.reason == "host_request" and not pedido.levels:
        raise _falla(422, "nivel", "Deshabilitar un nivel indica cuál")
    if pedido.levels is not None and len(set(pedido.levels)) != len(pedido.levels):
        raise _falla(422, "nivel", "Un nivel no se repite")
    niveles = None if pedido.levels is None else tuple(pedido.levels)
    if pedido.reason == "host_request" and pedido.levels:
        await aceptacion.olvidar(conn, list(pedido.levels))
    resultado = await paso.purgar(
        conn,
        _slug(request),
        _cliente(),
        razon=pedido.reason,
        actor=pedido.actor,
        niveles=niveles,
    )
    ref = None if resultado.cuerpo is None else resultado.cuerpo.get("purge_ref")
    return {"accepted": resultado.sale, "purge_ref": ref, "motivo": resultado.motivo}


@router.get("/inventory")
async def inventory(
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict[str, object]:
    """Cuántos radicados conserva el proveedor. También con la entidad en local.

    No transmite registros. Si no se puede leer, 503: la pantalla lo dice en
    vez de mostrar un cero.
    """
    resultado = await paso.contar_radicados(conn, _slug(request), _cliente())
    cuerpo = resultado.cuerpo
    if not resultado.sale or not isinstance(cuerpo, dict):
        raise _falla(
            503,
            "inventario_no_disponible",
            "No se pudo leer cuántos radicados conserva el proveedor",
        )
    return {"records": cuerpo["records"], "complete": cuerpo["complete"]}


def _slug(request: Request) -> str:
    """El slug que ``get_tenant_conn`` ya validó para esta petición."""
    slug = request.headers.get("X-Tenant-Slug", "")
    if not slug_valido(slug):
        raise _falla(400, "invalid_tenant", "Falta la entidad de la llamada")
    return slug


def _traducir(motivo: str) -> HTTPException:
    if motivo in ("destino", "proveedor", "clave"):
        return _falla(503, "capacidades_no_disponibles", "No se pudo leer la declaración")
    if motivo == "distinta":
        return _falla(409, "declaracion_distinta", "La declaración del proveedor cambió")
    return _falla(409, "capacidades_invalidas", "La declaración del proveedor no se pudo comprobar")
