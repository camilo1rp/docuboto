# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Ingesta de preparación: un archivo subido, aún sin radicado."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from knowledge.clients import storage
from knowledge.core import paso
from knowledge.core.traduccion import lote_preparacion


@dataclass(frozen=True)
class Resultado:
    """Salida de crear, consultar, extraer o borrar una preparación."""

    sale: bool
    motivo: str
    cuerpo: dict[str, Any] | None = None


async def crear(
    conn: Any,
    slug: str,
    cliente: Any,
    file_id: uuid.UUID,
    *,
    leer: Callable[[uuid.UUID, str], Awaitable[storage.Archivo | storage.Falta]] | None = None,
    ahora: datetime | None = None,
    staging_id: str | None = None,
) -> Resultado:
    """Sube el archivo como record padre más adjunto en línea."""
    obtener = leer or (lambda ident, entidad: storage.leer(ident, slug=entidad))
    pieza = await obtener(file_id, slug)
    if isinstance(pieza, storage.Falta):
        return Resultado(False, pieza.motivo)
    ident = staging_id or f"staging-{uuid.uuid4()}"
    lote = lote_preparacion(
        staging_id=ident,
        filename=pieza.filename,
        media_type=pieza.media_type,
        size_bytes=pieza.size_bytes,
        sha256=pieza.sha256,
        contenido=pieza.contenido,
        received_at=ahora or datetime.now(tz=UTC),
    )
    decision = await paso.enviar_preparacion(conn, slug, cliente, lote, ident)
    if not decision.sale:
        return Resultado(False, decision.motivo)
    return Resultado(
        True,
        "ok",
        {
            "staging_ref": ident,
            "file_ref": f"{ident}.file",
            "file_id": str(file_id),
            "filename": pieza.filename,
            "size_bytes": pieza.size_bytes,
            "sha256": pieza.sha256,
        },
    )


async def estado(conn: Any, slug: str, cliente: Any, external_ref: str) -> Resultado:
    """El estado de pipeline del adjunto (el archivo)."""
    respuesta = await paso.estado_ingesta(conn, slug, cliente, external_ref)
    return Resultado(respuesta.sale, respuesta.motivo, respuesta.cuerpo)


async def extraer(conn: Any, slug: str, cliente: Any, cuerpo: dict[str, Any]) -> Resultado:
    """Rellena los campos pedidos desde el objeto indexado."""
    respuesta = await paso.extraer_campos(conn, slug, cliente, cuerpo)
    return Resultado(respuesta.sale, respuesta.motivo, respuesta.cuerpo)


async def borrar(conn: Any, slug: str, cliente: Any, external_ref: str) -> Resultado:
    """Borra el stub y sus hijos para que no quede junto al radicado real."""
    decision = await paso.expulsar(conn, slug, external_ref, cliente, razon="host_request")
    return Resultado(decision.sale, decision.motivo)
