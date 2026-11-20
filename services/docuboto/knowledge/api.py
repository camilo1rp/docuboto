# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lo que tenant pide a la pasarela de inteligencia para el acto de configuración.

Leen la declaración firmada del proveedor y ordenan un borrado. Eran rutas
`/api/v1/internal/intelligence/*` selladas con token interno; en un solo
proceso son llamadas a función (ADR-047 §1). El cliente PPI solo lo toca la
pasarela: tenant no habla con el proveedor.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from knowledge.core import aceptacion, paso, salida_http
from knowledge.core.config import settings
from knowledge.core.database import acquire_tenant

_SHA = re.compile(r"^[0-9a-f]{64}$")
_NIVELES = (1, 2, 3)
_RAZONES = ("withdrawn", "host_request")


class IntelligenceError(Exception):
    """La pasarela no pudo atender. `status` y `error` dicen por qué."""

    def __init__(self, status: int, error: str) -> None:
        super().__init__(error)
        self.status = status
        self.error = error


def _cliente() -> Any:
    return paso.destino_de(settings.ppi_provider_url, salida_http.transporte_de_consulta())


def _traducir(motivo: str) -> IntelligenceError:
    if motivo in ("destino", "proveedor", "clave"):
        return IntelligenceError(503, "capacidades_no_disponibles")
    if motivo == "distinta":
        return IntelligenceError(409, "declaracion_distinta")
    return IntelligenceError(409, "capacidades_invalidas")


@asynccontextmanager
async def _tenant_conn(tenant_slug: str) -> AsyncIterator[asyncpg.Connection]:
    try:
        async with acquire_tenant(tenant_slug) as conn:
            yield conn
    except ValueError:
        raise IntelligenceError(400, "invalid_tenant") from None
    except (asyncpg.PostgresError, OSError):
        raise IntelligenceError(503, "pasarela_no_disponible") from None


async def capabilities(*, tenant_slug: str) -> dict[str, Any]:
    """La declaración vigente, ya comprobada. No incluye la firma."""
    async with _tenant_conn(tenant_slug) as conn:
        declaracion = await aceptacion.leer(conn, tenant_slug, _cliente())
    if not declaracion.ok or declaracion.vista is None:
        raise _traducir(declaracion.motivo)
    return declaracion.vista


async def accept(*, tenant_slug: str, level: int, sha256: str) -> None:
    """Anota la huella aceptada para el nivel. Levanta la suspensión si ya están todos."""
    if level not in _NIVELES or not _SHA.match(sha256):
        raise IntelligenceError(422, "declaracion_invalida")
    async with _tenant_conn(tenant_slug) as conn:
        motivo = await aceptacion.aceptar(conn, tenant_slug, _cliente(), nivel=level, sha256=sha256)
    if motivo:
        raise _traducir(motivo)


async def purge(
    *,
    tenant_slug: str,
    actor: uuid.UUID,
    reason: str,
    levels: list[int] | None,
) -> dict[str, Any]:
    """Borra en el proveedor lo de unos niveles, o todo lo de la entidad.

    ``accepted`` dice si el proveedor lo tomó; ``purge_ref`` es su acuse.
    """
    if reason not in _RAZONES:
        raise IntelligenceError(422, "razon")
    if levels is not None and any(n not in _NIVELES for n in levels):
        raise IntelligenceError(422, "nivel")
    if reason == "withdrawn" and levels is not None:
        raise IntelligenceError(422, "nivel")
    if reason == "host_request" and not levels:
        raise IntelligenceError(422, "nivel")
    if levels is not None and len(set(levels)) != len(levels):
        raise IntelligenceError(422, "nivel")
    async with _tenant_conn(tenant_slug) as conn:
        if reason == "host_request" and levels:
            await aceptacion.olvidar(conn, list(levels))
        resultado = await paso.purgar(
            conn,
            tenant_slug,
            _cliente(),
            razon=reason,
            actor=actor,
            niveles=None if levels is None else tuple(levels),
        )
    ref = None if resultado.cuerpo is None else resultado.cuerpo.get("purge_ref")
    return {"accepted": resultado.sale, "purge_ref": ref, "motivo": resultado.motivo}


async def inventory(*, tenant_slug: str) -> dict[str, Any]:
    """Cuántos radicados conserva el proveedor. También con la entidad en local.

    No transmite registros. Si no se puede leer, 503: la pantalla lo dice en
    vez de mostrar un cero.
    """
    async with _tenant_conn(tenant_slug) as conn:
        resultado = await paso.contar_radicados(conn, tenant_slug, _cliente())
    cuerpo = resultado.cuerpo
    if not resultado.sale or not isinstance(cuerpo, dict):
        raise IntelligenceError(503, "inventario_no_disponible")
    return {"records": cuerpo["records"], "complete": cuerpo["complete"]}
