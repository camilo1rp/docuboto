# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Identidad del llamante: una sola resolución para todos los transportes.

**El problema que cierra este módulo.** Había tres caminos distintos para la misma
pregunta —«¿quién es y qué puede hacer?»— y dos de ellos no funcionaban:

1. `routers/mcp.py` y el transporte MCP streamable leían la cabecera
   `X-User-Permissions`. Esa cabecera **no la produce nadie**: el api-gateway
   inyecta `X-User-Id`, `X-Username`, `X-Tenant-Slug`, `X-User-Roles`, `X-Canal` y
   `X-Real-Ip`, y su allowlist de reenvío la descarta si la manda un cliente
   (bien: sería forjable). Llegaba siempre vacía, así que **toda tool con permiso
   quedaba invisible salvo para ROOT**. Solo aparecía poblada en los tests, que la
   fabricaban a mano y por eso no detectaban nada.
2. El asistente conversacional (retirado en D0, ADR-046 §6) sí lo hacía bien: `GET /api/v1/auth/me` con el token
   del usuario, que devuelve `permissions` **y** `permission_levels`.

Este módulo generaliza (2) y elimina (1). La identidad sale siempre del token
validado, nunca de una cabecera que el cliente controla.

**Por qué también `permission_levels`.** El sistema autoriza por permiso *y* por
nivel (`crud` 0..5; las escrituras exigen 3). Con la lista plana no se puede saber
si alguien tiene `PERM_RADI` para *leer* o para *crear*, y ofrecerle una tool que
no puede ejecutar produce un 403 al pulsar — el defecto que ya se corrigió en la
interfaz web.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from app.services import origen


class IdentityError(Exception):
    """Fallo al resolver identidad/permisos contra el auth-service (vía gateway)."""

    def __init__(self, status: int, code: str) -> None:
        self.status = status
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Identity:
    user_id: str
    tenant_slug: str
    permissions: set[str] = field(default_factory=set)
    levels: dict[str, int] = field(default_factory=dict)
    is_root: bool = False
    #: ¿La entidad activó el asistente? (ADR-037). Viene de `/auth/me`; ausente = `False`.
    #: Sirve para MOSTRAR u ocultar y para negar temprano: knowledge-service vuelve a
    #: leerlo de la base en cada consulta, así que un dato viejo aquí no abre nada.
    asistente_habilitado: bool = False


# **Sin caché, a propósito.** El plan contemplaba cachear la identidad por hash del
# token con TTL de 60 s para ahorrar un viaje en el par proponer/ejecutar. Al
# implementarlo, el coste resultó mayor que el ahorro: una caché de permisos abre
# una ventana en la que un permiso REVOCADO sigue surtiendo efecto, y este trabajo
# va precisamente de que la autorización sea explícita y esté al día. Un TTL corto
# tampoco servía: entre proponer y ejecutar media el tiempo que tarda una persona
# en leer la tarjeta.
#
# El coste que se acepta a cambio son dos llamadas a `/auth/me` por acto, contra un
# servicio de la misma red. ADR-019 ya asume el salto extra: esto es una capa de
# asistencia, no de alto rendimiento. Si algún día pesa, la solución es que el
# gateway inyecte los permisos ya validados, no cachearlos aquí.


def _codigo_de_entidad(resp: httpx.Response) -> str | None:
    """`tenant_inactivo` / `tenant_*` del cuerpo de error, o `None`.

    Solo códigos del estado de la entidad (lista cerrada): lo demás no se reenvía.
    """
    try:
        cuerpo = resp.json()
    except ValueError:
        return None
    if isinstance(cuerpo, dict) and isinstance(cuerpo.get("detail"), dict):
        cuerpo = cuerpo["detail"]
    codigo = cuerpo.get("error") if isinstance(cuerpo, dict) else None
    if codigo in _CODIGOS_DE_ENTIDAD:
        return str(codigo)
    return None


#: Códigos de error del estado de la entidad (ADR-035): únicos que se propagan.
_CODIGOS_DE_ENTIDAD = frozenset(
    {"tenant_inactivo", "tenant_no_provisionado", "tenant_estado_no_disponible"}
)


async def resolve_identity(
    gateway_client: httpx.AsyncClient,
    *,
    authorization: str,
    tenant_slug: str,
) -> Identity:
    """Resuelve la identidad del portador vía `GET /api/v1/auth/me`.

    Fail-closed en todos los caminos: sin `user_id` no se puede aislar el historial
    ni atar una propuesta a su autor, así que se rechaza en vez de colapsar dos
    usuarios en una misma llave.
    """
    try:
        resp = await gateway_client.get(
            "/api/v1/auth/me",
            headers=origen.cabeceras_de_origen(authorization, tenant_slug),
        )
    except httpx.RequestError as exc:
        raise IdentityError(502, "gateway_unreachable") from exc

    if resp.status_code == 401:
        raise IdentityError(401, "unauthenticated")
    if resp.status_code != 200:
        # ADR-035 (B9-N4): una entidad no operativa se responde con el MISMO código que
        # el gateway (`tenant_inactivo` 403; `tenant_*` 503), no con un genérico
        # `auth_service_error` que el cliente no sabe distinguir de una caída.
        codigo = _codigo_de_entidad(resp)
        if codigo is not None and resp.status_code in (403, 503):
            raise IdentityError(resp.status_code, codigo)
        raise IdentityError(503, "auth_service_error")

    data = resp.json()
    user_id = str(data.get("user_id") or "")
    if not user_id:
        raise IdentityError(401, "unauthenticated")

    # `permission_levels` puede venir vacío (superadmin sin tenant, usuario no
    # aprovisionado): el auth-service lo deja vacío, nunca ausente ni nulo. Un
    # nivel ausente vale 0 al comprobarse, que es el lado seguro.
    niveles_crudos = data.get("permission_levels") or {}
    levels = {str(k): int(v) for k, v in niveles_crudos.items() if isinstance(v, int | float)}

    return Identity(
        user_id=user_id,
        tenant_slug=str(data.get("tenant_slug") or tenant_slug),
        permissions=set(data.get("permissions") or []),
        levels=levels,
        # ROOT = `USUA_PERM_ROOT` con nivel > 0 en `permission_levels` (así lo emite
        # auth-service, con 5). El rol JWT «ROOT» no existe en el realm.
        is_root=levels.get("USUA_PERM_ROOT", 0) > 0,
        # Solo un `true` literal enciende: «1», «sí» o un objeto no cuentan.
        asistente_habilitado=data.get("asistente_habilitado") is True,
    )
