# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""
Cabeceras con las que el mcp-server habla con el api-gateway.

Un solo sitio para TODAS las llamadas salientes (despacho de tools, `/auth/me`,
lecturas del marco de alcance): si cada módulo armara las suyas, el que se olvidara
de una cabecera nueva fallaría solo en la ruta que menos se prueba.

Además de `Authorization` y `X-Tenant-Slug`:

- **`X-Origen-Canal`** (AREA-11-04): marca firmada con el secreto de identidad,
  ligada al tenant y al token. Es lo que permite al gateway asentar `canal='mcp'`
  en lo que el asistente hace en nombre de una persona; sin ella, un radicado
  confirmado desde la tarjeta quedaba como 'api', igual que una llamada directa.
- **`X-Request-Id`**: el de la petición entrante, para que el turno y las llamadas
  que dispara compartan un hilo de traza.
- **`X-Forwarded-For`** (AREA-11-08): la IP del cliente real. Sin ella todas las
  llamadas del asistente de TODAS las entidades caen en el cubo por IP del
  contenedor del mcp-server (el problema del NAT, un salto más adentro). Solo se
  reenvía la IP que el gateway firmó con la identidad (`ip_real_firmada`), no una
  cabecera suelta.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from orpycamcp_common import identidad
from orpycamcp_common.request_log import request_id_actual

#: IP del cliente real de la petición en curso (la firmada por el gateway) o None.
_ip_cliente: ContextVar[str | None] = ContextVar("mcp_ip_cliente", default=None)

#: Contador de llamadas salientes del turno en curso (None = no se cuenta).
_conteo: ContextVar[list[int] | None] = ContextVar("mcp_conteo_llamadas", default=None)


def fijar_ip_cliente(ip: str | None) -> Token:
    return _ip_cliente.set(ip)


def soltar_ip_cliente(token: Token) -> None:
    _ip_cliente.reset(token)


def iniciar_conteo() -> list[int]:
    """Empieza a contar las llamadas al gateway del contexto actual."""
    contador = [0]
    _conteo.set(contador)
    return contador


def cabeceras_de_origen(authorization: str | None, tenant_slug: str | None) -> dict[str, str]:
    """Cabeceras de toda llamada saliente hacia el gateway."""
    contador = _conteo.get()
    if contador is not None:
        contador[0] += 1

    headers: dict[str, str] = {}
    if authorization:
        headers["Authorization"] = authorization
    if tenant_slug:
        headers["X-Tenant-Slug"] = tenant_slug
    headers.update(
        identidad.firmar_origen("mcp", tenant=tenant_slug or "", authorization=authorization or "")
    )
    request_id = request_id_actual()
    if request_id:
        headers["X-Request-Id"] = request_id
    ip = _ip_cliente.get()
    if ip:
        headers["X-Forwarded-For"] = ip
    return headers
