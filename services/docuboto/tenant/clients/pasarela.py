# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Llamadas internas a knowledge-service para el acto de configuración.

Este servicio no habla con el proveedor. La pasarela es el único punto de
paso, y estas rutas no las enruta el gateway.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from tenant.core.config import settings

_CAPACIDADES = "/api/v1/internal/intelligence/capabilities"
_ACEPTACION = "/api/v1/internal/intelligence/acceptance"
_PURGA = "/api/v1/internal/intelligence/purge"
_INVENTARIO = "/api/v1/internal/intelligence/inventory"


class PasarelaError(Exception):
    def __init__(self, status: int, error: str) -> None:
        self.status = status
        self.error = error


async def capacidades(slug: str) -> dict[str, Any]:
    """La declaración vigente, ya comprobada por la pasarela."""
    return await _pedir("GET", _CAPACIDADES, slug)


async def aceptar(slug: str, nivel: int, sha256: str) -> None:
    """Registra que el administrador aceptó esa huella para el nivel."""
    await _pedir("POST", _ACEPTACION, slug, json={"level": nivel, "sha256": sha256})


async def inventario(slug: str) -> dict[str, Any]:
    """Cuántos radicados conserva el proveedor. También con la entidad en local."""
    return await _pedir("GET", _INVENTARIO, slug)


async def purgar(
    slug: str,
    *,
    actor: uuid.UUID,
    reason: str,
    levels: list[int] | None,
) -> dict[str, Any]:
    """Pide el borrado. ``accepted`` dice si el proveedor lo tomó."""
    cuerpo: dict[str, Any] = {"reason": reason, "actor": str(actor)}
    if levels is not None:
        cuerpo["levels"] = levels
    return await _pedir("POST", _PURGA, slug, json=cuerpo)


def _config() -> tuple[str, str]:
    base = settings.knowledge_service_url.strip().rstrip("/")
    token = settings.intelligence_internal_token.strip()
    if not base or not token:
        raise PasarelaError(503, "pasarela_no_configurada")
    return base, token


async def _pedir(
    method: str,
    ruta: str,
    slug: str,
    *,
    json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base, token = _config()
    headers = {"X-Internal-Token": token, "X-Tenant-Slug": slug}
    try:
        async with httpx.AsyncClient(timeout=10.0) as cliente:
            respuesta = await cliente.request(
                method, f"{base}{ruta}", headers=headers, json=json
            )
    except httpx.HTTPError:
        raise PasarelaError(503, "pasarela_no_disponible") from None
    if respuesta.status_code >= 400:
        raise PasarelaError(respuesta.status_code, _error(respuesta))
    if respuesta.status_code == 204 or not respuesta.content:
        return {}
    try:
        cuerpo = respuesta.json()
    except ValueError:
        raise PasarelaError(503, "pasarela_no_disponible") from None
    if not isinstance(cuerpo, dict):
        raise PasarelaError(503, "pasarela_no_disponible")
    return cuerpo


def _error(respuesta: httpx.Response) -> str:
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return "pasarela_no_disponible"
    if isinstance(cuerpo, dict) and isinstance(cuerpo.get("detail"), dict):
        error = cuerpo["detail"].get("error")
        if isinstance(error, str):
            return error
    if isinstance(cuerpo, dict) and isinstance(cuerpo.get("error"), str):
        return cuerpo["error"]
    return "pasarela_no_disponible"
