# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Reto de prueba de trabajo ALTCHA — protocolo v1, en la biblioteca estándar (C16).

Solo cálculo: sin base de datos ni Redis, para poder sustituirlo por el protocolo
v2 (PBKDF2) el día que el widget suba a 3.x sin tocar a quien lo llama. El
antirreplay (consumir el reto) vive aparte, en `app/services/antirrobots.py`.

El widget instalado es `altcha@2.3.0`, fijado EXACTO en `frontend/package.json`
(`"altcha": "2.3.0"`, sin `^`): subir el widget cambia el protocolo en silencio
(la 3.x usa PBKDF2/Argon2) y un `^` lo haría solo. `tests/test_altcha.py` fija un
vector generado con el algoritmo del widget; un cambio de protocolo lo rompe.

Protocolo v1:

    salt      = <32 hex> "?" expires=<epoch>&t=<slug>&v=1
    challenge = sha256(salt + str(number)).hex()
    signature = HMAC-SHA256(clave, challenge).hex()

El cliente recibe (algorithm, challenge, maxnumber, salt, signature), busca el
`number` por fuerza bruta y devuelve `base64(JSON{algorithm, challenge, number,
salt, signature, took})`. `salt` (con `expires` y `t`) queda cubierto por
`challenge`, y `challenge` por la firma: alargar el vencimiento o cambiar de
entidad invalida la firma.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from typing import Any
from urllib.parse import parse_qs, urlencode

from orpycamcp_common.tenant import slug_valido

ALGORITMO = "SHA-256"
#: Longitud mínima de la clave HMAC del despliegue. Constante del módulo y no un ajuste:
#: bajarla por entorno sería debilitar la defensa sin que nadie lo decida.
HMAC_MIN_BYTES = 32
VERSION = "1"
#: 128 bits de aleatoriedad en hex, un `?` y los parámetros (sin otro `?` ni `#`).
_SALT_RE = re.compile(r"^[0-9a-f]{32}\?[^?#]{1,200}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_PARAMETROS = frozenset({"expires", "t", "v"})


class AltchaRechazado(Exception):
    """El payload no vale. `codigo` es uno de `antirrobots_invalido|antirrobots_vencido`."""

    def __init__(self, codigo: str) -> None:
        super().__init__(codigo)
        self.codigo = codigo


def _firma(clave: bytes, challenge: str) -> str:
    return hmac.new(clave, challenge.encode(), hashlib.sha256).hexdigest()


def _hash(salt: str, number: int) -> str:
    return hashlib.sha256(f"{salt}{number}".encode()).hexdigest()


def emitir_reto(
    *, clave: bytes, tenant: str, ahora: int, ttl: int, maxnumber: int
) -> dict[str, Any]:
    """Un reto nuevo, ya firmado, atado a la entidad y con vencimiento."""
    salt = secrets.token_hex(16) + "?" + urlencode({"expires": ahora + ttl, "t": tenant, "v": VERSION})
    number = secrets.randbelow(maxnumber + 1)
    challenge = _hash(salt, number)
    return {
        "algorithm": ALGORITMO,
        "challenge": challenge,
        "maxnumber": maxnumber,
        "salt": salt,
        "signature": _firma(clave, challenge),
    }


def resolver_reto(reto: dict[str, Any]) -> int:
    """Fuerza bruta hasta `maxnumber`: lo que hace el widget. Para scripts y pruebas."""
    for number in range(int(reto["maxnumber"]) + 1):
        if _hash(reto["salt"], number) == reto["challenge"]:
            return number
    raise ValueError("el reto no tiene solución dentro de maxnumber")


def codificar_payload(reto: dict[str, Any], number: int, took: int = 1) -> str:
    """El campo `altcha` que envía el widget: base64 de un JSON."""
    return base64.b64encode(
        json.dumps(
            {
                "algorithm": reto["algorithm"],
                "challenge": reto["challenge"],
                "number": number,
                "salt": reto["salt"],
                "signature": reto["signature"],
                "took": took,
            }
        ).encode()
    ).decode()


def verificar(
    payload: str, *, clave: bytes, tenant: str, maxnumber: int, ahora: int
) -> tuple[str, int]:
    """Valida el payload y devuelve `(challenge, expires)`. Todo fail-closed.

    No consume el reto: eso es del antirreplay. El orden es el del diseño C-3:
    forma, algoritmo, `salt`, rango, solución, firma y, solo DESPUÉS de la firma,
    el vencimiento (un `expires` falso no merece un mensaje distinto).
    """
    invalido = AltchaRechazado("antirrobots_invalido")
    try:
        crudo = base64.b64decode(payload, validate=True)
        datos = json.loads(crudo)
    except (binascii.Error, ValueError):  # JSON inválido o base64 mal formado
        raise invalido from None
    if not isinstance(datos, dict):
        raise invalido
    algorithm, challenge, salt, signature = (
        datos.get("algorithm"),
        datos.get("challenge"),
        datos.get("salt"),
        datos.get("signature"),
    )
    number = datos.get("number")
    if not (
        isinstance(algorithm, str)
        and isinstance(challenge, str)
        and isinstance(salt, str)
        and isinstance(signature, str)
    ):
        raise invalido
    # `bool` es subclase de `int`: `true` no es un número de reto.
    if type(number) is not int:
        raise invalido
    if algorithm != ALGORITMO:  # el algoritmo no se negocia
        raise invalido
    if not (_HEX64_RE.match(challenge) and _HEX64_RE.match(signature)):
        raise invalido
    if not _SALT_RE.match(salt):
        raise invalido
    try:
        consulta = parse_qs(salt.split("?", 1)[1], strict_parsing=True, keep_blank_values=True)
    except ValueError:
        raise invalido from None
    if set(consulta) != _PARAMETROS or any(len(v) != 1 for v in consulta.values()):
        raise invalido  # faltan, sobran o se repiten parámetros
    expires_txt, entidad, version = (consulta[k][0] for k in ("expires", "t", "v"))
    if version != VERSION or not slug_valido(entidad) or entidad != tenant:
        raise invalido
    if not (expires_txt.isascii() and expires_txt.isdigit() and len(expires_txt) <= 12):
        raise invalido
    if not 0 <= number <= maxnumber:
        raise invalido
    if not hmac.compare_digest(_hash(salt, number), challenge):  # la solución
        raise invalido
    if not hmac.compare_digest(_firma(clave, challenge), signature):  # la autenticidad
        raise invalido
    expires = int(expires_txt)
    if expires <= ahora:
        raise AltchaRechazado("antirrobots_vencido")
    return challenge, expires
