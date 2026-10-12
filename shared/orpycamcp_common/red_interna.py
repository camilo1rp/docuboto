# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Qué destinos de red NO puede alcanzar un servicio por orden de un usuario.

Vive aquí, y no dentro del servicio que primero lo necesitó, porque la lista de
compañeros de red interna y los rangos prohibidos son **los mismos** para toda
salida configurable: el webhook saliente, el buzón IMAP de una entidad, el
proveedor de sellado de tiempo. Cuando esa lista se duplica, las copias crecen
aparte —a este proyecto ya le ha pasado tres veces con listas de admisión— y la
que se queda corta no avisa: simplemente deja pasar.

Un contenedor comprometido no es el modelo de amenaza. Lo es el administrador de
una entidad, que es un rol de NEGOCIO: puede configurar a dónde se conecta el
sistema, y sin este filtro eso equivale a operar un escáner de puertos desde
dentro de la red de contenedores, con las respuestas devueltas por la propia
interfaz de administración.
"""

from __future__ import annotations

import ipaddress
import socket

_DOCKER_SERVICE_HOSTNAMES = frozenset(
    {
        "postgres",
        "redis",
        "minio",
        "keycloak",
        "api-gateway",
        "mcp-server",
        "signature-service",
        "knowledge-service",
        "auth-service",
        "tenant-service",
        "document-service",
        "archive-service",
        "storage-service",
        "workflow-service",
        "notification-service",
        "frontend",
        "mailpit",
        "orpycamcp-postgres",
        "orpycamcp-redis",
        "orpycamcp-minio",
        "orpycamcp-keycloak",
        "orpycamcp-mailpit",
    }
)



class DestinoNoPermitido(ValueError):
    """El destino cae en la red interna o en un rango no enrutable."""


def _classify_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Devuelve el motivo de bloqueo si `ip` cae en un rango prohibido, si no None."""
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local"
    if ip.is_private:
        return "rango privado"
    if ip.is_reserved or ip.is_unspecified or ip.is_multicast:
        return "rango reservado/no enrutable"
    return None


def _try_parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None



def validar_destino(host: str, *, permitir_privado: bool = False, resolver: bool = True) -> None:
    """Lanza `DestinoNoPermitido` si *host* apunta hacia dentro.

    Se comprueba en DOS momentos y por eso acepta `resolver`: barato al guardar
    la configuración (sin DNS, para dar un error inmediato a quien la escribe) y
    de nuevo con resolución antes de cada conexión, que es lo que cierra el
    reenlace de DNS. Queda el residual ya declarado para los webhooks: entre la
    resolución y el `connect` cabe una carrera; cerrarla exige fijar la conexión
    a la IP ya validada.
    """
    limpio = (host or "").strip().strip("[]").lower()
    if not limpio:
        raise DestinoNoPermitido("No se indicó ningún destino.")

    if permitir_privado:
        return

    if limpio in _DOCKER_SERVICE_HOSTNAMES:
        raise DestinoNoPermitido(
            f"'{host}' es un servicio interno del despliegue y no es un destino válido."
        )

    literal = _try_parse_ip(limpio)
    if literal is not None:
        motivo = _classify_ip(literal)
        if motivo:
            raise DestinoNoPermitido(f"'{host}' apunta a un {motivo}, no a un destino externo.")
        return

    if not resolver:
        return

    try:
        infos = socket.getaddrinfo(limpio, None)
    except OSError as exc:
        raise DestinoNoPermitido(f"No se pudo resolver '{host}'.") from exc

    for info in infos:
        ip = _try_parse_ip(str(info[4][0]))
        if ip is None:
            continue
        motivo = _classify_ip(ip)
        if motivo:
            raise DestinoNoPermitido(
                f"'{host}' resuelve a un {motivo}, no a un destino externo."
            )
