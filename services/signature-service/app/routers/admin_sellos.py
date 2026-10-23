# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Consulta de administración de los sellos de archivo XAdES-LTA (C-6, ADR-042).

La cadena de ``ArchiveTimeStamp`` de una firma (``firma_sello_archivo``) vale mientras viva el
certificado de su TSA y el de la CA de esa TSA. El job de renovación la mantiene, pero cuando no puede
—TSA por vencer, sin revocación de la TSA, renovación rechazada, paso WORM pendiente— lo deja anotado
(``ultimo_aviso``) y asienta UNA vez. Esta consulta es lo que lo hace visible a una persona: un plazo que
corre sin que nadie lo vea es el defecto de B9.

Gate: ``USUA_PERM_ADMIN`` a nivel 3, el mismo de la administración de archive-service. Lectura de un
administrador sobre evidencia de conservación: deja un asiento por petición
(``firma.sellos_archivo_consultados``, «auditar todo egreso masivo»).

Clearance: NO se ocultan filas —el administrador sin nivel no vería que un sello reservado va a
caducar: pérdida de prueba invisible—, se acota el CAMPO (``objeto_tipo``/``objeto_id`` en ``null`` y
``objeto_reservado=true``), decidido en la misma consulta. Respuesta content-free.
"""

from __future__ import annotations

import uuid
from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.core.clearance import get_actor, require_permission, resolve_caller_clearance
from app.core.config import settings
from app.core.database import get_tenant_conn
from app.repositories.firma_repository import SITUACIONES_SELLO, FirmaRepository
from app.schemas.firma import SelloArchivoListItem, SellosArchivoResumen

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "signature-service"

router = APIRouter(
    prefix="/api/v1/signature/admin/sellos-archivo",
    tags=["admin-sellos-archivo"],
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)


async def _asentar_consulta(
    conn: asyncpg.Connection, request: Request, actor: uuid.UUID, payload: dict[str, object]
) -> None:
    if not _AUDIT_AVAILABLE:  # pragma: no cover
        return
    await _audit.append(
        conn,
        tenant_slug=request.headers.get("X-Tenant-Slug"),
        service=_SERVICE,
        action="firma.sellos_archivo_consultados",
        object_type="firma_sello_archivo",
        object_ref=None,
        actor=str(actor),
        payload=payload,
        canal=_audit.CANAL_PETICION,
    )


@router.get("", response_model=list[SelloArchivoListItem])
async def listar_sellos_archivo(
    request: Request,
    response: Response,
    situacion: Annotated[list[str] | None, Query(description="Repetible. Omitido = toda anomalía.")] = None,
    dias: Annotated[int | None, Query(ge=1, le=3650)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[SelloArchivoListItem]:
    """Últimos eslabones de las cadenas de sellos de archivo con alguna anomalía, del más urgente al
    menos (``vence_at ASC, firma_id``). ``X-Total-Count`` sale del MISMO filtro que las filas."""
    desconocidas = sorted(set(situacion or []) - set(SITUACIONES_SELLO))
    if desconocidas:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "situacion_desconocida",
                "detail": f"situación debe ser una de {list(SITUACIONES_SELLO)}: {desconocidas}",
                "status": 422,
            },
        )
    margen = dias if dias is not None else settings.lta_renovacion_margen_dias
    clearance = await resolve_caller_clearance(conn, actor)
    repo = FirmaRepository(conn)
    try:
        filas, total = await repo.listar_sellos_archivo(
            situaciones=sorted(set(situacion)) if situacion else None,
            dias=margen,
            clearance=clearance,
            limit=size,
            offset=(page - 1) * size,
        )
    except asyncpg.UndefinedTableError:
        # Un tenant sin la migración de sellos de archivo no tiene cadena que vigilar.
        filas, total = [], 0
    await _asentar_consulta(
        conn, request, actor,
        {
            "situaciones": sorted(set(situacion)) if situacion else list(SITUACIONES_SELLO),
            "dias": margen, "page": page, "size": size, "total": total, "devueltas": len(filas),
        },
    )
    response.headers["X-Total-Count"] = str(total)
    return [SelloArchivoListItem.model_validate(dict(f)) for f in filas]


@router.get("/resumen", response_model=SellosArchivoResumen)
async def resumen_sellos_archivo(
    request: Request,
    dias: Annotated[int | None, Query(ge=1, le=3650)] = None,
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> SellosArchivoResumen:
    """Recuentos por situación para la tarjeta de administración (una fila puede contar en varias)."""
    margen = dias if dias is not None else settings.lta_renovacion_margen_dias
    try:
        cuentas = await FirmaRepository(conn).resumen_sellos_archivo(dias=margen)
    except asyncpg.UndefinedTableError:
        cuentas = dict.fromkeys(SITUACIONES_SELLO, 0)
    await _asentar_consulta(conn, request, actor, {"resumen": cuentas, "dias": margen})
    return SellosArchivoResumen(**cuentas, margen_dias=margen)
