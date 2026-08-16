# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Propuestas firmadas: el compromiso de que lo ejecutado es lo que se describió.

**Qué sustituye.** El gate de escritura era `X-Confirm-Write: true`, un booleano
que cualquier cliente podía poner y que no ataba la confirmación a ningún acto
concreto. El binding MCP directamente lo daba por puesto. Confirmar «algo» no es
confirmar «esto».

**Cómo funciona.** Proponer devuelve un token opaco que lleva dentro el acto
completo —tool y argumentos ya validados—, firmado con HMAC-SHA256. Ejecutar toma
los argumentos **del token**, nunca del cuerpo de la petición: «lo que se firmó»
es «lo que se ejecuta» por construcción, no por disciplina.

**Por qué firmado y no guardado en memoria.** El único almacén que este servicio
tiene es un diccionario por proceso, declarado como no válido para varias
réplicas. Con memoria, proponer en una réplica y confirmar en otra rompe el botón
que la persona sí pulsó — un fallo que aparece justo cuando el sistema crece. La
firma es verificable desde cualquier réplica y sobrevive a un reinicio.

**Lo que NO garantiza, dicho aquí y no en una nota al pie.** El consumo por `jti`
vive en el proceso: cubre el doble clic y el reintento, que es el caso real, pero
no impide que un token robado se ejecute dos veces contra réplicas distintas
dentro de su ventana. La defensa definitiva es la idempotencia aguas abajo, que
hoy no implementa ningún servicio: se emite `Idempotency-Key` como gancho para
que cada épica la implemente, no como garantía fingida.

Lo que sí es cierto siempre: **nada se ejecuta que el servidor no haya descrito y
firmado para ese usuario, en esa entidad, dentro de su ventana**. Eso es
incomparablemente más que un booleano en una cabecera.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

VERSION = 1
_PREFIJO = "op1"


class ProposalError(Exception):
    """Propuesta inválida, caducada, ajena o ya consumida."""

    def __init__(self, code: str, status: int, detail: str = "") -> None:
        self.code = code
        self.status = status
        self.detail = detail or code
        super().__init__(self.detail)


@dataclass(frozen=True)
class Proposal:
    jti: str
    tool: str
    args: dict[str, Any]
    sub: str
    ten: str
    iat: int
    exp: int
    fp: str | None = None


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(txt: str) -> bytes:
    return base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))


def _firmar(payload_b64: str, secreto: str) -> str:
    mac = hmac.new(secreto.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256)
    return _b64e(mac.digest())


def issue(
    tool: str,
    args: dict[str, Any],
    *,
    sub: str,
    ten: str,
    secreto: str,
    ttl_seconds: int,
    fp: str | None = None,
    ahora: int | None = None,
) -> tuple[str, Proposal]:
    """Emite el token de una propuesta. `args` deben venir YA validados."""
    if not secreto:
        # No debería llegar aquí: el arranque falla sin secreto. La guarda existe
        # porque una firma con clave vacía es una firma que cualquiera reproduce,
        # y eso convertiría el gate en decorativo sin que nada lo aparentara.
        raise ProposalError("propuesta_sin_secreto", 500, "falta MCP_PROPOSAL_SECRET")

    iat = int(ahora if ahora is not None else time.time())
    propuesta = Proposal(
        jti=str(uuid.uuid4()),
        tool=tool,
        args=args,
        sub=sub,
        ten=ten,
        iat=iat,
        exp=iat + ttl_seconds,
        fp=fp,
    )
    payload = {
        "v": VERSION,
        "jti": propuesta.jti,
        "tool": propuesta.tool,
        "args": propuesta.args,
        "sub": propuesta.sub,
        "ten": propuesta.ten,
        "iat": propuesta.iat,
        "exp": propuesta.exp,
        "fp": propuesta.fp,
    }
    # `sort_keys` + separadores fijos: la firma se calcula sobre bytes concretos,
    # y una serialización que varíe entre versiones de Python invalidaría tokens
    # vivos sin que nadie entendiera por qué.
    payload_b64 = _b64e(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return f"{_PREFIJO}.{payload_b64}.{_firmar(payload_b64, secreto)}", propuesta


def verify(
    token: str,
    *,
    secretos: tuple[str, ...],
    sub: str,
    ten: str,
    ahora: int | None = None,
) -> Proposal:
    """Verifica firma, vigencia y titularidad. No consume.

    `secretos` admite varias claves para que una rotación no invalide las
    propuestas ya emitidas: se prueba con la actual y con la anterior.
    """
    partes = token.split(".") if token else []
    if len(partes) != 3 or partes[0] != _PREFIJO:
        raise ProposalError("propuesta_invalida", 400, "el formato de la propuesta no es válido")

    _, payload_b64, firma = partes
    # `compare_digest` en vez de `==`: la comparación byte a byte con salida
    # temprana filtra información sobre la firma correcta.
    if not any(
        s and hmac.compare_digest(firma, _firmar(payload_b64, s)) for s in secretos
    ):
        raise ProposalError("propuesta_invalida", 400, "la firma de la propuesta no es válida")

    try:
        datos = json.loads(_b64d(payload_b64))
    except (ValueError, TypeError) as exc:
        raise ProposalError("propuesta_invalida", 400, "la propuesta no se puede leer") from exc

    if datos.get("v") != VERSION:
        raise ProposalError("propuesta_invalida", 400, "versión de propuesta no soportada")

    propuesta = Proposal(
        jti=str(datos.get("jti") or ""),
        tool=str(datos.get("tool") or ""),
        args=datos.get("args") or {},
        sub=str(datos.get("sub") or ""),
        ten=str(datos.get("ten") or ""),
        iat=int(datos.get("iat") or 0),
        exp=int(datos.get("exp") or 0),
        fp=datos.get("fp"),
    )

    if int(ahora if ahora is not None else time.time()) >= propuesta.exp:
        raise ProposalError(
            "propuesta_expirada", 410, "la propuesta caducó; vuelva a pedir la acción"
        )

    # Titularidad: un token filtrado no sirve a otra persona ni a otra entidad.
    # Se comprueba contra la identidad RESUELTA del portador, no contra lo que
    # diga el propio token.
    if propuesta.sub != sub or propuesta.ten != ten:
        raise ProposalError("propuesta_ajena", 403, "la propuesta no es de este usuario")

    return propuesta


# Consumo por `jti`, en proceso. Ver la advertencia del docstring del módulo: es
# best-effort declarado, no una garantía de unicidad global.
_CONSUMIDAS: dict[str, int] = {}


def consume(propuesta: Proposal, *, ahora: int | None = None) -> None:
    """Marca la propuesta como usada. Lanza si ya lo estaba."""
    t = int(ahora if ahora is not None else time.time())
    # Barrido perezoso de las caducadas: sin él, el diccionario crece sin fin.
    for jti, exp in list(_CONSUMIDAS.items()):
        if exp <= t:
            _CONSUMIDAS.pop(jti, None)

    if propuesta.jti in _CONSUMIDAS:
        raise ProposalError(
            "propuesta_consumida", 409, "esa acción ya se ejecutó una vez"
        )
    _CONSUMIDAS[propuesta.jti] = propuesta.exp


def limpiar_consumidas() -> None:
    """Vacía el registro de consumo. Solo para tests."""
    _CONSUMIDAS.clear()


def huella(datos: Any) -> str:
    """Huella estable de unos campos, para detectar que el mundo cambió.

    Sirve para no mostrar una tarjeta mentirosa —el radicado ya se reasignó
    mientras la persona leía—, no para serializar escrituras: entre la
    comprobación y el POST sigue habiendo una ventana, y esa la cierra el guarda
    del endpoint de dominio.
    """
    crudo = json.dumps(datos, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(crudo.encode("utf-8")).hexdigest()
