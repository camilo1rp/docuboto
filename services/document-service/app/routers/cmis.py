# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Perfil CMIS 1.1 mínimo de lectura (E11, RF-INT-02) — Browser Binding.

`GET /api/v1/public/cmis/{tenant}` → repositoryInfo.
`GET /api/v1/public/cmis/{tenant}/root?cmisselector=children|object|content` →
navegación de la raíz / objeto por id / descarga del stream. Público por-path bajo
`/api/v1/public/` (el gateway no exige JWT, sin tocarlo). Solo radicados PÚBLICOS
(recorte único `_PUBLIC` en `OaiRepository`); un id reservado → 404 indistinguible.
"""

import re
from urllib.parse import quote

import asyncpg
from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from app.clients import storage_client
from app.core.database import tenant_schema
from app.core.public_audit import audit_public_access
from app.core.public_interop import require_public_interop
from app.repositories.oai_repository import OaiRepository
from app.services.cmis_service import CmisNotFound, CmisService

# Guard de interoperabilidad pública ANÓNIMA (E11): con el interruptor apagado —el
# default— estas rutas responden 404, indistinguible de una ruta inexistente. Ver
# `app/core/public_interop.py` para el razonamiento completo.
router = APIRouter(tags=["cmis"], dependencies=[Depends(require_public_interop)])

_MIME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]*/[A-Za-z0-9][A-Za-z0-9.+-]*$")


def _err(status_code: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"exception": code, "message": detail})


async def _release(pool: asyncpg.Pool, conn: asyncpg.Connection) -> None:
    """Resetea el search_path y SIEMPRE devuelve la conexión al pool (el reset puede
    fallar sobre una conexión rota, pero la liberación no debe saltarse — Seg #2)."""
    try:
        await conn.execute("SET search_path = public")
    finally:
        await pool.release(conn)


async def _acquire(request: Request, tenant: str):
    """(pool, conn) con el schema del tenant validado y EXISTENTE; None si no existe.
    Lanza HTTPException (via tenant_schema) si el slug es inválido. A prueba de
    excepciones: si algo falla tras `pool.acquire()`, libera antes de propagar (Seg #2)."""
    schema = tenant_schema(tenant)
    pool: asyncpg.Pool = request.app.state.db_pool
    conn = await pool.acquire()
    try:
        await conn.execute(f'SET search_path = "{schema}", public')
        exists = await conn.fetchval("SELECT to_regclass($1)", f'"{schema}".radicados')
        if exists is None:
            await _release(pool, conn)
            return None
        return pool, conn
    except BaseException:
        await _release(pool, conn)
        raise


def _content_disposition(filename: str) -> str:
    """RFC 6266 a prueba de inyección: `filename` lo controla quien subió el anexo;
    un `"`/CR/LF sin sanear rompe la cabecera (patrón de storage-service). Seg #1."""
    ascii_name = (
        (filename or "").encode("ascii", "ignore").decode("ascii")
        .replace('"', "").replace("\r", "").replace("\n", "").strip()
        or "download"
    )
    return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename or 'download')}"


def _safe_mime(mime: str | None) -> str:
    """Un mime_type con CR/LF u otro contenido no válido inyectaría la cabecera
    Content-Type → se acota a un patrón estricto (Seg #1)."""
    return mime if mime and _MIME_RE.match(mime) else "application/octet-stream"


@router.get("/api/v1/public/cmis/{tenant}", summary="CMIS repositoryInfo (E11)")
async def cmis_repository_info(tenant: str, request: Request) -> Response:
    try:
        acquired = await _acquire(request, tenant)
    except Exception:
        return _err(400, "invalidArgument", "Tenant inválido")
    if acquired is None:
        return _err(404, "objectNotFound", "Tenant inválido")
    pool, conn = acquired
    base_url = str(request.url).split("?", 1)[0]
    try:
        svc = CmisService(OaiRepository(conn), tenant=tenant, base_url=base_url)
        return JSONResponse(svc.repository_info())
    finally:
        await _release(pool, conn)


@router.get(
    "/api/v1/public/cmis/{tenant}/root",
    summary="CMIS getObject/getChildren/getContentStream (E11)",
)
async def cmis_root(
    tenant: str,
    request: Request,
    cmisselector: str = Query("children"),
    objectId: str | None = Query(None),
    maxItems: int = Query(100),
    skipCount: int = Query(0),
) -> Response:
    try:
        acquired = await _acquire(request, tenant)
    except Exception:
        return _err(400, "invalidArgument", "Tenant inválido")
    if acquired is None:
        return _err(404, "objectNotFound", "Tenant inválido")
    pool, conn = acquired

    # Resuelve todo lo que necesita la CONEXIÓN dentro del try/finally; el streaming
    # de bytes (que viene de storage-service, no de la BD) ocurre DESPUÉS de liberar
    # la conexión. `result` = (kind, payload).
    result: tuple[str, object]
    try:
        svc = CmisService(OaiRepository(conn), tenant=tenant)
        selector = cmisselector.lower()
        if selector == "children":
            result = ("json", await svc.get_children(max_items=maxItems, skip_count=skipCount))
        elif selector in ("object", "content"):
            if not objectId:
                result = ("err", _err(400, "invalidArgument", "objectId requerido"))
            elif selector == "object":
                try:
                    result = ("json", await svc.get_object(objectId))
                except CmisNotFound:
                    result = ("err", _err(404, "objectNotFound", "Objeto no encontrado"))
            else:  # content
                try:
                    result = ("content", await svc.get_content_anexo(objectId))
                except CmisNotFound:
                    result = ("err", _err(404, "objectNotFound", "Objeto sin contenido o no encontrado"))
        else:
            # CMIS: notSupported -> 405 (invalidArgument seria 400). Conf #5.
            result = ("err", _err(405, "notSupported", f"cmisselector '{cmisselector}' no soportado"))

        # Traza del acceso ANÓNIMO (best-effort, ver core/public_audit.py). Va DENTRO
        # del try, antes de liberar la conexión: es la única que tiene el search_path
        # del tenant. Se asienta también el 404/405 — un barrido de objectId que no
        # existen es exactamente lo que una entidad querría poder ver después.
        kind_previo = result[0]
        await audit_public_access(
            conn,
            tenant_slug=tenant,
            action=(
                "document.cmis_descarga_anexo"
                if selector == "content" and kind_previo == "content"
                else "document.cmis_consulta"
            ),
            object_type="cmis_request",
            object_ref=objectId,
            payload={
                "selector": selector,
                "resultado": kind_previo,
                **({"max_items": maxItems, "skip_count": skipCount} if selector == "children" else {}),
            },
        )
    finally:
        await _release(pool, conn)

    kind, payload = result
    if kind == "json":
        return JSONResponse(payload)
    if kind == "err":
        return payload  # type: ignore[return-value]
    return await _stream_content(tenant, payload)


async def _stream_content(tenant: str, anexo) -> Response:
    """Transmite los bytes del anexo público. La conexión de BD YA se liberó; el
    stream es un httpx a storage-service, cerrado por el BackgroundTask."""
    try:
        stream = await storage_client.open_content_stream(
            tenant_slug=tenant, file_id=anexo["file_id"]
        )
    except storage_client.StorageContentError as exc:
        return _err(status.HTTP_503_SERVICE_UNAVAILABLE, "storage", str(exc))
    if stream is None:  # purgado en storage → mismo 404 neutro
        return _err(404, "objectNotFound", "Objeto sin contenido o no encontrado")
    return StreamingResponse(
        stream.aiter_bytes(),
        media_type=_safe_mime(anexo["mime_type"]),
        headers={"Content-Disposition": _content_disposition(anexo["filename"])},
        background=BackgroundTask(stream.aclose),
    )
