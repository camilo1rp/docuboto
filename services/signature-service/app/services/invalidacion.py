# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Invalidación de circuitos cuyo texto dejó de ser el vigente (B10, ítem 2).

Un único núcleo para las tres puertas por las que llega el aviso: el consumidor
de ``documento.contenido_cambiado``, el replay de la DLQ y el endpoint síncrono
que llama document-service al editar. El predicado es siempre la huella VIGENTE
(ver ``CadenaRepository.invalidar_por_contenido``), así que ninguna puerta depende
del orden en que lleguen los avisos y repetir cualquiera es inocuo.
"""

from __future__ import annotations

import logging
import re
from typing import Literal

import asyncpg
from orpycamcp_common import audit

from app.core.http_clients import (
    BorradorDesaparecidoError,
    HuellaNoVerificableError,
    get_huella_borrador,
)
from app.repositories.cadena_repository import CadenaRepository

logger = logging.getLogger(__name__)

_SERVICE = "signature-service"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

MOTIVO_CAMBIO = "el texto del documento cambió después de someterse a firma"
MOTIVO_DESAPARECIDO = "el borrador ya no existe"


async def resolver_huella_vigente(borrador_id: str, tenant_slug: str) -> str | None:
    """Huella vigente del texto, preguntada a document-service AHORA.

    ``None`` = el borrador ya no existe. ``HuellaNoVerificableError`` se propaga:
    sin ack el mensaje vuelve al PEL y, tras los reintentos, a la DLQ; tragárselo
    daría por «sin cambios» algo que simplemente no se pudo comprobar.
    """
    try:
        datos = await get_huella_borrador(borrador_id, tenant_slug)
    except BorradorDesaparecidoError:
        return None
    huella = datos.get("cuerpo_sha256")
    if not isinstance(huella, str) or not _SHA256.match(huella):
        raise HuellaNoVerificableError("document-service no devolvió una huella de texto válida")
    return huella


async def invalidar_por_contenido(
    conn: asyncpg.Connection,
    *,
    tenant_slug: str,
    objeto_id: str,
    vigente: str | None,
    origen: Literal["evento", "sincrono", "replay"],
    event_id: str | None,
    objeto_tipo: str = "borrador",
    actor: str = "system",
) -> int:
    """Invalida lo que no recae sobre ``vigente`` y deja el asiento en la MISMA tx.

    Con 0 solicitudes afectadas no hay asiento: una reentrega at-least-once no
    duplica nada. Devuelve cuántas solicitudes se invalidaron.
    """
    motivo = MOTIVO_DESAPARECIDO if vigente is None else MOTIVO_CAMBIO
    async with conn.transaction():
        filas = await CadenaRepository(conn).invalidar_por_contenido(
            objeto_tipo, objeto_id, vigente, motivo
        )
        if not filas:
            return 0
        await audit.append(
            conn,
            tenant_slug=tenant_slug,
            service=_SERVICE,
            action="firma.circuito_invalidado",
            object_type=objeto_tipo,
            object_ref=objeto_id,
            actor=actor,
            payload={
                "solicitudes": [str(f["id"]) for f in filas],
                "estados_previos": sorted({f["estado_previo"] for f in filas}),
                "vigente": vigente,
                "origen": origen,
                "event_id": event_id,
                "motivo": motivo,
            },
            canal="system",
        )
    logger.info(
        "Invalidadas %d solicitud(es) de %s %s (origen=%s)",
        len(filas), objeto_tipo, objeto_id, origen,
    )
    return len(filas)
