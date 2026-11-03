# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Conexión con el proveedor (ADR-045 §4).

Lee la declaración firmada, la comprueba con la clave fijada y guarda su
huella. Si esa huella cambia, suspende el envío y conserva la huella
aceptada. La declaración de la conexión (niveles, vocabularios vacíos hasta
el paso que los llena, URL de retorno y la clave de retorno) sale solo
cuando la firma cuadra y el envío no queda suspendido por ese cambio.
"""

from __future__ import annotations

import base64
import logging
import re
import secrets
from typing import Any

from orpycamcp_common.kek import decode_aes256, seal

from knowledge.core import clave_retorno, paso
from knowledge.core.config import settings
from knowledge.core.firma_capabilities import verificar
from knowledge.core.salida_http import PPI_VERSION

logger = logging.getLogger(__name__)

try:
    from orpycamcp_common import audit

    _AUDIT = True
except ImportError:  # pragma: no cover
    audit = None  # type: ignore[assignment]
    _AUDIT = False

_SERVICIO = "knowledge-service"
_KID = clave_retorno.KID
_FINALIDAD = clave_retorno.FINALIDAD
_SLOT = clave_retorno.SLOT
_NIVELES = (
    (1, "public", "Pública"),
    (2, "reserved", "Reservada"),
    (3, "classified", "Clasificada"),
)
_KID_FIJADO = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_HUELLA = """
-- conexion:huella
SELECT capabilities_sha256
  FROM intelligence_state
 WHERE id = 1
"""

_FIJAR = """
UPDATE intelligence_state
   SET capabilities_sha256 = $1, updated_at = now()
 WHERE id = 1
   AND capabilities_sha256 IS NULL
"""

_SUSPENDER = """
UPDATE intelligence_state
   SET send_suspended = true, updated_at = now()
 WHERE id = 1
   AND NOT send_suspended
"""

_GUARDAR_RETORNO = """
INSERT INTO intelligence_credentials
    (purpose, slot, client_id, ciphertext, nonce, kek_version)
VALUES ($1, $2, NULL, $3, $4, 1)
ON CONFLICT (purpose, slot) DO NOTHING
"""


async def asegurar(conn: Any, slug: str, cliente: Any) -> None:
    """Una pasada. Con el interruptor apagado no hay llamada."""
    politica = await paso.leer_politica(conn, slug)
    if not politica.completa or politica.motivo is not None:
        return
    pem, kid = _clave_fijada()
    if pem is None or kid is None:
        logger.warning("no hay clave fijada para comprobar la declaración")
        return
    recibido = await paso.capacidades(conn, slug, cliente)
    if not recibido.sale or not isinstance(recibido.cuerpo, dict):
        return
    veredicto = verificar(recibido.cuerpo, pem=pem, kid=kid)
    if not veredicto.ok or veredicto.huella is None:
        await _suspender(conn, slug, "invalida", None, None)
        return
    fila = await conn.fetchrow(_HUELLA)
    guardada = None if fila is None else fila["capabilities_sha256"]
    if guardada is None:
        await conn.execute(_FIJAR, veredicto.huella)
        await _trazar(conn, slug, "intelligence.capabilities_fijadas", {"huella": veredicto.huella})
    elif guardada != veredicto.huella:
        await _suspender(conn, slug, "cambio", guardada, veredicto.huella)
        return
    await _declarar(conn, slug, cliente, politica.niveles)


def _clave_fijada() -> tuple[str | None, str | None]:
    pem = settings.ppi_capabilities_public_key.strip().replace("\\n", "\n")
    kid = settings.ppi_capabilities_kid.strip()
    if not pem or not _KID_FIJADO.fullmatch(kid):
        return None, None
    return pem, kid


async def _declarar(conn: Any, slug: str, cliente: Any, niveles: tuple[int, ...]) -> None:
    base = settings.ppi_callback_base_url.strip().rstrip("/")
    if not base.startswith(("https://", "http://")):
        return
    declarados = [
        {"code": codigo, "label": etiqueta}
        for numero, codigo, etiqueta in _NIVELES
        if numero in niveles
    ]
    if not declarados:
        return
    secreto = await _secreto_retorno(conn, slug)
    if secreto is None:
        return
    cuerpo = {
        "protocol_version": PPI_VERSION,
        "sensitivity_levels": declarados,
        "vocabularies": {"document_types": [], "units": []},
        "callbacks": {
            "recommend_url": f"{base}/ppi/v1/{slug}/recommendations",
            "content_url": f"{base}/ppi/v1/{slug}/content",
            "keys": [
                {
                    "kid": _KID,
                    "secret_base64": base64.b64encode(secreto).decode("ascii"),
                }
            ],
        },
        "recommendation_kinds": [],
    }
    respuesta = await paso.declarar(conn, slug, cliente, cuerpo)
    if not respuesta.sale:
        logger.warning("la declaración de la conexión no quedó guardada")


async def _secreto_retorno(conn: Any, slug: str) -> bytes | None:
    cruda = settings.intelligence_credential_kek.strip()
    if not cruda:
        return None
    try:
        kek = decode_aes256(cruda, "INTELLIGENCE_CREDENTIAL_KEK")
    except Exception:
        logger.warning("no se pudo abrir la KEK de la clave de retorno")
        return None
    fila = await conn.fetchrow(clave_retorno.LEER, _FINALIDAD, _SLOT)
    if fila is None:
        ciphertext, nonce = seal(
            secrets.token_bytes(32), key=kek, aad=clave_retorno.aad(slug, _SLOT)
        )
        await conn.execute(_GUARDAR_RETORNO, _FINALIDAD, _SLOT, ciphertext, nonce)
    return await clave_retorno.abrir(conn, slug, _SLOT)


async def _suspender(
    conn: Any, slug: str, motivo: str, anterior: str | None, recibida: str | None
) -> None:
    await conn.execute(_SUSPENDER)
    await _trazar(
        conn,
        slug,
        "intelligence.capabilities_suspendidas",
        {"motivo": motivo, "anterior": anterior, "recibida": recibida},
    )


async def _trazar(conn: Any, slug: str, action: str, payload: dict[str, Any]) -> None:
    if not _AUDIT or audit is None:
        return
    try:
        await audit.append(
            conn,
            tenant_slug=slug,
            service=_SERVICIO,
            action=action,
            object_type="intelligence_state",
            object_ref=slug,
            actor="system",
            canal="system",
            payload=payload,
        )
    except Exception:
        logger.warning("no se pudo dejar la traza de la conexión")
