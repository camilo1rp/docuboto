# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""B9-1 (ADR-035): el gateway hace cumplir el estado de la entidad.

`/auth/validate` informa `tenant_estado`; una entidad suspendida o sin aprovisionar
no llega al upstream, ni por el camino sin caché ni por el de la caché caliente, y
un `validate` que no informa el campo (un auth-service viejo) NO abre la puerta.
"""

from unittest.mock import AsyncMock

import httpx
import pytest

from tests.conftest import make_validate_response, wire_upstream

TOKEN = "Bearer valid-token"
_ENTIDAD = {"X-Tenant-Slug": "icetex"}


class _RedisCache:
    """Redis mínimo con `get`/`set` en memoria (la caché de la validación)."""

    def __init__(self, redis_base) -> None:
        self.datos: dict[str, str] = {}
        self.set_llamadas = 0
        self._base = redis_base

    async def get(self, clave: str):
        return self.datos.get(clave)

    async def set(self, clave: str, valor: str, ex: int | None = None):
        self.set_llamadas += 1
        self.datos[clave] = valor

    def __getattr__(self, nombre: str):
        return getattr(self._base, nombre)


def _llamadas_al_upstream(http_client) -> int:
    return http_client.build_request.call_count


async def _pedir(client):
    return await client.get(
        "/api/v1/documents/", headers={"Authorization": TOKEN, **_ENTIDAD}
    )


@pytest.mark.parametrize(
    ("estado", "http", "error"),
    [
        ("inactiva", 403, "tenant_inactivo"),
        ("no_provisionada", 503, "tenant_no_provisionado"),
        ("desconocido", 503, "tenant_estado_no_disponible"),
        (None, 503, "tenant_estado_no_disponible"),  # auth-service viejo: campo ausente
        ("cualquier-otra-cosa", 503, "tenant_estado_no_disponible"),
    ],
)
async def test_una_entidad_no_operativa_no_llega_al_upstream(
    test_client, estado, http, error
) -> None:
    client, http_client, _ = test_client
    http_client.get = AsyncMock(return_value=make_validate_response(tenant_estado=estado))
    wire_upstream(http_client, httpx.Response(status_code=200, json={"ok": True}))

    resp = await _pedir(client)

    assert resp.status_code == http
    assert resp.json()["error"] == error
    assert resp.json()["status"] == http
    assert _llamadas_al_upstream(http_client) == 0


async def test_una_entidad_operativa_sigue(test_client) -> None:
    client, http_client, _ = test_client
    http_client.get = AsyncMock(return_value=make_validate_response(tenant_estado="operativa"))
    wire_upstream(http_client, httpx.Response(status_code=200, json={"ok": True}))

    resp = await _pedir(client)

    assert resp.status_code == 200
    assert _llamadas_al_upstream(http_client) == 1


async def test_sin_tenant_en_el_token_sigue_sin_estado(test_client) -> None:
    """Superadmin sin entidad: los servicios exigen la cabecera; el gateway no veta."""
    client, http_client, _ = test_client
    http_client.get = AsyncMock(
        return_value=make_validate_response(tenant_slug="", tenant_estado=None)
    )
    wire_upstream(http_client, httpx.Response(status_code=200, json={"ok": True}))

    resp = await _pedir(client)

    assert resp.status_code == 200


@pytest.mark.parametrize("estado", ["inactiva", "no_provisionada"])
async def test_el_mismo_veredicto_con_la_cache_caliente(test_client, estado) -> None:
    """La segunda petición sale de la caché y debe pasar por el MISMO gate."""
    client, http_client, redis = test_client
    cache = _RedisCache(redis)
    from app.main import app

    app.state.redis = cache
    http_client.get = AsyncMock(return_value=make_validate_response(tenant_estado=estado))
    wire_upstream(http_client, httpx.Response(status_code=200, json={"ok": True}))

    primera = await _pedir(client)
    segunda = await _pedir(client)

    assert cache.set_llamadas == 1, "el estado no operativo SÍ se cachea (TTL corto)"
    assert http_client.get.await_count == 1, "la segunda salió de la caché"
    assert primera.status_code == segunda.status_code
    assert primera.json() == segunda.json()
    assert _llamadas_al_upstream(http_client) == 0


async def test_el_estado_desconocido_no_se_cachea(test_client) -> None:
    """Un fallo transitorio de la base no debe bloquear a la entidad todo el TTL."""
    client, http_client, redis = test_client
    cache = _RedisCache(redis)
    from app.main import app

    app.state.redis = cache
    http_client.get = AsyncMock(
        return_value=make_validate_response(tenant_estado="desconocido")
    )
    wire_upstream(http_client, httpx.Response(status_code=200, json={"ok": True}))

    resp = await _pedir(client)

    assert resp.status_code == 503
    assert cache.set_llamadas == 0


async def test_la_reactivacion_surte_efecto_al_expirar_la_cache(test_client) -> None:
    client, http_client, redis = test_client
    cache = _RedisCache(redis)
    from app.main import app

    app.state.redis = cache
    http_client.get = AsyncMock(return_value=make_validate_response(tenant_estado="inactiva"))
    assert (await _pedir(client)).status_code == 403

    cache.datos.clear()  # expira el TTL
    http_client.get = AsyncMock(return_value=make_validate_response(tenant_estado="operativa"))
    wire_upstream(http_client, httpx.Response(status_code=200, json={"ok": True}))

    assert (await _pedir(client)).status_code == 200


# ---------------------------------------------------------------------------
# Huecos de la revisión de pruebas de B9
# ---------------------------------------------------------------------------


async def test_una_ruta_publica_con_token_de_entidad_suspendida_no_lo_toca(test_client) -> None:
    """`/api/v1/public/...` no valida credenciales: un Bearer de una entidad
    suspendida NO provoca `validate` ni un 403/503 del gateway. El veredicto
    neutro (404 indistinguible de «no existe») lo da el servicio; si el gateway
    respondiera `tenant_inactivo` aquí, delataría que la entidad existe."""
    client, http_client, _ = test_client
    http_client.get = AsyncMock(return_value=make_validate_response(tenant_estado="inactiva"))
    neutra = httpx.Response(
        status_code=404,
        json={"error": "not_found", "detail": "No encontrado", "status": 404},
    )
    wire_upstream(http_client, neutra)

    resp = await client.get(
        "/api/v1/public/icetex/verify/ABC", headers={"Authorization": TOKEN}
    )

    assert resp.status_code == 404
    assert resp.json() == {"error": "not_found", "detail": "No encontrado", "status": 404}
    assert http_client.get.await_count == 0, "una ruta pública no consulta /auth/validate"
    assert _llamadas_al_upstream(http_client) == 1


async def test_superadmin_sin_entidad_no_hereda_la_cabecera_de_una_suspendida(
    test_client,
) -> None:
    """Sin `tenant_slug` el gateway sigue (no hay estado que mirar), pero NO deja que
    el cliente elija entidad con `X-Tenant-Slug`: aguas abajo viaja vacío. Si pasara
    la cabecera del cliente, el superadmin operaría una entidad suspendida por un
    camino que el gate del borde no ve."""
    client, http_client, _ = test_client
    http_client.get = AsyncMock(
        return_value=make_validate_response(tenant_slug="", tenant_estado=None)
    )
    wire_upstream(http_client, httpx.Response(status_code=200, json={"ok": True}))

    resp = await client.get(
        "/api/v1/documents/",
        headers={"Authorization": TOKEN, "X-Tenant-Slug": "suspendida"},
    )

    assert resp.status_code == 200
    enviado = http_client.build_request.call_args.kwargs["headers"]
    por_nombre = {k.lower(): v for k, v in enviado.items()}
    assert por_nombre["x-tenant-slug"] == ""


async def test_una_entrada_de_cache_anterior_sin_el_campo_falla_cerrada(test_client) -> None:
    """Despliegue gradual: una entrada cacheada ANTES de B9 (sin `tenant_estado`)
    no abre la puerta durante lo que quede de TTL."""
    client, http_client, redis = test_client
    cache = _RedisCache(redis)
    from app.main import app

    app.state.redis = cache
    http_client.get = AsyncMock(return_value=make_validate_response(tenant_estado=None))
    wire_upstream(http_client, httpx.Response(status_code=200, json={"ok": True}))

    primera = await _pedir(client)
    segunda = await _pedir(client)

    assert primera.status_code == segunda.status_code == 503
    assert segunda.json()["error"] == "tenant_estado_no_disponible"
    assert _llamadas_al_upstream(http_client) == 0
