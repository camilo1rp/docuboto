# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Seudónimo de quien consulta (ADR-045 §4).

Es el HMAC-SHA256 del id interno, en hex. La clave vive cifrada en
``intelligence_credentials`` (finalidad ``pseudonym``, ranura 1). Si no está
o no se puede abrir, no hay consulta: el id crudo no sale.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from typing import Any

from orpycamcp_common.kek import decode_aes256, unseal

from knowledge.core.config import settings

logger = logging.getLogger(__name__)

FINALIDAD = "pseudonym"
SLOT = 1

_CLAVE = """
SELECT ciphertext, nonce, kek_version
  FROM intelligence_credentials
 WHERE purpose = $1 AND slot = $2
"""


def aad(slug: str) -> bytes:
    """El mismo AAD que sella la clave al aprovisionar la entidad."""
    return f"tenant:{slug}:intelligence:{FINALIDAD}:{SLOT}".encode()


def referenciar(clave: bytes, actor: uuid.UUID) -> str:
    """64 hex. Cabe en el `SubjectRef` del protocolo y no es el id."""
    return hmac.new(clave, str(actor).encode(), hashlib.sha256).hexdigest()


async def abrir(conn: Any, slug: str) -> bytes | None:
    """La clave en claro, o None si falta, la KEK no está, o el sello no cuadra."""
    cruda = settings.intelligence_credential_kek.strip()
    if not cruda:
        return None
    fila = await conn.fetchrow(_CLAVE, FINALIDAD, SLOT)
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
            aad=aad(slug),
        )
    except Exception:
        logger.warning("no se pudo abrir la clave de seudónimo")
        return None
