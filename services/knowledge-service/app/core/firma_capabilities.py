# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Firma de la declaración `capabilities` (PPI, JWS separado, RFC 8785).

La clave pública está fijada en la configuración. El `kid` del encabezado
tiene que ser ese. La huella es el SHA-256 de la declaración canónica.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from app.core.jcs import canonico

_VERSION = re.compile(r"^1\.\d+\.\d+$")
_JWS = re.compile(r"^[A-Za-z0-9_-]+\.\.[A-Za-z0-9_-]+$")
_CLAVES = (
    "protocol_version",
    "provider",
    "connection_id",
    "accepted_levels",
    "routing",
    "subprocessors",
    "recommendation_kinds",
    "run_tasks",
    "limits",
    "issued_at",
    "expires_at",
)


@dataclass(frozen=True)
class Verificacion:
    ok: bool
    huella: str | None
    motivo: str


def verificar(firmado: dict[str, Any], *, pem: str, kid: str) -> Verificacion:
    """Comprueba la firma y el vencimiento. No acepta una declaración a medias."""
    statement = firmado.get("statement")
    jws = firmado.get("jws")
    if not isinstance(statement, dict) or not isinstance(jws, str) or not _JWS.fullmatch(jws):
        return Verificacion(False, None, "forma")
    if any(clave not in statement for clave in _CLAVES):
        return Verificacion(False, None, "forma")
    if not isinstance(statement.get("protocol_version"), str):
        return Verificacion(False, None, "forma")
    if not _VERSION.fullmatch(statement["protocol_version"]):
        return Verificacion(False, None, "forma")
    if _vencida(statement.get("expires_at")):
        return Verificacion(False, None, "vencida")
    try:
        cuerpo = canonico(statement)
    except TypeError:
        return Verificacion(False, None, "forma")
    encabezado, _, firma = jws.split(".")
    try:
        cabecera = _json_cabecera(encabezado)
        if cabecera.get("alg") not in ("EdDSA", "ES256"):
            return Verificacion(False, None, "alg")
        if cabecera.get("kid") != kid:
            return Verificacion(False, None, "kid")
        carga = _b64url(cuerpo)
        entrada = f"{encabezado}.{carga}".encode()
        _verificar_firma(cabecera["alg"], _b64url_decode(firma), entrada, pem)
    except (ValueError, TypeError, InvalidSignature):
        return Verificacion(False, None, "firma")
    return Verificacion(True, hashlib.sha256(cuerpo).hexdigest(), "ok")


def _vencida(valor: Any) -> bool:
    if not isinstance(valor, str):
        return True
    texto = valor.replace("Z", "+00:00")
    try:
        instante = datetime.fromisoformat(texto)
    except ValueError:
        return True
    if instante.tzinfo is None:
        return True
    return instante <= datetime.now(UTC)


def _json_cabecera(segmento: str) -> dict[str, Any]:
    import json

    cabecera = json.loads(_b64url_decode(segmento))
    if not isinstance(cabecera, dict):
        raise TypeError("cabecera")
    return cabecera


def _verificar_firma(alg: str, firma: bytes, entrada: bytes, pem: str) -> None:
    clave = serialization.load_pem_public_key(pem.encode())
    if alg == "EdDSA":
        if not isinstance(clave, ed25519.Ed25519PublicKey):
            raise TypeError("clave")
        clave.verify(firma, entrada)
        return
    if not isinstance(clave, ec.EllipticCurvePublicKey):
        raise TypeError("clave")
    if len(firma) != 64:
        raise ValueError("firma")
    der = encode_dss_signature(
        int.from_bytes(firma[:32], "big"),
        int.from_bytes(firma[32:], "big"),
    )
    clave.verify(der, entrada, ec.ECDSA(hashes.SHA256()))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segmento: str) -> bytes:
    relleno = "=" * (-len(segmento) % 4)
    return base64.urlsafe_b64decode(segmento + relleno)
