# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Dispatcher de tools MCP (E18, ADR-019).

Traduce la invocación de una tool a una llamada HTTP al api-gateway, propagando el
token Keycloak del usuario y el tenant. No contiene lógica de negocio: compone
contratos REST existentes. La autorización real la resuelve la BD vía gateway
(ADR-013); aquí solo se filtra la visibilidad/gate por permisos del usuario.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.tools.catalog import ArgError, Tool, coerce_args

_SIN_CUERPO = ("GET", "DELETE", "HEAD")


class DispatchError(Exception):
    def __init__(self, code: str, status: int) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


@dataclass(frozen=True)
class PreparedCall:
    """Una llamada HTTP ya resuelta: método, ruta, query y cuerpo.

    Separar la preparación del envío permite firmar exactamente lo que se va a
    ejecutar (bloque de propuestas) y probar el reparto de argumentos sin red.
    """

    method: str
    path: str
    query: dict[str, Any]
    body: dict[str, Any] | None


def _forward_headers(authorization: str | None, tenant_slug: str | None) -> dict:
    headers: dict[str, str] = {}
    if authorization:
        headers["Authorization"] = authorization
    if tenant_slug:
        headers["X-Tenant-Slug"] = tenant_slug
    return headers


def authorize(
    tool: Tool,
    *,
    permissions: set[str],
    is_root: bool,
    levels: dict[str, int] | None = None,
) -> None:
    """Aplica el scope por permiso **y nivel** antes de invocar.

    Ya NO recibe `confirm_write`. Ese booleano era el gate de escritura entero, y
    cualquier cliente podía ponerlo: confirmaba «algo», no «esto». El binding MCP
    directamente lo daba por puesto. Ahora una escritura no llega a ejecutarse por
    esta vía —devuelve una propuesta firmada— y la confirmación es un compromiso
    sobre un acto concreto, con su usuario y su ventana.

    Quitar el parámetro es deliberado: mientras existiera, alguien podría volver a
    pasar `True` y saltarse el mecanismo sin que nada lo delatara.
    """
    if tool.permission and not is_root:
        if tool.permission not in permissions:
            raise DispatchError("permiso_insuficiente", 403)
        if levels is not None and levels.get(tool.permission, 0) < tool.min_crud:
            # El permiso está, pero no con nivel suficiente. Mismo código: para el
            # llamante ambos casos son «no autorizado», y distinguirlos aquí sería
            # un oráculo sobre la configuración de permisos ajena.
            raise DispatchError("permiso_insuficiente", 403)


def build_request(tool: Tool, args: dict) -> PreparedCall:
    """Valida los argumentos y resuelve la llamada, sin enviarla.

    **Los parámetros de ruta se escapan.** Antes se interpolaban con `str()`
    crudo, y eso era un traversal explotable, no una cuestión de higiene: un
    `document_id` con `../../auth/me` hacía que el mcp-server invocara **otro
    endpoint con el token del usuario**. Es el mismo defecto de normalización que
    se cerró en el gateway, vivo un salto más allá. `quote(safe="")` convierte
    `../../auth/me` en `..%2F..%2Fauth%2Fme`, que el gateway resuelve a 404.

    La validación de `coerce_args` es la primera capa —un `format="uuid"` no
    llega siquiera a `quote`— y el escape es la segunda: ninguna de las dos
    depende de que la otra esté puesta.
    """
    try:
        limpio = coerce_args(tool, dict(args))
    except ArgError as exc:
        # 400, no 422: para el llamante MCP esto es una invocación mal formada,
        # y el código estable (`parametro_no_declarado`, `valor_invalido`) es lo
        # que permite al modelo corregirse sin adivinar.
        raise DispatchError(exc.code, 400) from exc

    por_nombre = tool.params_by_name
    path = tool.path
    query: dict[str, Any] = {}
    body: dict[str, Any] = {}

    for nombre, valor in limpio.items():
        p = por_nombre[nombre]
        destino = p.location
        if destino == "auto":
            destino = "path" if f"{{{nombre}}}" in tool.path else "query"
            if destino == "query" and tool.method not in _SIN_CUERPO:
                destino = "body"
        if destino == "path":
            path = path.replace(f"{{{nombre}}}", quote(str(valor), safe=""))
        elif destino == "query":
            query[nombre] = valor
        else:
            body[nombre] = valor

    # Un `{x}` sin valor dejaría la plantilla en la ruta y produciría un 404
    # incomprensible; se detecta aquí, con el nombre del parámetro que falta.
    for p_name in tool.path_params:
        if f"{{{p_name}}}" in path:
            raise DispatchError(f"falta_parametro:{p_name}", 400)

    return PreparedCall(
        method=tool.method.upper(),
        path=path,
        query=query,
        # DELETE nunca lleva cuerpo: hay servidores y proxies que lo descartan en
        # silencio, y una petición cuyo significado depende de algo que puede
        # desaparecer por el camino no es una petición fiable.
        body=None if tool.method.upper() in _SIN_CUERPO else body,
    )


async def execute(
    client: httpx.AsyncClient,
    prepared: PreparedCall,
    *,
    authorization: str | None,
    tenant_slug: str | None,
    idempotency_key: str | None = None,
) -> httpx.Response:
    """Envía la llamada ya preparada."""
    headers = _forward_headers(authorization, tenant_slug)
    if idempotency_key:
        # Hoy no la honra ningún servicio (verificado). Se emite igual porque es
        # inocua y es el gancho para que cada épica la implemente; se documenta
        # como pendiente, no como garantía.
        headers["Idempotency-Key"] = idempotency_key
    return await client.request(
        prepared.method,
        prepared.path,
        params=prepared.query or None,
        json=prepared.body if prepared.body is not None else None,
        headers=headers,
    )


async def dispatch(
    client: httpx.AsyncClient,
    tool: Tool,
    args: dict,
    *,
    authorization: str | None,
    tenant_slug: str | None,
) -> httpx.Response:
    """Prepara y envía. Se conserva porque es lo que consumen los tres transportes."""
    prepared = build_request(tool, args)
    return await execute(
        client, prepared, authorization=authorization, tenant_slug=tenant_slug
    )
