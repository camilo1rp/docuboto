# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Ingesta de preparación para el asistente de radicación."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator

from knowledge.core import ingesta, paso
from knowledge.core.clearance import get_actor, require_asistente_habilitado, require_permission
from knowledge.core.config import settings
from knowledge.core.database import get_tenant_conn
from knowledge.core.salida_http import transporte_de_consulta

router = APIRouter(prefix="/api/v1/knowledge", tags=["ingesta"])

_GATES = (
    Depends(require_asistente_habilitado),
    Depends(require_permission("PERM_RADI")),
    Depends(get_actor),
)

_HTTP = {
    "ok": 200,
    "bandera": 403,
    "local": 403,
    "suspendido": 403,
    "file_not_found": 404,
    "scope_not_found": 404,
    "not_indexed": 409,
    "content_mismatch": 409,
    "too_large": 413,
    "ref": 422,
    "cuerpo": 422,
    "nivel": 403,
}


class CrearPedido(BaseModel):
    file_id: uuid.UUID


class OpcionCampo(BaseModel):
    value: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=256)


class CampoExtracto(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    kind: Literal["choice", "text", "number", "date"]
    label: str | None = Field(default=None, min_length=1, max_length=256)
    choices: tuple[OpcionCampo, ...] | None = None

    @model_validator(mode="after")
    def _opciones_cerradas(self) -> CampoExtracto:
        if self.kind == "choice":
            if not self.choices:
                raise ValueError("Un campo choice nombra al menos una opción")
        elif self.choices is not None:
            raise ValueError("Solo un campo choice lleva opciones")
        return self


class ExtraerPedido(BaseModel):
    external_ref: str = Field(min_length=1, max_length=256)
    fields: tuple[CampoExtracto, ...] = Field(min_length=1, max_length=100)


def _slug(request: Request) -> str:
    return request.headers.get("X-Tenant-Slug") or ""


def _cliente() -> Any:
    return paso.destino_de(settings.ppi_provider_url, transporte_de_consulta())


def _falla(resultado: ingesta.Resultado) -> HTTPException:
    code = resultado.motivo
    http = _HTTP.get(code, status.HTTP_503_SERVICE_UNAVAILABLE)
    return HTTPException(
        status_code=http,
        detail={"error": code, "detail": _detalle(code), "status": http},
    )


def _detalle(code: str) -> str:
    if code == "too_large":
        return "El archivo supera el tope del proveedor."
    if code == "file_not_found":
        return "No existe el archivo solicitado."
    if code == "not_indexed":
        return "El archivo aún no está indexado."
    if code == "scope_not_found":
        return "No hay un objeto ingerido con esa referencia."
    if code in {"bandera", "local", "suspendido"}:
        return "La entidad no transmite al proveedor ahora."
    return "No se pudo completar la ingesta de preparación."


@router.post("/ingesta", dependencies=_GATES)
async def crear(
    pedido: CrearPedido,
    request: Request,
    conn: Any = Depends(get_tenant_conn),
) -> dict[str, Any]:
    resultado = await ingesta.crear(conn, _slug(request), _cliente(), pedido.file_id)
    if not resultado.sale or resultado.cuerpo is None:
        raise _falla(resultado)
    return resultado.cuerpo


@router.post("/ingesta/extract", dependencies=_GATES)
async def extraer(
    pedido: ExtraerPedido,
    request: Request,
    conn: Any = Depends(get_tenant_conn),
) -> dict[str, Any]:
    resultado = await ingesta.extraer(
        conn, _slug(request), _cliente(), pedido.model_dump(mode="json", exclude_none=True)
    )
    if not resultado.sale or resultado.cuerpo is None:
        raise _falla(resultado)
    return resultado.cuerpo


@router.get("/ingesta/{external_ref}", dependencies=_GATES)
async def estado(
    external_ref: str,
    request: Request,
    conn: Any = Depends(get_tenant_conn),
) -> dict[str, Any]:
    resultado = await ingesta.estado(conn, _slug(request), _cliente(), external_ref)
    if not resultado.sale or resultado.cuerpo is None:
        raise _falla(resultado)
    return resultado.cuerpo


@router.delete(
    "/ingesta/{external_ref}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_GATES,
)
async def borrar(
    external_ref: str,
    request: Request,
    conn: Any = Depends(get_tenant_conn),
) -> None:
    resultado = await ingesta.borrar(conn, _slug(request), _cliente(), external_ref)
    if not resultado.sale:
        raise _falla(resultado)
