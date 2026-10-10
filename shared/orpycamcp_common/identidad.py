# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Firma de las cabeceras de identidad que el gateway inyecta.

La identidad viajaba entre el gateway y los servicios como texto plano: quién
eres, de qué entidad y con qué roles, en tres cabeceras sin firma. El gateway no
las reenvía si vienen del cliente, así que desde fuera no se pueden forjar; pero
dentro de la red de contenedores cualquiera que alcance el puerto de un servicio
—un contenedor comprometido, un operador, un proceso auxiliar— podía hablarle
directamente afirmando ser quien quisiera, de la entidad que quisiera.

Se cierra con el mismo mecanismo que el sobre de eventos y por la misma razón:
sin prueba de origen, una cabecera es una afirmación, no una identidad.

**Sin secreto configurado no se verifica**, igual que allí, y es deliberado: un
despliegue en marcha no puede quedarse sin atender por activar esto de golpe. El
interruptor es la propia variable, y en producción es obligatoria.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

#: Cabeceras que se firman, en orden fijo. Son las que deciden QUIÉN hace algo y
#: SOBRE QUÉ entidad: si alguna quedara fuera, se podría cambiar sin romper la
#: firma, que es exactamente el defecto que esto cierra.
CAMPOS_FIRMADOS = (
    "X-User-Id",
    "X-Username",
    "X-Tenant-Slug",
    "X-User-Roles",
    "X-Canal",
    "X-Real-Ip",
)

#: Cabecera donde viaja la firma.
CABECERA_FIRMA = "X-Identity-Signature"


class IdentidadNoAutentica(ValueError):
    """Las cabeceras de identidad no vienen firmadas por el gateway."""


def _secreto() -> str | None:
    return os.environ.get("IDENTITY_SIGNING_SECRET") or None


def _firma(cabeceras: dict[str, str], secreto: str) -> str:
    # Concatenación canónica en orden fijo, con un separador que no puede
    # aparecer en el valor de una cabecera HTTP.
    base = "\x1e".join((cabeceras.get(c) or "") for c in CAMPOS_FIRMADOS)
    return hmac.new(secreto.encode(), base.encode(), hashlib.sha256).hexdigest()


def firmar(cabeceras: dict[str, str]) -> dict[str, str]:
    """Devuelve las cabeceras con su firma, si hay secreto configurado."""
    secreto = _secreto()
    if secreto is None:
        return cabeceras
    return {**cabeceras, CABECERA_FIRMA: _firma(cabeceras, secreto)}


def verificar(cabeceras: dict[str, str]) -> None:
    """Lanza `IdentidadNoAutentica` si la identidad no viene del gateway.

    Las claves se comparan sin distinguir mayúsculas, porque los nombres de
    cabecera no las distinguen y el servidor puede entregarlas en minúscula.
    """
    secreto = _secreto()
    if secreto is None:
        return
    normalizadas = {k.lower(): v for k, v in cabeceras.items()}
    recibidas = {c: normalizadas.get(c.lower(), "") for c in CAMPOS_FIRMADOS}
    esperada = _firma(recibidas, secreto)
    if not hmac.compare_digest(normalizadas.get(CABECERA_FIRMA.lower(), ""), esperada):
        raise IdentidadNoAutentica(
            "Las cabeceras de identidad no vienen firmadas por el gateway."
        )


# ---------------------------------------------------------------------------
# Marca de ORIGEN del canal (AREA-11-04)
# ---------------------------------------------------------------------------
#
# El campo `canal` del asiento dice por dónde entró un acto. El gateway lo deriva
# del lado del servidor, y hasta ahora solo conocía 'mcp' por la RUTA
# (`/api/v1/mcp/…`): todo lo que el mcp-server hace en nombre de una persona —el
# asistente confirmando un radicado, un traslado— llega al gateway por
# `/api/v1/<dominio>` y quedaba como 'api', indistinguible de una llamada directa.
#
# La marca la emite SOLO quien posee `MCP_ORIGIN_SECRET` (el mcp-server) y la
# verifica el gateway. Es un secreto DEDICADO, distinto de `IDENTITY_SIGNING_SECRET`:
# el mcp-server es el servicio más expuesto (corre el loop LLM, habla con un
# proveedor externo y atiende clientes MCP), y con el secreto maestro de identidad
# un compromiso suyo bastaría para firmar cualquier `X-User-Id` de cualquier entidad
# y hablarle directo a un servicio. Con este solo puede marcar el canal y verificar
# la IP; no puede suplantar a nadie. Va ligada al tenant y al token del que habla (sha256 del
# `Authorization`) y caduca a los 120 s: una marca capturada no sirve con otro
# token ni con otra entidad, y un cliente sin el secreto no la puede fabricar.
# Sin secreto configurado NO se emite ni se verifica (el canal cae a 'api', que es
# el lado seguro: nunca se atribuye 'mcp' sin prueba).

#: Cabecera donde viaja la marca de origen. El gateway NO la reenvía aguas abajo.
CABECERA_ORIGEN = "X-Origen-Canal"

#: Vida máxima de una marca, en segundos.
VENTANA_ORIGEN_S = 120


def _secreto_origen() -> str | None:
    """Secreto compartido SOLO por el gateway y el mcp-server (no es el maestro)."""
    return os.environ.get("MCP_ORIGIN_SECRET") or None


def _firma_origen(canal: str, ts: str, tenant: str, authorization: str, secreto: str) -> str:
    huella = hashlib.sha256(authorization.encode()).hexdigest()
    base = "\x1e".join(("origen", canal, ts, tenant, huella))
    return hmac.new(secreto.encode(), base.encode(), hashlib.sha256).hexdigest()


def firmar_origen(
    canal: str, *, tenant: str, authorization: str, ahora: float | None = None
) -> dict[str, str]:
    """Cabecera con la marca de origen de ``canal``, o ``{}`` sin secreto."""
    secreto = _secreto_origen()
    if secreto is None or not authorization or "." in canal:
        return {}
    ts = str(int(time.time() if ahora is None else ahora))
    firma = _firma_origen(canal, ts, tenant, authorization, secreto)
    return {CABECERA_ORIGEN: f"{canal}.{ts}.{firma}"}


def verificar_origen(
    valor: str | None, *, tenant: str, authorization: str, ahora: float | None = None
) -> str | None:
    """Canal que la marca prueba, o ``None`` si falta, es mala o está vencida."""
    secreto = _secreto_origen()
    if secreto is None or not valor or not authorization:
        return None
    partes = valor.split(".")
    if len(partes) != 3:
        return None
    canal, ts, firma = partes
    try:
        emitida = int(ts)
    except ValueError:
        return None
    actual = time.time() if ahora is None else ahora
    if abs(actual - emitida) > VENTANA_ORIGEN_S:
        return None
    esperada = _firma_origen(canal, ts, tenant, authorization, secreto)
    if not hmac.compare_digest(firma, esperada):
        return None
    return canal


#: Cabecera con la firma de `X-Real-Ip` hecha con el secreto de origen.
CABECERA_IP_FIRMA = "X-Real-Ip-Signature"


def _firma_ip(ip: str, usuario: str, tenant: str, secreto: str) -> str:
    base = "\x1e".join(("ip", ip, usuario, tenant))
    return hmac.new(secreto.encode(), base.encode(), hashlib.sha256).hexdigest()


def firmar_ip_real(cabeceras: dict[str, str]) -> dict[str, str]:
    """Cabeceras con la firma de la IP real (``X-Real-Ip``), si hay secreto de origen.

    La emite el gateway, ligada a la IP, a la persona y a la entidad. Va aparte de
    `firmar` a propósito: la verifica el mcp-server, que NO debe poseer el secreto
    maestro de identidad (ver arriba).
    """
    secreto = _secreto_origen()
    ip = (cabeceras.get("X-Real-Ip") or "").strip()
    if secreto is None or not ip:
        return cabeceras
    firma = _firma_ip(
        ip, cabeceras.get("X-User-Id") or "", cabeceras.get("X-Tenant-Slug") or "", secreto
    )
    return {**cabeceras, CABECERA_IP_FIRMA: firma}


def ip_real_firmada(cabeceras: dict[str, str]) -> str | None:
    """`X-Real-Ip` del gateway, SOLO si trae su firma de origen.

    El gateway la deriva por saltos de confianza y la firma con el secreto de
    origen. Un servicio que quiera reenviar la IP del cliente (el mcp-server, para
    que las llamadas del asistente no caigan todas en el cubo por IP de su propio
    contenedor) no puede fiarse de una cabecera suelta: un cliente que llegara
    directo a su puerto elegiría su propia identidad de red. Sin secreto configurado
    no se confía en nada (a diferencia de `verificar`, que en ese modo deja pasar):
    aquí el fallo seguro es NO reenviar.
    """
    secreto = _secreto_origen()
    if secreto is None:
        return None
    normalizadas = {k.lower(): v for k, v in cabeceras.items()}
    ip = (normalizadas.get("x-real-ip") or "").strip()
    if not ip:
        return None
    esperada = _firma_ip(
        ip, normalizadas.get("x-user-id", ""), normalizadas.get("x-tenant-slug", ""), secreto
    )
    if not hmac.compare_digest(normalizadas.get(CABECERA_IP_FIRMA.lower(), ""), esperada):
        return None
    return ip


# ---------------------------------------------------------------------------
# IP de una ruta PÚBLICA (B2, ADR-031)
# ---------------------------------------------------------------------------
#
# En una ruta pública no hay persona ni token, así que no hay identidad que
# firmar; pero la constancia de autorización (Ley 1581) sí lleva la IP de quien
# la dio, y esa IP no puede salir de `X-Forwarded-For`, que escribe el cliente.
# El gateway la deriva por saltos de confianza y la firma con un par PROPIO de
# cabeceras: reusar `X-Identity-Signature` mezclaría un anónimo con una
# identidad de persona (y `IdentidadFirmadaMiddleware` se dispara por `X-User-Id`).
# La firma va ligada a la RUTA y al instante: una capturada no sirve en otra ruta
# ni pasados 120 s. Sin secreto no se emite ni se verifica (fail-safe: IP nula).

#: Instante de emisión (epoch, segundos) de la firma de la IP pública.
CABECERA_IP_PUBLICA_TS = "X-Real-Ip-Ts"
#: Firma de la IP pública.
CABECERA_IP_PUBLICA_FIRMA = "X-Public-Ip-Signature"
#: Vida máxima de la firma, en segundos.
VENTANA_IP_PUBLICA_S = 120


def _firma_ip_publica(ip: str, path: str, ts: str, secreto: str) -> str:
    base = "\x1e".join(("ip-publica", ip, path, ts))
    return hmac.new(secreto.encode(), base.encode(), hashlib.sha256).hexdigest()


def firmar_ip_publica(
    ip: str, path: str, ts: int | None = None
) -> dict[str, str]:
    """Cabeceras ``X-Real-Ip`` + instante + firma, o ``{}`` sin secreto/IP."""
    secreto = _secreto()
    ip = (ip or "").strip()
    if secreto is None or not ip:
        return {}
    marca = str(int(time.time()) if ts is None else int(ts))
    return {
        "X-Real-Ip": ip,
        CABECERA_IP_PUBLICA_TS: marca,
        CABECERA_IP_PUBLICA_FIRMA: _firma_ip_publica(ip, path, marca, secreto),
    }


def ip_publica_verificada(
    cabeceras: dict[str, str], path: str, *, ahora: float | None = None
) -> str | None:
    """IP que el gateway firmó para ESTA ruta hace menos de 120 s, o ``None``.

    Nunca lee ``X-Forwarded-For``. Sin secreto configurado no se confía en nada.
    """
    secreto = _secreto()
    if secreto is None:
        return None
    n = {k.lower(): v for k, v in cabeceras.items()}
    ip = (n.get("x-real-ip") or "").strip()
    marca = n.get(CABECERA_IP_PUBLICA_TS.lower()) or ""
    firma = n.get(CABECERA_IP_PUBLICA_FIRMA.lower()) or ""
    if not ip or not marca or not firma:
        return None
    try:
        emitida = int(marca)
    except ValueError:
        return None
    actual = time.time() if ahora is None else ahora
    if abs(actual - emitida) > VENTANA_IP_PUBLICA_S:
        return None
    if not hmac.compare_digest(firma, _firma_ip_publica(ip, path, marca, secreto)):
        return None
    return ip


class IdentidadFirmadaMiddleware:
    """Verifica la firma de la identidad, si viene identidad.

    Se comprueba **solo cuando hay cabecera de usuario**. Las llamadas
    servicio-a-servicio selladas con token interno y SIN identidad de persona
    tienen su propio gate: exigirles firma las rompería sin ganar nada.

    Lo que esta clase decía antes era que las llamadas servicio-a-servicio «no
    traen identidad de persona», y es falso: siete clientes internos propagan el
    `X-User-Id` de quien originó el acto, porque el destino lo EXIGE para
    atribuirlo a una persona — el sellado del índice electrónico se atribuye a
    quien cierra el expediente (RF-FIR-11/12/14), no al sistema. Con esa premisa
    escrita, esas llamadas pasaron a morir en 401 y ningún expediente cerrado
    volvía a tener su índice sellado. **Quien propaga una identidad, la firma**
    (`firmar`), igual que hace el gateway; el guardián que lo censa vive en
    `shared/tests/test_la_identidad_propagada_va_firmada.py`.

    Es ASGI puro y no `BaseHTTPMiddleware` para poder ir el primero sin
    interferir con el cuerpo en streaming de las subidas.
    """

    def __init__(self, app):  # noqa: ANN001 — firma ASGI
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cabeceras = {
            k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])
        }
        if cabeceras.get("x-user-id"):
            try:
                verificar(cabeceras)
            except IdentidadNoAutentica:
                cuerpo = (
                    b'{"error":"identidad_no_autentica","detail":"La identidad de la '
                    b'peticion no viene firmada por el gateway.","status":401}'
                )
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(cuerpo)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": cuerpo})
                return

        await self.app(scope, receive, send)
