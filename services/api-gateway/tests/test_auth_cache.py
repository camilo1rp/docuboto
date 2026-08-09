# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Caché de la validación de token en el gateway.

El gateway llamaba a `GET /auth/validate` en TODA petición no pública. Eso hacía
de auth-service un punto único de fallo síncrono —y el cuello de botella del
sistema— pese a no tener réplicas, mientras gateway, document y storage sí las
tienen. Y cada llamada escribía un asiento `token_valid` en `public.audit_log`
tomando un advisory lock por tenant: una fila por request en un registro con
valor probatorio.
"""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.middleware.auth import _ttl_validacion, _validation_key
from tests.conftest import make_validate_response, wire_upstream

pytestmark = pytest.mark.asyncio

_AUTH = {"Authorization": "Bearer tok-123", "X-Tenant-Slug": "icetex"}


class _RedisFalso:
    """Redis mínimo pero REAL en su semántica: guarda y devuelve bytes.

    El `AsyncMock` genérico del conftest devuelve otro `AsyncMock` en `get()`,
    que es truthy y hace estallar el `json.loads` — el fallo queda absorbido por
    el `except` y la caché nunca se ejercita. Un doble que no se comporta como la
    cosa real convierte estos tests en decoración.
    """

    def __init__(self) -> None:
        self.datos: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, clave):
        return self.datos.get(clave)

    async def set(self, clave, valor, ex=None):
        self.datos[clave] = valor
        if ex is not None:
            self.ttls[clave] = ex

    def pipeline(self):  # el limitador de tasa también toca este objeto
        raise NotImplementedError

    async def aclose(self):
        return None


async def test_la_segunda_peticion_no_vuelve_a_preguntar(test_client) -> None:
    """Con la validación cacheada, auth-service recibe UNA sola llamada."""
    client, http_client, _ = test_client
    app = client._transport.app  # type: ignore[attr-defined]
    app.state.redis = _RedisFalso()
    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    wire_upstream(http_client, httpx.Response(200, json={"ok": True}))

    primera = await client.get("/api/v1/documents/", headers=_AUTH)
    segunda = await client.get("/api/v1/documents/", headers=_AUTH)

    assert primera.status_code == segunda.status_code == 200
    assert http_client.get.await_count == 1, "la segunda debía servirse de la caché"


async def test_el_token_no_se_guarda_en_claro(test_client) -> None:
    """La clave es el SHA-256 del encabezado, no el token.

    Redis no necesita el token en claro para nada, y un volcado de Redis no debe
    entregar credenciales vivas.
    """
    client, http_client, _ = test_client
    app = client._transport.app  # type: ignore[attr-defined]
    redis = _RedisFalso()
    app.state.redis = redis
    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    wire_upstream(http_client, httpx.Response(200, json={"ok": True}))

    await client.get("/api/v1/documents/", headers=_AUTH)

    guardado = " ".join(redis.datos.keys()) + " " + " ".join(redis.datos.values())
    assert "tok-123" not in guardado
    assert _validation_key("Bearer tok-123") in redis.datos


async def test_un_token_invalido_no_se_cachea(test_client) -> None:
    """Solo se cachea el SÍ.

    Cachear el NO ahorra poco y retrasa el momento en que un token recién emitido
    empieza a funcionar.
    """
    client, http_client, _ = test_client
    app = client._transport.app  # type: ignore[attr-defined]
    redis = _RedisFalso()
    app.state.redis = redis
    http_client.get = AsyncMock(return_value=make_validate_response(valid=False))

    respuesta = await client.get("/api/v1/documents/", headers=_AUTH)

    assert respuesta.status_code == 401
    assert redis.datos == {}


async def test_la_identidad_de_red_se_recalcula_aunque_haya_cache(test_client) -> None:
    """`X-Real-Ip` es de la PETICIÓN, no del token.

    Si viniera cacheada junto al resto de la identidad, todas las peticiones de un
    mismo token quedarían selladas en el `audit_log` con la IP de la primera.
    """
    client, http_client, _ = test_client
    app = client._transport.app  # type: ignore[attr-defined]
    app.state.redis = _RedisFalso()
    # SOLO la dirección del transporte de test, no `0.0.0.0/0`: con todo declarado
    # de confianza no queda ningún salto no-confiable en la cadena y la
    # implementación devuelve el socket — que es su comportamiento correcto, y por
    # eso una allowlist demasiado ancha no sirve para este test.
    app.state.trusted_proxies = "127.0.0.1/32"
    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    wire_upstream(http_client, httpx.Response(200, json={"ok": True}))

    await client.get("/api/v1/documents/", headers={**_AUTH, "X-Forwarded-For": "1.1.1.1"})
    primera = http_client.build_request.call_args.kwargs["headers"]["X-Real-Ip"]

    await client.get("/api/v1/documents/", headers={**_AUTH, "X-Forwarded-For": "2.2.2.2"})
    segunda = http_client.build_request.call_args.kwargs["headers"]["X-Real-Ip"]

    assert primera != segunda
    assert (primera, segunda) == ("1.1.1.1", "2.2.2.2")


# ---------------------------------------------------------------------------
# El TTL
# ---------------------------------------------------------------------------


def test_el_ttl_nunca_sobrevive_al_token() -> None:
    """Un token que caduca en 3 s no puede quedar cacheado 15 s.

    Sin este acotado, la caché **resucitaría** tokens caducados durante su
    ventana, que es exactamente lo contrario de lo que una caché de autorización
    puede permitirse.
    """
    assert _ttl_validacion({"expires_in": 3}, 15) == 3


def test_el_ttl_no_excede_el_maximo_configurado() -> None:
    """Un token de larga vida no amplía la ventana de revocación.

    El TTL ES la ventana durante la cual un token revocado se seguiría aceptando;
    por eso lo manda el mínimo de los dos.
    """
    assert _ttl_validacion({"expires_in": 3600}, 15) == 15


@pytest.mark.parametrize("payload", [{}, {"expires_in": None}, {"expires_in": 0}, {"expires_in": -5}])
def test_sin_expires_in_utilizable_se_usa_el_maximo(payload: dict) -> None:
    assert _ttl_validacion(payload, 15) == 15
