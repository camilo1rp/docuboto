# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Token de cuenta de servicio para las llamadas PPI (ADR-045 §4).

Abre el secreto `service_client` con la misma KEK que `init_tenant.py` y pide
un token `client_credentials` al realm. Lo guarda hasta poco antes de `exp`.
Sin secreto, sin KEK o sin respuesta del realm, no hay token: la pasarela no
firma una petición al proveedor.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from orpycamcp_common.kek import decode_aes256, unseal

from knowledge.core.config import settings

logger = logging.getLogger(__name__)

FINALIDAD = "service_client"
SLOT = 1
_MARGEN = 30.0

_LEER = """
SELECT client_id, ciphertext, nonce, kek_version
  FROM intelligence_credentials
 WHERE purpose = $1 AND slot = $2
"""

_cache: dict[str, tuple[str, float]] = {}


def aad(slug: str) -> bytes:
    """El mismo AAD con el que se selló el secreto al aprovisionar la entidad."""
    return f"tenant:{slug}:intelligence:{FINALIDAD}:{SLOT}".encode()


def olvidar(slug: str | None = None) -> None:
    """Tira el token guardado. Sin slug, tira todos."""
    if slug is None:
        _cache.clear()
        return
    _cache.pop(slug, None)


async def portador(conn: Any, slug: str) -> str:
    """El access token vigente, o un error. Nunca una cadena vacía."""
    ahora = time.monotonic()
    guardado = _cache.get(slug)
    if guardado is not None and guardado[1] > ahora:
        return guardado[0]
    token, plazo = await _pedir(conn, slug)
    _cache[slug] = (token, ahora + plazo)
    return token


async def _pedir(conn: Any, slug: str) -> tuple[str, float]:
    cruda = settings.intelligence_credential_kek.strip()
    if not cruda:
        raise RuntimeError("sin KEK del cliente de servicio")
    base = settings.keycloak_url.strip()
    realm = settings.keycloak_realm.strip() or "orpycamcp"
    if not base:
        raise RuntimeError("sin URL del realm")
    fila = await conn.fetchrow(_LEER, FINALIDAD, SLOT)
    if fila is None:
        raise RuntimeError("sin cliente de servicio")
    client_id = str(fila["client_id"] or "").strip()
    if not client_id:
        raise RuntimeError("sin cliente de servicio")
    try:
        if int(fila["kek_version"]) != 1:
            raise RuntimeError("sin cliente de servicio")
        kek = decode_aes256(cruda, "INTELLIGENCE_CREDENTIAL_KEK")
        secreto = unseal(
            bytes(fila["ciphertext"]),
            nonce=bytes(fila["nonce"]),
            key=kek,
            aad=aad(slug),
        ).decode()
    except Exception:
        logger.warning("no se pudo abrir el secreto del cliente de servicio")
        raise RuntimeError("sin cliente de servicio") from None
    url = f"{base.rstrip('/')}/realms/{realm}/protocol/openid-connect/token"
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": secreto,
                },
            )
            resp.raise_for_status()
            cuerpo = resp.json()
    except Exception:
        logger.warning("el realm no entregó el token de servicio")
        raise RuntimeError("sin token de servicio") from None
    token = cuerpo.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("sin token de servicio")
    try:
        vida = float(cuerpo.get("expires_in") or 0)
    except (TypeError, ValueError):
        vida = 0.0
    plazo = max(vida - _MARGEN, 0.0)
    return token, plazo
