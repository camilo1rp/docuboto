# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Índice público de información clasificada y reservada por serie (C12, ADR-039).

**Público y sin autenticar**, por el camino que el gateway ya enruta a este servicio
(`/api/v1/public/archive/…`). Gemelo de `public_eliminacion.py` en su manejo del
tenant: va en la RUTA, `_tenant_operativo` decide si se sirve, y una entidad inexistente
devuelve LO MISMO que una sin clasificados (lista vacía) — sin oráculo de qué entidades
aloja la instalación. Una entidad suspendida SE SIRVE (ADR-035: solo lectura pública).

Es una **obligación legal** (Ley 1712/2014 art. 20), así que NO cuelga de
`public_interop_enabled` (la cosecha OAI/CMIS, decisión de la entidad).
"""

from __future__ import annotations

import csv
import io
import logging

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.clients.document_client import DocumentClientError
from app.core.database import get_db_conn
from app.routers.public_eliminacion import _tenant_operativo
from app.schemas.indice_clasificados import IndiceSeriePublico
from app.services.indice_clasificados_service import indice_publico

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/public/archive/indice-clasificados", tags=["public"])

_COLUMNAS = list(IndiceSeriePublico.model_fields)


def _csv_safe(valor: object) -> object:
    """Neutraliza la inyección de fórmulas CSV: el nombre de una serie es texto libre."""
    if isinstance(valor, str) and valor and valor[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + valor
    return valor


async def _filas(conn: asyncpg.Connection, tenant: str) -> list[IndiceSeriePublico]:
    if not await _tenant_operativo(conn, tenant):
        return []
    try:
        return await indice_publico(conn, tenant)
    except DocumentClientError as exc:
        logger.warning("índice de clasificados no disponible para %s: %s", tenant, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "indice_no_disponible",
                "detail": "El índice no se puede componer en este momento; reintente",
                "status": 503,
            },
        ) from exc


# El CSV va ANTES que `/{tenant}`: si no, éste captura «demo.csv» (un slug no lleva punto).
@router.get(
    "/{tenant}.csv",
    summary="Índice de información clasificada y reservada por serie, en CSV (público)",
)
async def indice_csv(
    tenant: str, conn: asyncpg.Connection = Depends(get_db_conn)
) -> Response:
    filas = await _filas(conn, tenant)
    buf = io.StringIO()
    escritor = csv.writer(buf)
    escritor.writerow(_COLUMNAS)
    for f in filas:
        d = f.model_dump()
        escritor.writerow(
            ["" if d[c] is None else _csv_safe(d[c]) for c in _COLUMNAS]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=indice-clasificados.csv"},
    )


@router.get(
    "/{tenant}",
    response_model=list[IndiceSeriePublico],
    summary="Índice de información clasificada y reservada por serie (público, sin autenticar)",
)
async def indice(
    tenant: str, conn: asyncpg.Connection = Depends(get_db_conn)
) -> list[IndiceSeriePublico]:
    return await _filas(conn, tenant)
