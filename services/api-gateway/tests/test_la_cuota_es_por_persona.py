# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La cuota de un usuario autenticado es SUYA, no de su dirección de red.

El defecto que cubren estas pruebas se encontró en la suite del frontend, y su
disfraz era perfecto: `asistente-dock.spec.js` fallaba en las tres corridas
completas y pasaba aislado, lo que se lee como un test inestable. No lo era. El
log del gateway traía la causa:

    "path": "/api/v1/assistant/alcance", "status": 429,
    "duracion_ms": 1.6, "tenant": null, "usuario": null

Los `null` y el 1,6 ms son el hallazgo: el limitador rechazaba **antes** de
autenticar, así que la cuota iba por IP. En una entidad pública lo normal es una
oficina entera detrás de un NAT, y una pantalla del sistema cuesta **7 peticiones
al gateway** (medido sobre tablero, bandeja, búsqueda, expedientes y borradores):
con el tope de 100/min, cincuenta funcionarios compartían **14 pantallas por
minuto**. El primero que trabajara deprisa dejaba a los demás con 429.

Se prueba con un doble de Redis que CUENTA de verdad, no uno que devuelve un
número fijo: la afirmación es sobre el efecto acumulado en claves distintas, y un
mock de respuesta constante no distingue «contó aparte» de «contó junto».
"""

from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app

from .conftest import make_validate_response, wire_upstream
from .test_limitador_no_gasta_el_cubo_ajeno import RedisQueCuenta

LIMITE_POR_USUARIO = 3


def _cabeceras(usuario: str) -> dict[str, str]:
    """Petición autenticada COMO ese usuario, desde una IP fija.

    La IP es la misma en todas las llamadas de todas las pruebas: es el punto
    entero del asunto — dos personas de la misma oficina salen por una sola
    dirección.
    """
    return {"Authorization": f"Bearer token-de-{usuario}", "X-Forwarded-For": "10.0.0.7"}


@pytest.fixture
async def cliente(monkeypatch):
    redis = RedisQueCuenta()
    http_client = AsyncMock()
    wire_upstream(http_client, httpx.Response(status_code=200, json={"ok": True}))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        app.state.http_client = http_client
        app.state.redis = redis
        app.state.trusted_proxies = settings.trusted_proxies
        # El cubo por IP se deja MUY holgado a propósito: si estrechara aquí, un
        # fallo suyo se confundiría con el del cubo por usuario y la prueba no
        # diría cuál de los dos ejes actuó.
        app.state.rate_limit_per_ip = 10_000
        app.state.rate_limit_global = 10_000
        app.state.rate_limit_pqrs_per_ip = 5
        app.state.rate_limit_cosecha_per_ip = 30
        app.state.rate_limit_per_usuario = LIMITE_POR_USUARIO
        app.state.rate_limit_per_tenant = 10_000
        app.state.rate_limit_login_per_ip = 10_000
        app.state.auth_cache_ttl = 0
        # Los topes de cuerpo y el resto del estado se toman de `settings` en vez
        # de fijarse a mano: el proxy los lee y un atributo que falte revienta con
        # un 500 que no tiene nada que ver con lo que se está probando.
        app.state.max_request_body_bytes = settings.max_request_body_bytes
        app.state.max_public_body_bytes = settings.max_public_body_bytes
        app.state.max_upload_body_bytes = settings.max_upload_body_bytes
        app.state.assistant_proxy_timeout = settings.assistant_proxy_timeout
        app.state.openapi_publico = settings.openapi_publico
        app.state.max_public_body_bytes = settings.max_public_body_bytes
        for atributo in dir(settings):
            if atributo.endswith("_service_url"):
                setattr(app.state, atributo, getattr(settings, atributo))

        # La validación responde con el usuario que dice el token, para poder
        # distinguir a dos personas que salen por la misma IP. El gateway valida
        # con `GET .../auth/validate` y el token en la cabecera `Authorization`
        # —no en un cuerpo—, así que el doble tiene que leerlo de ahí o todas las
        # peticiones caen en 503 y la prueba mediría el doble, no el limitador.
        async def validar(url, **kwargs):
            autorizacion = (kwargs.get("headers") or {}).get("Authorization", "")
            usuario = autorizacion.removeprefix("Bearer ").removeprefix("token-de-")
            return make_validate_response(True, user_id=usuario or "desconocido", username=usuario)

        http_client.get = AsyncMock(side_effect=validar)
        yield client, redis


async def test_dos_personas_por_la_misma_ip_no_se_roban_la_cuota(cliente):
    """El defecto en una frase: la de al lado te dejaba sin sistema.

    Ana agota su cuota; Beto, en la misma oficina y por la misma IP saliente,
    sigue trabajando. Con el eje anterior, Beto heredaba el 429 de Ana.
    """
    client, _ = cliente

    for _ in range(LIMITE_POR_USUARIO):
        assert (await client.get("/api/v1/documents", headers=_cabeceras("ana"))).status_code != 429

    agotada = await client.get("/api/v1/documents", headers=_cabeceras("ana"))
    assert agotada.status_code == 429, "el cubo por usuario no frena a quien se pasa"

    de_beto = await client.get("/api/v1/documents", headers=_cabeceras("beto"))
    assert de_beto.status_code != 429, (
        "Beto hereda el 429 de Ana: la cuota sigue yendo por dirección de red y "
        "una oficina entera tras un NAT se queda sin sistema por culpa del primero "
        "que trabaje deprisa"
    )


async def test_el_cubo_por_usuario_lleva_el_tenant_en_la_clave(cliente):
    """El identificador de usuario solo es único DENTRO de su entidad.

    Sin el tenant en la clave, dos personas de entidades distintas con el mismo
    id compartirían cuota — y en un sistema multi-tenant eso es una entidad
    dejando sin servicio a otra.
    """
    client, redis = cliente

    await client.get("/api/v1/documents", headers=_cabeceras("ana"))

    claves = [k for k in redis.contadores if k.startswith("ratelimit:usuario:")]
    assert claves, "no se contó nada por usuario: el middleware no llegó a actuar"
    assert all(":icetex:" in k for k in claves), (
        f"la clave del cubo por usuario no separa por entidad: {claves}"
    )


async def test_el_anonimo_no_entra_en_el_cubo_por_usuario(cliente):
    """Sin identidad no hay clave de usuario, y meterlos todos juntos sería peor.

    Control de la otra dirección: si el middleware contara también lo anónimo,
    todos los visitantes sin sesión caerían en el mismo cubo y el primero dejaría
    fuera a los demás — el mismo defecto que esto viene a cerrar, reaparecido en
    la puerta pública.
    """
    client, redis = cliente

    for _ in range(LIMITE_POR_USUARIO + 2):
        respuesta = await client.get("/api/v1/public/oai/demo")
        assert respuesta.status_code != 429

    assert not [k for k in redis.contadores if k.startswith("ratelimit:usuario:")], (
        "se contó tráfico anónimo en el cubo por usuario"
    )


async def test_lo_rechazado_por_persona_no_suma_al_cubo_global(cliente):
    """Gemelo del test del eje por IP, para el eje nuevo.

    El invariante es el mismo y el proyecto ya lo tenía escrito para el otro
    cubo: *un limitador no puede gastar en el cubo de terceros el tráfico que ya
    declaró abusivo*. Al añadir el eje por usuario volvió a romperse, porque el
    contador global se incrementa en un middleware MÁS EXTERNO que el que decide
    el rechazo: cuando el cubo por persona responde 429, el global ya contó.

    Lo que eso significa en operación: el global es de **todas las entidades**.
    Un usuario que se pase manda al global todo lo que su propia cuota le
    rechaza, así que unas pocas identidades agotan el cubo compartido y dejan en
    429 a los usuarios de las demás entidades — sin haber conseguido pasar ni una
    petición.
    """
    client, redis = cliente

    for _ in range(LIMITE_POR_USUARIO):
        assert (await client.get("/api/v1/documents", headers=_cabeceras("ana"))).status_code != 429

    global_tras_las_permitidas = redis.global_actual()

    for _ in range(20):
        respuesta = await client.get("/api/v1/documents", headers=_cabeceras("ana"))
        assert respuesta.status_code == 429

    assert redis.global_actual() == global_tras_las_permitidas, (
        f"las peticiones rechazadas por la cuota de la persona siguieron sumando al "
        f"cubo global ({global_tras_las_permitidas} → {redis.global_actual()}): unas "
        f"pocas identidades dejan sin servicio a todas las entidades"
    )


async def test_una_entidad_no_deja_sin_servicio_a_las_demas(cliente):
    """Entre la persona y la plataforma hace falta un escalón por ENTIDAD.

    Sin él, el techo de una entidad es el cubo global de TODAS: con 120 por
    persona y 4000 global bastaban ~33 personas de una sola entidad trabajando a
    la vez —sin abuso ninguno— para dejar a las demás en 429. En multi-tenant el
    aislamiento no es solo de datos: también de disponibilidad.
    """
    client, redis = cliente
    app.state.rate_limit_per_tenant = 4
    app.state.rate_limit_per_usuario = 10_000

    for _ in range(4):
        assert (await client.get("/api/v1/documents", headers=_cabeceras("ana"))).status_code != 429

    # La quinta la pide OTRA persona de la misma entidad: el cubo es de la entidad.
    assert (
        await client.get("/api/v1/documents", headers=_cabeceras("beto"))
    ).status_code == 429, "no hay cubo por entidad: el techo de una entidad es el global de todas"

    claves = [k for k in redis.contadores if k.startswith("ratelimit:tenant:")]
    assert claves, "no se contó nada por entidad"


async def test_el_login_tiene_cubo_propio_y_estrecho(cliente):
    """`POST /auth/token` no puede heredar el cubo general.

    Cada petición es un password-grant contra Keycloak, con hash de contraseña
    sobre el componente que no tiene réplicas. Subir el cubo general de 100 a
    1200 —hecho para resolver el problema del NAT— habría multiplicado por doce
    la amplificación de CPU en la única puerta anónima cara que quedaba sin tope
    propio: una puerta cerrada que abre un cambio hecho para otra cosa.
    """
    client, redis = cliente
    app.state.rate_limit_login_per_ip = 2

    for _ in range(2):
        respuesta = await client.post("/api/v1/auth/token", json={"username": "x"})
        assert respuesta.status_code != 429

    assert (
        await client.post("/api/v1/auth/token", json={"username": "x"})
    ).status_code == 429, "el login hereda el cubo general y es amplificación de CPU"

    assert [k for k in redis.contadores if k.startswith("ratelimit:login:")], (
        "el login no usa su propia clave, así que su tope no lo separa del general"
    )


async def test_el_tenant_se_sanea_antes_de_entrar_en_la_clave(cliente):
    """Un slug con `:` haría alias entre cubos de entidades distintas.

    `:` es el separador de las claves de este módulo, así que
    `tenant "a:b" + usuario "c"` y `tenant "a" + usuario "b:c"` producirían la
    MISMA clave y fusionarían cuotas de dos entidades. El claim llega tal cual
    desde el proveedor de identidad, así que se sanea aquí.
    """
    client, redis = cliente

    async def validar_con_slug_raro(url, **kwargs):
        return make_validate_response(True, user_id="c", username="c", tenant_slug="a:b")

    app.state.http_client.get = AsyncMock(side_effect=validar_con_slug_raro)
    await client.get("/api/v1/documents", headers=_cabeceras("ana"))

    claves = [k for k in redis.contadores if k.startswith("ratelimit:usuario:")]
    assert claves, "no se contó nada por usuario"
    assert all(k.count(":") == 4 for k in claves), (
        f"el slug sin sanear mete separadores extra en la clave y hace alias: {claves}"
    )
