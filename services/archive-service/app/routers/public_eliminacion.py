# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Publicidad previa de la eliminación documental — RF-TRD-07, fase 2 (E04).

**Público y sin autenticar**, por el camino que el gateway ya enruta a este
servicio (`/api/v1/public/archive/…`), sin tocar la lista de rutas.

**El chokepoint es el ESTADO del lote**, no un interruptor de despliegue.
Deliberadamente NO cuelga de `public_interop_enabled` —la cosecha OAI/CMIS—
porque la política no es la misma: publicar el registro de correspondencia es
una decisión de la entidad; publicar el inventario de lo que va a eliminar es
una **obligación del procedimiento**. Atarlas al mismo interruptor dejaría a una
entidad sin poder eliminar por no haber activado una cosecha que no le interesa.
Un chokepoint se reutiliza cuando la política es la misma, no porque el nombre
encaje.

Es público **todo lote que llegó a publicarse**, y lo sigue siendo después:
avalado, ejecutado y también **anulado**. La constancia de qué se eliminó no
puede desaparecer al eliminarlo, y la de que algo NO se eliminó le importa a
quien leyó que iba a eliminarse — con su motivo a la vista. Un lote en
`borrador` **no existe** para estos endpoints: 404, no 403 — la respuesta es
indistinguible de la de un identificador inventado.
"""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import APIRouter, Depends

from app.core.database import get_db_conn
from app.schemas.archive import LoteEliminacionPublico
from app.services.archive_service import ArchiveService

router = APIRouter(prefix="/api/v1/public/archive/eliminacion", tags=["public"])


@router.get(
    "/{tenant}/lotes",
    response_model=list[LoteEliminacionPublico],
    summary="Inventarios de eliminación publicados (público, sin autenticar)",
)
async def listar_publicos(
    tenant: str,
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[LoteEliminacionPublico]:
    # El tenant viaja en la RUTA, no en una cabecera: aquí no hay sesión de la
    # que derivarlo, y es el mismo patrón que el OAI público y el callback
    # postal.
    service = ArchiveService(conn, tenant)
    return await service.list_lotes_publicos()


@router.get(
    "/{tenant}/lotes/{lote_id}",
    response_model=LoteEliminacionPublico,
    summary="Inventario publicado de un lote (público, sin autenticar)",
)
async def ver_publico(
    tenant: str,
    lote_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> LoteEliminacionPublico:
    service = ArchiveService(conn, tenant)
    return await service.get_lote_publico(lote_id)
