# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El limitador no puede gastar en el cubo de terceros el tráfico que ya rechazó.

Estas pruebas usan un doble de Redis que CUENTA de verdad, no uno que devuelve un
número fijo. La diferencia importa: el defecto que cubren no está en qué responde
el limitador a una petición, sino en el EFECTO ACUMULADO de muchas sobre un
contador compartido. Un mock que siempre contesta lo mismo no puede distinguir
«sumó al global» de «no sumó», que es justo la afirmación a demostrar.
"""

from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app

from .conftest import wire_upstream


class RedisQueCuenta:
    """Doble de Redis que lleva las cuentas reales por clave.

    Reproduce solo lo que el limitador usa: `pipeline()`, `incr`, `expire` y
    `execute`, devolviendo los valores en el orden en que se encolaron.
    """

    def __init__(self) -> None:
        self.contadores: dict[str, int] = {}
        self._encolado: list[tuple[str, str]] = []

    def pipeline(self) -> "RedisQueCuenta":
        self._encolado = []
        return self

    def incr(self, clave: str) -> "RedisQueCuenta":
        self._encolado.append(("incr", clave))
        return self

    def expire(self, clave: str, _segundos: int) -> "RedisQueCuenta":
        self._encolado.append(("expire", clave))
        return self

    async def execute(self) -> list:
        salida: list = []
        for operacion, clave in self._encolado:
            if operacion == "incr":
                self.contadores[clave] = self.contadores.get(clave, 0) + 1
                salida.append(self.contadores[clave])
            else:
                salida.append(True)
        self._encolado = []
        return salida

    def global_actual(self) -> int:
        return sum(v for k, v in self.contadores.items() if k.startswith("ratelimit:global:"))


@pytest.fixture
async def cliente_con_redis_real():
    redis = RedisQueCuenta()
    http_client = AsyncMock()
    # El proxy es real: sin un upstream cableado la petición revienta antes de
    # llegar a la aserción, y el test mediría el doble en vez del limitador.
    wire_upstream(http_client, httpx.Response(status_code=200, json={"ok": True}))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        app.state.http_client = http_client
        app.state.redis = redis
        app.state.trusted_proxies = settings.trusted_proxies
        app.state.rate_limit_per_ip = 3
        app.state.rate_limit_global = 1000
        app.state.rate_limit_pqrs_per_ip = 2
        app.state.auth_cache_ttl = settings.auth_cache_ttl
        app.state.max_request_body_bytes = settings.max_request_body_bytes
        for atributo in dir(settings):
            if atributo.endswith("_service_url"):
                setattr(app.state, atributo, getattr(settings, atributo))
        yield client, redis


async def test_lo_rechazado_no_suma_al_cubo_global(cliente_con_redis_real):
    """Un anónimo que agota SU cubo deja de contar para todos los demás.

    Es el hallazgo de fondo: con la versión anterior cada petición ya rechazada
    seguía empujando el contador compartido, así que una sola IP abusiva bastaba
    para dejar en 429 a los usuarios de todas las entidades.
    """
    client, redis = cliente_con_redis_real

    for _ in range(3):
        assert (await client.get("/api/v1/openapi.json")).status_code != 429

    global_tras_las_permitidas = redis.global_actual()

    for _ in range(20):
        respuesta = await client.get("/api/v1/openapi.json")
        assert respuesta.status_code == 429

    assert redis.global_actual() == global_tras_las_permitidas, (
        "las peticiones rechazadas siguieron sumando al cubo global: una sola IP "
        "puede dejar sin servicio a todas las entidades"
    )


async def test_la_ventanilla_vigente_tiene_el_cubo_estrecho(cliente_con_redis_real):
    """La puerta anónima que consume numeración es la ventanilla, no solo PQRS.

    El tope estricto vigilaba únicamente la ruta legada. La vigente —la que
    además acepta anexos— heredaba el cubo general, cien veces más ancho.
    """
    client, redis = cliente_con_redis_real

    for _ in range(2):
        respuesta = await client.post("/api/v1/public/demo/ventanilla", json={})
        assert respuesta.status_code != 429

    respuesta = await client.post("/api/v1/public/demo/ventanilla", json={})
    assert respuesta.status_code == 429, (
        "la ventanilla anónima no está bajo el cubo estrecho: acepta el tope "
        "general y permite quemar numeración oficial en bucle"
    )
    assert any(k.startswith("ratelimit:pqrs:") for k in redis.contadores), (
        "la ventanilla no comparte el cubo estrecho de radicación anónima"
    )


async def test_pqrs_sigue_bajo_el_cubo_estrecho(cliente_con_redis_real):
    """Ampliar la lista no puede haber soltado la puerta que ya estaba vigilada."""
    client, _ = cliente_con_redis_real

    for _ in range(2):
        assert (await client.post("/api/v1/public/demo/pqrs", json={})).status_code != 429

    assert (await client.post("/api/v1/public/demo/pqrs", json={})).status_code == 429


async def test_la_salud_no_entra_en_el_cubo_global(cliente_con_redis_real):
    """El chequeo de salud no puede declarar enfermo a un gateway sano.

    Cuenta en su propio cubo por IP —no es una puerta libre— pero queda fuera del
    global, porque ahí la saturación la provoca un tercero.
    """
    client, redis = cliente_con_redis_real

    antes = redis.global_actual()
    for _ in range(3):
        assert (await client.get("/health")).status_code == 200

    assert redis.global_actual() == antes, (
        "el chequeo de salud suma al cubo global: la saturación provocada por un "
        "atacante volvería «no apto» a un gateway que responde correctamente"
    )


async def test_el_reto_antirrobots_es_anonimo_y_cae_en_el_cubo_general(cliente_con_redis_real):
    """C16 (ADR-038): `GET .../ventanilla/reto` NO es una alta de radicación.

    Emitir un reto es puro cálculo sin escritura, así que cae en el cubo anónimo
    GENERAL (aquí 3 por IP) y no en el estrecho de radicación (aquí 2): si entrara
    en el estrecho, quien va a radicar agotaría su cuota pidiendo el reto y el POST
    llegaría ya en 429. Y es público: no pide credenciales.
    """
    client, redis = cliente_con_redis_real

    for _ in range(3):
        respuesta = await client.get("/api/v1/public/demo/ventanilla/reto")
        assert respuesta.status_code == 200, "el reto es anónimo: no pide credenciales"

    assert not any(k.startswith("ratelimit:pqrs:") for k in redis.contadores), (
        "el reto está contando en el cubo estrecho de radicación anónima"
    )
    # Control positivo: el POST de la misma ventanilla SÍ cuenta en el estrecho.
    await client.post("/api/v1/public/demo/ventanilla", json={})
    assert any(k.startswith("ratelimit:pqrs:") for k in redis.contadores)
