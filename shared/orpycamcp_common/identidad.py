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

**Un solo uso (ADR-036, addendum C-7).** La firma lleva un nonce de 128 bits que
`firmar` genera siempre nuevo y que `IdentidadFirmadaMiddleware` reclama en Redis
(``SET NX EX``) solo después de verificar la firma: la reproducción de una captura,
incluso dentro de la ventana, da 401. Sin Redis, las peticiones con identidad de
persona dan 503 ``identidad_no_verificable`` (fail-closed); las públicas no se tocan.

**Sin secreto configurado no se verifica**, igual que allí, y es deliberado: un
despliegue en marcha no puede quedarse sin atender por activar esto de golpe. El
interruptor es la propia variable, y en producción es obligatoria.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import math
import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import unquote

from orpycamcp_common.tenant import slug_valido

logger = logging.getLogger(__name__)

#: Versión de la base firmada. v2 ataba la firma al método y a la ruta (ADR-036);
#: v3 (ADR-036, addendum C-7) añade un nonce de un solo uso: una identidad capturada
#: no se puede reproducir ni siquiera DENTRO de su ventana de validez.
VERSION_FIRMA = "v3"

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
    # El instante de emisión VA FIRMADO (ADR-035, B9-N3): sin él, un juego de
    # cabeceras capturado dentro de la red valía para siempre y permitía
    # reproducirlo contra un servicio que no consulta el estado de la entidad
    # (archive, workflow, storage), evadiendo una suspensión.
    "X-Identity-Ts",
    # Nonce de 128 bits, de un solo uso (ADR-036, addendum C-7): dentro del HMAC,
    # porque un nonce que se pudiera cambiar sin romper la firma no ataría nada.
    "X-Identity-Nonce",
)

#: Cabecera donde viaja la firma.
CABECERA_FIRMA = "X-Identity-Signature"

#: Cabecera con el instante de emisión (epoch, segundos). La pone `firmar`.
CABECERA_TS = "X-Identity-Ts"

#: Cabecera con el nonce de un solo uso. Lo genera SIEMPRE `firmar`.
CABECERA_NONCE = "X-Identity-Nonce"

#: Forma del nonce: `secrets.token_urlsafe(16)` = 22 caracteres base64url (128 bits).
_RE_NONCE = re.compile(r"^[A-Za-z0-9_-]{22}$")

#: Antigüedad máxima aceptada de una identidad firmada, en segundos. Cubre de sobra
#: el trayecto gateway->servicio y los reintentos de un cliente interno; configurable
#: con `IDENTITY_MAX_AGE_SECONDS`.
VENTANA_IDENTIDAD_S = 120

#: Adelanto máximo tolerado del reloj del emisor respecto del verificador, en
#: segundos (desfase entre contenedores). `IDENTITY_MAX_SKEW_SECONDS`.
SKEW_IDENTIDAD_S = 30


class IdentidadNoAutentica(ValueError):
    """Las cabeceras de identidad no vienen firmadas por el gateway."""


def _secreto() -> str | None:
    return os.environ.get("IDENTITY_SIGNING_SECRET") or None


def ruta_canonica(path: str) -> str:
    """Forma de la ruta que firman y verifican los DOS extremos.

    Sin query ni fragmento (no deciden qué operación es) y decodificada hasta
    estabilizar: el gateway firma la ruta que Starlette ya decodificó una vez, httpx
    la reenvía tal cual y el destino la decodifica otra vez, así que ``/x/%2541``
    llega como ``/x/A`` y no como ``/x/%41``. Aplicar esta función en ambos lados
    hace que coincidan. Si no estabiliza (decodificación anidada sin fin) o no
    empieza por ``/``, no es una ruta: ``IdentidadNoAutentica``.
    """
    decodificada = path.split("#", 1)[0].split("?", 1)[0]
    for _ in range(8):
        siguiente = unquote(decodificada)
        if siguiente == decodificada:
            break
        decodificada = siguiente
    else:
        raise IdentidadNoAutentica("La ruta de la identidad no es canónica.")
    if not decodificada.startswith("/"):
        raise IdentidadNoAutentica("La ruta de la identidad no es canónica.")
    return decodificada


def _firma(cabeceras: dict[str, str], secreto: str, *, metodo: str, ruta: str) -> str:
    # Concatenación canónica en orden fijo, con un separador que no puede
    # aparecer en el valor de una cabecera HTTP. Método y ruta NO viajan: el
    # emisor los conoce porque es la petición que va a hacer y el verificador los
    # toma de la suya. Si viajaran como cabecera, alguien acabaría confiando en ella.
    base = "\x1e".join(
        (
            VERSION_FIRMA,
            metodo.upper(),
            ruta_canonica(ruta),
            *((cabeceras.get(c) or "") for c in CAMPOS_FIRMADOS),
        )
    )
    return hmac.new(secreto.encode(), base.encode(), hashlib.sha256).hexdigest()


def _entero_env(nombre: str, defecto: int) -> int:
    try:
        valor = int(os.environ.get(nombre, ""))
    except ValueError:
        return defecto
    return valor if valor >= 0 else defecto


def firmar(
    cabeceras: dict[str, str], *, metodo: str, ruta: str, ahora: float | None = None
) -> dict[str, str]:
    """Devuelve las cabeceras con su instante y su firma, si hay secreto configurado.

    ``metodo`` y ``ruta`` son los de la petición que se va a hacer (obligatorios y
    sin valor por defecto: quien olvide atarlos falla al escribirlo).

    **Cada llamada genera un nonce nuevo** y descarta cualquier ``X-Identity-Nonce``
    (y ``X-Identity-Ts``) entrante, sin distinguir mayúsculas: el llamante no puede
    aportar uno, así que no existe forma de «reutilizar» una identidad desde la API.
    Todo reintento es una llamada nueva a `firmar`, es decir, una identidad nueva.
    """
    secreto = _secreto()
    if secreto is None:
        return cabeceras
    propios = {CABECERA_TS.lower(), CABECERA_NONCE.lower(), CABECERA_FIRMA.lower()}
    con_ts = {
        **{k: v for k, v in cabeceras.items() if k.lower() not in propios},
        CABECERA_TS: str(int(time.time() if ahora is None else ahora)),
        CABECERA_NONCE: secrets.token_urlsafe(16),
    }
    return {**con_ts, CABECERA_FIRMA: _firma(con_ts, secreto, metodo=metodo, ruta=ruta)}


@dataclass(frozen=True)
class IdentidadVerificada:
    """Lo que `verificar` prueba de una identidad firmada y vigente.

    ``caduca_en`` (epoch, segundos) es el instante en que la identidad deja de valer
    en el reloj del verificador (``emitida + max_age``): de ahí sale el TTL del nonce.
    """

    tenant: str
    nonce: str
    caduca_en: float


def verificar(
    cabeceras: dict[str, str], *, metodo: str, ruta: str, ahora: float | None = None
) -> IdentidadVerificada | None:
    """Lanza `IdentidadNoAutentica` si la identidad no viene del gateway o caducó.

    Devuelve ``None`` sin secreto configurado (no se verifica) o la
    `IdentidadVerificada` con la firma, la ventana y la forma del nonce en regla.
    **No** consume el nonce: eso lo hace `IdentidadFirmadaMiddleware` DESPUÉS, con el
    registro de un solo uso, para que `verificar` siga siendo síncrona y pura.

    Las claves se comparan sin distinguir mayúsculas, porque los nombres de
    cabecera no las distinguen y el servidor puede entregarlas en minúscula.

    Además de la firma, el instante firmado tiene que estar dentro de la ventana
    (`IDENTITY_MAX_AGE_SECONDS`, 120 s) y no más de `IDENTITY_MAX_SKEW_SECONDS`
    (30 s) en el futuro: una identidad capturada deja de servir. Y solo vale para
    el ``metodo`` y la ``ruta`` de ESTA petición (los toma el llamante de su scope).
    """
    secreto = _secreto()
    if secreto is None:
        return None
    normalizadas = {k.lower(): v for k, v in cabeceras.items()}
    recibidas = {c: normalizadas.get(c.lower(), "") for c in CAMPOS_FIRMADOS}
    esperada = _firma(recibidas, secreto, metodo=metodo, ruta=ruta)
    if not hmac.compare_digest(normalizadas.get(CABECERA_FIRMA.lower(), ""), esperada):
        raise IdentidadNoAutentica(
            "Las cabeceras de identidad no vienen firmadas por el gateway."
        )
    # La firma ya cubre el instante: solo falta que sea reciente. Mismo mensaje que
    # una firma mala, a propósito (no se le dice a un atacante cuál de las dos falló).
    try:
        emitida = int(recibidas[CABECERA_TS])
    except ValueError:
        raise IdentidadNoAutentica(
            "Las cabeceras de identidad no vienen firmadas por el gateway."
        ) from None
    actual = time.time() if ahora is None else ahora
    edad = actual - emitida
    max_age = _entero_env("IDENTITY_MAX_AGE_SECONDS", VENTANA_IDENTIDAD_S)
    if edad > max_age or -edad > _entero_env("IDENTITY_MAX_SKEW_SECONDS", SKEW_IDENTIDAD_S):
        raise IdentidadNoAutentica(
            "Las cabeceras de identidad no vienen firmadas por el gateway."
        )
    nonce = recibidas[CABECERA_NONCE]
    if not _RE_NONCE.fullmatch(nonce):
        raise IdentidadNoAutentica(
            "Las cabeceras de identidad no vienen firmadas por el gateway."
        )
    return IdentidadVerificada(
        tenant=recibidas["X-Tenant-Slug"], nonce=nonce, caduca_en=float(emitida + max_age)
    )


# ---------------------------------------------------------------------------
# Registro de un solo uso del nonce (ADR-036, addendum C-7)
# ---------------------------------------------------------------------------
#
# La firma + la ventana de 120 s dejaban una captura de red reproducible muchas veces
# dentro de la ventana. El nonce la reduce a UN uso: el primer verificador que lo ve lo
# reclama en Redis (`SET NX EX`), compartido por todas las réplicas; el segundo recibe
# 401. Es la ÚNICA dependencia dura de Redis en el camino síncrono (fail-closed): se
# acota a peticiones CON identidad de persona.

#: Prefijo de la clave del registro; la versión va dentro para que v4 no choque con v3.
PREFIJO_CLAVE_NONCE = "orpycamcp:identidad:nonce:v3:"


class RegistroNoDisponible(RuntimeError):
    """El registro de nonces no responde: no se puede afirmar ni negar el primer uso."""


class RegistroNonces(Protocol):
    async def reclamar(self, clave: str, ttl_s: int) -> bool:
        """``True`` si es el primer uso; ``False`` si ya estaba. Falla con
        `RegistroNoDisponible`: nunca devuelve ``True``/``False`` por un error."""
        ...


def clave_nonce(tenant: str, nonce: str) -> str:
    """Clave por tenant + nonce, SIN servicio: la misma identidad no vale en dos destinos."""
    t = tenant if slug_valido(tenant) else "-"
    return f"{PREFIJO_CLAVE_NONCE}{t}:{nonce}"


def ttl_nonce(v: IdentidadVerificada, ahora: float) -> int:
    """Vida del nonce en el registro, en segundos.

    Vida restante de la identidad en el reloj del verificador MÁS el desfase tolerado
    (otro verificador con el reloj atrasado hasta ``SKEW`` la aceptaría más tarde en
    tiempo real). Piso de 1 s en la parte restante; techo ``max_age + 2*skew`` = 180 s.
    """
    skew = _entero_env("IDENTITY_MAX_SKEW_SECONDS", SKEW_IDENTIDAD_S)
    return max(1, math.ceil(v.caduca_en - ahora)) + skew


class RegistroNoncesMemoria:
    """Registro en memoria, SOLO para pruebas (se instala con `fijar_registro`)."""

    def __init__(self) -> None:
        self._vence: dict[str, float] = {}
        self.llamadas = 0

    async def reclamar(self, clave: str, ttl_s: int) -> bool:
        self.llamadas += 1
        ahora = time.monotonic()
        if self._vence.get(clave, 0.0) > ahora:
            return False
        self._vence[clave] = ahora + ttl_s
        return True


class RegistroNoncesRedis:
    """Registro compartido en Redis: ``SET clave 1 NX EX ttl``.

    Cliente propio y perezoso (uno por proceso y por bucle de eventos), con tiempos de
    espera cortos: un Redis colgado debe ser un 503 rápido, no una petición colgada.
    **Cualquier** excepción es `RegistroNoDisponible`; jamás se traduce a un booleano.
    """

    def __init__(self, url: str | None = None) -> None:
        self._url = url
        self._cliente: Any = None
        self._bucle: asyncio.AbstractEventLoop | None = None

    def _obtener(self) -> Any:
        import redis.asyncio as aioredis

        bucle = asyncio.get_running_loop()
        if self._cliente is None or self._bucle is not bucle:
            url = self._url or os.environ.get("REDIS_URL") or ""
            if not url:
                raise RegistroNoDisponible("REDIS_URL no está configurada")
            self._cliente = aioredis.from_url(
                url, socket_timeout=0.5, socket_connect_timeout=0.5
            )
            self._bucle = bucle
        return self._cliente

    async def reclamar(self, clave: str, ttl_s: int) -> bool:
        try:
            puesto = await self._obtener().set(clave, b"1", nx=True, ex=ttl_s)
        except RegistroNoDisponible:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # RedisError, OSError, TimeoutError: ninguna es "primer uso"
            raise RegistroNoDisponible(f"{type(exc).__name__}: {exc}") from exc
        return bool(puesto)


_registro: RegistroNonces | None = None


def fijar_registro(registro: RegistroNonces | None) -> None:
    """Instala un registro (o ``None`` para volver al de Redis). Solo para pruebas."""
    global _registro
    _registro = registro


def _registro_activo() -> RegistroNonces:
    """El registro instalado o el de Redis por defecto (uno por proceso)."""
    global _registro
    if _registro is None:
        _registro = RegistroNoncesRedis()
    return _registro


def registro_configurado() -> bool:
    """¿Hay con qué registrar nonces? (registro instalado o ``REDIS_URL``)."""
    return _registro is not None or bool(os.environ.get("REDIS_URL"))


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

    La firma va atada al MÉTODO y a la RUTA de esta petición (ADR-036): se verifica
    con `scope["method"]` y `scope["path"]`, de modo que una identidad capturada no
    sirve para otra operación del mismo servicio.

    Es ASGI puro y no `BaseHTTPMiddleware` para poder ir el primero sin
    interferir con el cuerpo en streaming de las subidas.
    """

    def __init__(self, app):  # noqa: ANN001 — firma ASGI
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] == "lifespan":
            # Configuración incoherente = no arranca: con secreto y sin dónde registrar
            # nonces, TODA petición con identidad daría 503. Es un fallo determinista de
            # despliegue y se acusa al arrancar, no petición a petición. (Un Redis que
            # no responde al arrancar NO impide arrancar: puede llegar después.)
            if _secreto() is not None and not registro_configurado():
                mensaje = await receive()
                if mensaje["type"] == "lifespan.startup":
                    await send(
                        {
                            "type": "lifespan.startup.failed",
                            "message": (
                                "IDENTITY_SIGNING_SECRET está configurado pero REDIS_URL no: "
                                "el nonce de un solo uso (ADR-036, addendum C-7) necesita Redis."
                            ),
                        }
                    )
                    return
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cabeceras = {
            k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])
        }
        if cabeceras.get("x-user-id"):
            try:
                verificada = verificar(cabeceras, metodo=scope["method"], ruta=scope["path"])
            except IdentidadNoAutentica:
                await _responder(
                    send,
                    401,
                    b'{"error":"identidad_no_autentica","detail":"La identidad de la '
                    b'peticion no viene firmada por el gateway.","status":401}',
                )
                return
            if verificada is not None:
                # Solo DESPUÉS de verificar: sin el secreto nadie puede llenar el
                # registro ni quemar nonces ajenos.
                try:
                    primer_uso = await _registro_activo().reclamar(
                        clave_nonce(verificada.tenant, verificada.nonce),
                        ttl_nonce(verificada, time.time()),
                    )
                except RegistroNoDisponible:
                    logger.error("identidad_registro_no_disponible", exc_info=True)
                    await _responder(
                        send,
                        503,
                        b'{"error":"identidad_no_verificable","detail":"No se puede '
                        b'verificar que la identidad no se haya usado antes. Reintente.",'
                        b'"status":503}',
                        extra=((b"retry-after", b"1"),),
                    )
                    return
                if not primer_uso:
                    logger.warning(
                        "identidad_reutilizada tenant=%s ruta=%s nonce=%s",
                        verificada.tenant,
                        scope["path"],
                        hashlib.sha256(verificada.nonce.encode()).hexdigest()[:12],
                    )
                    await _responder(
                        send,
                        401,
                        b'{"error":"identidad_no_autentica","detail":"La identidad de la '
                        b'peticion no viene firmada por el gateway.","status":401}',
                    )
                    return

        await self.app(scope, receive, send)


async def _responder(
    send: Any, status: int, cuerpo: bytes, extra: tuple[tuple[bytes, bytes], ...] = ()
) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(cuerpo)).encode()),
                *extra,
            ],
        }
    )
    await send({"type": "http.response.body", "body": cuerpo})
