# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Importación interoperable de radicados (E11, RF-INT-05).

`POST /api/v1/import` — AUTENTICADO (no público), gate `USUA_PERM_EXPEDIENTE` +
**no-write-up por clearance** (no se ingiere un radicado por encima del clearance del
importador). Recibe el paquete ZIP de INT-01 como **cuerpo crudo** (`Content-Type:
application/zip`, sin multipart → sin dependencia extra), **verifica la fixity SHA-256
de cada binario y valida el esquema ANTES de ingerir** (todo-o-nada, sin estado
parcial), reingiere preservando el número de radicado, y audita el acto.
"""

import asyncio
import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.clearance import require_permission, resolve_caller_clearance
from app.core.database import get_tenant_conn
from app.core.operacion_pesada import OperacionPesadaOcupadaError, una_operacion_pesada
from app.services.import_service import ImportService, PackageValidationError

router = APIRouter(prefix="/api/v1/import", tags=["import"])

_TENANT_HEADER = "X-Tenant-Slug"
_USER_HEADER = "X-User-Id"
_MAX_UPLOAD = 512 * 1024 * 1024  # 512 MiB de ZIP comprimido (guarda de subida)
# Plazo TOTAL para recibir el cuerpo mientras se sostiene el cupo global: sin él un
# cliente con 1 byte cada pocos segundos retenía el único cupo y bloqueaba import/export
# de TODOS los tenants. ~1,7 MiB/s mínimo para un ZIP de 512 MiB.
_PLAZO_LECTURA_S = 300.0


def _get_registered_by(request: Request) -> uuid.UUID | None:
    raw = request.headers.get(_USER_HEADER)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


class _PaqueteDemasiadoGrande(Exception):
    pass


class _CuerpoLento(Exception):
    pass


async def _leer_cuerpo_acotado(request: Request) -> bytes:
    """Lee el cuerpo cortando en cuanto supera el tope.

    `await request.body()` bufferiza TODO antes de poder compararlo con
    `_MAX_UPLOAD`: el tope llegaba cuando la memoria ya estaba gastada.
    """
    declarado = request.headers.get("content-length")
    if declarado and declarado.isdigit() and int(declarado) > _MAX_UPLOAD:
        raise _PaqueteDemasiadoGrande
    partes: list[bytes] = []
    total = 0
    async for trozo in request.stream():
        total += len(trozo)
        if total > _MAX_UPLOAD:
            raise _PaqueteDemasiadoGrande
        partes.append(trozo)
    return b"".join(partes)


@router.post(
    "",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
    summary="Importar un paquete interoperable de radicados (E11, RF-INT-05)",
)
async def import_paquete(
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict:
    tenant_slug = request.headers.get(_TENANT_HEADER, "")
    actor = _get_registered_by(request)
    clearance = await resolve_caller_clearance(conn, actor)

    try:
        # El cupo se toma ANTES de leer el cuerpo: lo que agota la RAM es
        # bufferizar el ZIP, no validarlo.
        async with una_operacion_pesada():
            try:
                async with asyncio.timeout(_PLAZO_LECTURA_S):
                    zip_bytes = await _leer_cuerpo_acotado(request)
            except TimeoutError:
                raise _CuerpoLento from None
            svc = ImportService(conn, tenant_slug)
            return await svc.import_package(
                zip_bytes=zip_bytes, max_clearance=clearance, actor=actor
            )
    except OperacionPesadaOcupadaError:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": "30"},
            detail={
                "error": "operacion_ocupada",
                "detail": "Hay otra importación o exportación en curso; reintente en unos segundos.",
                "status": 429,
            },
        )
    except _CuerpoLento:
        raise HTTPException(
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
            detail={
                "error": "cuerpo_demasiado_lento",
                "detail": "El paquete no se recibió dentro del plazo; reintente con mejor caudal.",
                "status": 408,
            },
        )
    except _PaqueteDemasiadoGrande:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error": "paquete_demasiado_grande", "detail": "ZIP excede el máximo", "status": 413},
        )
    except PackageValidationError as exc:
        # Fallo de validación (esquema o fixity) → 422; NO se ingirió nada.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "paquete_invalido", "detail": exc.detail, "status": 422},
        )
