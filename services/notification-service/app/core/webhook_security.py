# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Validación del destino de webhooks salientes (hallazgo Media, auditoría de
seguridad — no bloqueante).

`WebhookCreate.url` (schema Pydantic) solo validaba la FORMA de la URL, nunca
su DESTINO: un administrador podía suscribir un webhook a un servicio interno
de la red Docker (`postgres`, `keycloak`, `document-service`, ...) o a un
rango no enrutable (loopback/link-local/privado). No es explotable como SSRF
clásico (POST con cuerpo fijo, sin cabeceras controlables por el atacante, sin
redirects, ciego — `_deliver_one` nunca devuelve la respuesta al llamante),
pero sí permite dirigir 3 reintentos × evento contra un servicio interno
arbitrario: amplificación/ruido contra la propia red.

## Dos momentos de validación, con distinta profundidad (decisión deliberada)

- **Al crear la suscripción** (`WebhookCreate`, ver `app/schemas/webhook.py`):
  validación RÁPIDA, sin resolución DNS — esquema, host literal (nombre de
  servicio Docker, `localhost`) e IP literal si el admin puso una IP directa.
  No resuelve nombres de dominio arbitrarios: hacerlo depende de red/DNS
  disponibles en el momento de un POST administrativo síncrono, y de todas
  formas no cerraría el rebinding (ver abajo). Produce 422 con el motivo.
- **En cada intento de entrega** (`webhook_service._deliver_one`): validación
  COMPLETA con resolución DNS real (`resolve=True`), justo antes de cada POST.
  Es aceptable en costo porque la entrega YA hace resolución DNS para
  conectar; repetirla en cada uno de los `_MAX_ATTEMPTS` reintentos cierra el
  caso de **DNS rebinding** (un dominio que resolvía a una IP pública al crear
  la suscripción y luego se re-apunta a una IP interna).

**Limitación aceptada y documentada, no cerrada**: existe una ventana TOCTOU
entre esta validación y la conexión real que hace `httpx` (milisegundos
después). Cerrarla del todo requeriría fijar (pin) la conexión a la IP ya
resuelta y validada en el momento del handshake TCP, lo cual excede el
alcance de este hallazgo Media no bloqueante. Se acepta el residual.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

# Nombres de servicio de la red Docker interna `orpycamcp-net` (ver
# docker-compose.yml) más sus `container_name`. Un webhook no debe poder
# apuntar a un compañero de red interno.
# La lista de compañeros de red y los rangos prohibidos viven en la librería
# compartida: son los MISMOS para toda salida configurable (webhook, buzón IMAP,
# sellado de tiempo). Tenerlos por duplicado hacía que las copias crecieran
# aparte, y la que se queda corta no avisa: deja pasar.
from orpycamcp_common.red_interna import (  # noqa: E402
    _DOCKER_SERVICE_HOSTNAMES,
    _classify_ip,
    _try_parse_ip,
)

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def validate_webhook_target(url: str, *, allow_private: bool, resolve: bool = True) -> None:
    """Rechaza destinos internos/no enrutables para un webhook saliente.

    Lanza `ValueError` con un mensaje que explica el motivo del rechazo (el
    llamante es un administrador legítimo configurando una integración, no un
    atacante al que haya que darle poca información).

    `allow_private=True` desactiva TODAS las restricciones salvo el esquema
    (kill-switch de desarrollo, ver `Settings.webhook_allow_private_targets`
    en `app/core/config.py`; por defecto es `False` — estricto).

    `resolve=False` omite la resolución DNS (usado en la validación de
    creación, ver el docstring del módulo).
    """
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"esquema '{parts.scheme}' no permitido para webhooks: use http o https"
        )
    host = parts.hostname
    if not host:
        raise ValueError("la URL del webhook no tiene un host válido")

    if allow_private:
        return

    host_lower = host.lower()
    if host_lower in _DOCKER_SERVICE_HOSTNAMES:
        raise ValueError(
            f"'{host}' es un nombre de servicio de la red interna de OrpycaMCP; "
            "los webhooks no pueden apuntar a servicios internos"
        )
    if host_lower == "localhost":
        raise ValueError("'localhost' no está permitido como destino de webhook (loopback)")

    literal_ip = _try_parse_ip(host)
    if literal_ip is not None:
        reason = _classify_ip(literal_ip)
        if reason is not None:
            raise ValueError(
                f"el host '{host}' es una IP en {reason}, no permitida como destino de webhook"
            )
        return

    if not resolve:
        return

    try:
        addrinfo = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"no fue posible resolver el host '{host}': {exc}") from None

    for _family, _type, _proto, _canon, sockaddr in addrinfo:
        ip = ipaddress.ip_address(sockaddr[0])
        reason = _classify_ip(ip)
        if reason is not None:
            raise ValueError(
                f"el host '{host}' resuelve a {ip} ({reason}), no permitido como "
                "destino de webhook"
            )
