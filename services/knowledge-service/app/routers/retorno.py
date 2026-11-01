# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Retorno del proveedor (ADR-045 §4).

`POST /ppi/v1/{slug}/content` no pasa por el gateway. La firma HMAC de la
entidad es el gate: ventana de 300 s y nonce de un solo uso en Redis. Si
la firma cuadra, el localizador vuelve a pasar la barrera y los bytes
salen de document-service.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from orpycamcp_common.tenant import slug_valido

from app.clients import documento
from app.clients.documento import DocumentoError
from app.core import clave_retorno, contenido, database
from app.core.firma_retorno import comprobar, plazo_nonce
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

router = APIRouter(tags=["retorno"])

_TOPE_CUERPO = 8192


class Problema(Exception):
    """Error del contrato, en `application/problem+json`."""

    def __init__(self, status: int, code: str, title: str) -> None:
        self.status = status
        self.code = code
        self.title = title


def respuesta_problema(_request: Request, exc: Problema) -> JSONResponse:
    return JSONResponse(
        {
            "type": "about:blank",
            "title": exc.title,
            "status": exc.status,
            "code": exc.code,
        },
        status_code=exc.status,
        media_type="application/problem+json",
    )


def _rechazar(status: int, code: str, title: str) -> Problema:
    return Problema(status, code, title)


async def exigir_firma_proveedor(request: Request, slug: str) -> None:
    """Comprueba la firma y reclama el nonce. Sin eso la ruta no sigue."""
    if not slug_valido(slug):
        raise _rechazar(401, "signature_invalid", "La firma no cuadra")
    cuerpo = await request.body()
    if len(cuerpo) > _TOPE_CUERPO:
        raise _rechazar(400, "invalid_request", "El pedido no tiene la forma acordada")
    kid = request.headers.get("PPI-Key-Id", "").strip()
    slot = clave_retorno.ranura(kid)
    if slot is None:
        raise _rechazar(401, "signature_invalid", "La firma no cuadra")
    async with database.acquire_tenant(slug) as conn:
        secreto = await clave_retorno.abrir(conn, slug, slot)
    if secreto is None:
        raise _rechazar(401, "signature_invalid", "La firma no cuadra")
    ahora = time.time()
    motivo = comprobar(
        cuerpo,
        metodo=request.method,
        ruta=request.url.path,
        cabeceras=request.headers,
        secreto=secreto,
        ahora=ahora,
    )
    if motivo is not None:
        raise _rechazar(401, "signature_invalid", "La firma no cuadra")
    marca = request.headers.get("PPI-Timestamp", "").strip()
    nonce = request.headers.get("PPI-Nonce", "").strip()
    try:
        primero = await get_redis().set(
            f"orpycamcp:ppi:nonce:{slug}:{nonce}",
            "1",
            nx=True,
            ex=plazo_nonce(marca, ahora),
        )
    except Exception:
        logger.warning("no se pudo reclamar el nonce")
        raise _rechazar(503, "unavailable", "No se pudo comprobar el nonce") from None
    if not primero:
        raise _rechazar(401, "replayed_nonce", "Ese nonce ya se usó")
    request.state.cuerpo_firmado = cuerpo


@router.post(
    "/ppi/v1/{slug}/content",
    dependencies=[Depends(exigir_firma_proveedor)],
)
async def content(request: Request, slug: str) -> Response:
    """Los bytes del localizador, o el problema que dice por qué no salen."""
    try:
        datos = json.loads(request.state.cuerpo_firmado)
    except (json.JSONDecodeError, UnicodeError):
        raise _rechazar(400, "invalid_request", "El pedido no tiene la forma acordada") from None
    pedido = contenido.pedido(datos)
    if isinstance(pedido, contenido.Rechazo):
        raise _rechazar(pedido.status, pedido.code, pedido.title)
    locator, sha = pedido
    async with database.acquire_tenant(slug) as conn:
        pieza = await contenido.resolver(conn, slug, locator)
    if isinstance(pieza, contenido.Rechazo):
        raise _rechazar(pieza.status, pieza.code, pieza.title)
    try:
        octetos = await documento.leer_anexo(slug, pieza.anexo_id)
    except DocumentoError:
        logger.warning("document-service no entregó el anexo")
        raise _rechazar(503, "unavailable", "El contenido no está disponible") from None
    if octetos is None:
        raise _rechazar(410, "gone", "Ya no se transmite")
    huella = hashlib.sha256(octetos).hexdigest()
    if not hmac.compare_digest(huella, sha):
        raise _rechazar(409, "content_mismatch", "La huella no cuadra")
    return Response(
        content=octetos,
        media_type="application/octet-stream",
        headers={"PPI-Content-SHA256": huella},
    )
