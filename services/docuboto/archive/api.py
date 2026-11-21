# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lo que otros dominios piden a archive. Importan este módulo, no los repositorios."""

from __future__ import annotations

from typing import Any

import asyncpg
from orpycamcp_common.database import acquire_tenant


class IndexUnavailableError(Exception):
    """No se pudo leer el índice. El llamante deniega: no es «sin cambios»."""


class ExpedienteNotFoundError(Exception):
    """El expediente no existe en este tenant."""


class IndexNotGeneratedError(Exception):
    """El expediente existe pero aún no tiene índice: no hay contenido que firmar."""

    code = "indice_no_disponible"


async def index_fingerprint(*, tenant_slug: str, ref: str) -> dict[str, Any]:
    """SHA-256 del índice electrónico VIGENTE de un expediente (AREA-04-09).

    La cadena de firma sobre un `expediente` sella el índice, no el
    identificador. `ref` es el código o el UUID (lo que la cadena guarda como
    `objeto_id`). Content-free: el no-read-up lo aplica signature al abrir la
    cadena, así que aquí no hay gate de clearance.
    """
    try:
        async with acquire_tenant(tenant_slug) as conn:
            expediente = await conn.fetchrow(
                "SELECT id, code FROM expedientes WHERE code = $1 OR id::text = $1 LIMIT 1",
                ref,
            )
            if expediente is None:
                raise ExpedienteNotFoundError(f"Expediente {ref} no encontrado")
            indice = await conn.fetchrow(
                "SELECT version, estado, xml_sha256, num_documentos FROM expediente_indice "
                "WHERE expediente_id = $1 ORDER BY version DESC LIMIT 1",
                expediente["id"],
            )
    except (asyncpg.PostgresError, OSError) as exc:
        raise IndexUnavailableError(str(exc)) from exc
    if indice is None or not indice["xml_sha256"]:
        raise IndexNotGeneratedError("El expediente no tiene índice electrónico generado")
    return {
        "expediente_id": str(expediente["id"]),
        "code": expediente["code"],
        "version": indice["version"],
        "estado": indice["estado"],
        "num_documentos": indice["num_documentos"],
        "huella_sha256": str(indice["xml_sha256"]).strip().lower(),
    }
