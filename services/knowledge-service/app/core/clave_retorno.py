# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Clave HMAC de retorno (ADR-045 §4).

Hay hasta dos ranuras vigentes durante una rotación. El `kid` que manda el
proveedor elige la ranura. Si la KEK no abre el sello, no hay clave: la
llamada no se autentica.
"""

from __future__ import annotations

import logging
from typing import Any

from orpycamcp_common.kek import decode_aes256, unseal

from app.core.config import settings

logger = logging.getLogger(__name__)

FINALIDAD = "hmac_return"
SLOT = 1
KID = "return-1"
_RANURAS = {KID: SLOT, "return-2": 2}

LEER = """
-- conexion:retorno
SELECT ciphertext, nonce, kek_version
  FROM intelligence_credentials
 WHERE purpose = $1 AND slot = $2
"""


def aad(slug: str, slot: int) -> bytes:
    """El mismo AAD con el que se selló la ranura."""
    return f"tenant:{slug}:intelligence:{FINALIDAD}:{slot}".encode()


def ranura(kid: str) -> int | None:
    """La ranura de este `kid`, o None si no es una clave de retorno."""
    return _RANURAS.get(kid)


async def abrir(conn: Any, slug: str, slot: int) -> bytes | None:
    """La clave en claro, o None si falta, la KEK no está, o el sello no cuadra."""
    cruda = settings.intelligence_credential_kek.strip()
    if not cruda:
        return None
    fila = await conn.fetchrow(LEER, FINALIDAD, slot)
    if fila is None:
        return None
    try:
        if int(fila["kek_version"]) != 1:
            return None
        kek = decode_aes256(cruda, "INTELLIGENCE_CREDENTIAL_KEK")
        return unseal(
            bytes(fila["ciphertext"]),
            nonce=bytes(fila["nonce"]),
            key=kek,
            aad=aad(slug, slot),
        )
    except Exception:
        logger.warning("no se pudo abrir la clave de retorno")
        return None
