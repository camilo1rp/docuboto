# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La identidad firmada es de UN SOLO USO (ADR-036, addendum C-7).

Firma + ventana de 120 s dejaban una captura de red reproducible muchas veces dentro de
la ventana. El nonce de 128 bits, dentro del HMAC y reclamado en Redis con `SET NX EX`,
la reduce a un uso. Aquí se ejerce la PIEZA (firmar/verificar/TTL) y la PUERTA real
(`IdentidadFirmadaMiddleware` sobre una app ASGI mínima); el `SET NX` contra un Redis
real vive en `services/api-gateway/tests/integration`.
"""

from __future__ import annotations

import json

import pytest

from orpycamcp_common import identidad
from orpycamcp_common.identidad import (
    CABECERA_FIRMA,
    CABECERA_NONCE,
    CAMPOS_FIRMADOS,
    VERSION_FIRMA,
    IdentidadFirmadaMiddleware,
    IdentidadNoAutentica,
    IdentidadVerificada,
    RegistroNoDisponible,
    RegistroNoncesMemoria,
    clave_nonce,
    firmar,
    ttl_nonce,
    verificar,
)

SECRETO = "secreto-de-pruebas-un-solo-uso"
AHORA = 1_900_000_000.0
RUTA = "/api/v1/x"
OP = {"metodo": "POST", "ruta": RUTA}
USER = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def entorno(monkeypatch):
    monkeypatch.setenv("IDENTITY_SIGNING_SECRET", SECRETO)
    monkeypatch.setenv("REDIS_URL", "redis://nunca-se-conecta:6379/0")
    monkeypatch.delenv("IDENTITY_MAX_AGE_SECONDS", raising=False)
    monkeypatch.delenv("IDENTITY_MAX_SKEW_SECONDS", raising=False)
    identidad.fijar_registro(None)
    yield
    identidad.fijar_registro(None)


def _identidad(**kw):
    base = {
        "X-User-Id": USER,
        "X-Username": "jperez",
        "X-Tenant-Slug": "demo",
        "X-User-Roles": "viewer",
        "X-Canal": "api",
        "X-Real-Ip": "10.0.0.1",
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# La pieza: formato v3
# ---------------------------------------------------------------------------


def test_la_version_es_v3_y_el_nonce_va_dentro_de_lo_firmado():
    assert VERSION_FIRMA == "v3"
    assert CAMPOS_FIRMADOS[-1] == CABECERA_NONCE == "X-Identity-Nonce"


def test_dos_firmar_seguidos_dan_nonces_distintos():
    a = firmar(_identidad(), **OP, ahora=AHORA)
    b = firmar(_identidad(), **OP, ahora=AHORA)
    assert a[CABECERA_NONCE] != b[CABECERA_NONCE]
    assert a[CABECERA_FIRMA] != b[CABECERA_FIRMA]


def test_el_nonce_son_128_bits_en_base64url():
    nonce = firmar(_identidad(), **OP)[CABECERA_NONCE]
    assert len(nonce) == 22
    assert set(nonce) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


@pytest.mark.parametrize("clave", ["X-Identity-Nonce", "x-identity-nonce", "X-IDENTITY-NONCE"])
def test_firmar_sustituye_el_nonce_entrante(clave):
    """El llamante no puede aportar un nonce: ni reutilizar uno, ni elegirlo."""
    elegido = "A" * 22
    firmadas = firmar({**_identidad(), clave: elegido}, **OP, ahora=AHORA)
    assert firmadas[CABECERA_NONCE] != elegido
    assert sum(1 for k in firmadas if k.lower() == "x-identity-nonce") == 1


def test_cambiar_el_nonce_invalida_la_firma():
    firmadas = firmar(_identidad(), **OP, ahora=AHORA)
    firmadas[CABECERA_NONCE] = "B" * 22
    with pytest.raises(IdentidadNoAutentica):
        verificar(firmadas, **OP, ahora=AHORA)


@pytest.mark.parametrize("nonce", ["", "corto", "A" * 21, "A" * 23, "A" * 21 + "+", "A" * 21 + " "])
def test_un_nonce_mal_formado_es_identidad_no_autentica(monkeypatch, nonce):
    """Aunque la firma sea correcta (la fabrica quien tenga el secreto), la forma se exige."""
    monkeypatch.setattr(identidad.secrets, "token_urlsafe", lambda _n: nonce)
    firmadas = firmar(_identidad(), **OP, ahora=AHORA)
    with pytest.raises(IdentidadNoAutentica):
        verificar(firmadas, **OP, ahora=AHORA)


def test_verificar_devuelve_tenant_nonce_y_caducidad():
    firmadas = firmar(_identidad(), **OP, ahora=AHORA)
    v = verificar(firmadas, **OP, ahora=AHORA)
    assert isinstance(v, IdentidadVerificada)
    assert (v.tenant, v.nonce) == ("demo", firmadas[CABECERA_NONCE])
    assert v.caduca_en == AHORA + 120


def test_sin_secreto_verificar_devuelve_none(monkeypatch):
    monkeypatch.delenv("IDENTITY_SIGNING_SECRET")
    assert verificar(_identidad(), **OP) is None


# ---------------------------------------------------------------------------
# El TTL del nonce y su borde
# ---------------------------------------------------------------------------


def test_ttl_en_el_borde_inferior():
    """`emitida` = ahora - max_age: vive 1 s (piso) + el desfase tolerado."""
    v = verificar(firmar(_identidad(), **OP, ahora=AHORA - 120), **OP, ahora=AHORA)
    assert ttl_nonce(v, AHORA) == 1 + 30


def test_ttl_en_el_borde_superior():
    """`emitida` = ahora + skew: techo max_age + 2*skew = 180 s."""
    v = verificar(firmar(_identidad(), **OP, ahora=AHORA + 30), **OP, ahora=AHORA)
    assert ttl_nonce(v, AHORA) == 120 + 2 * 30


def test_ttl_de_una_identidad_recien_emitida():
    v = verificar(firmar(_identidad(), **OP, ahora=AHORA), **OP, ahora=AHORA)
    assert ttl_nonce(v, AHORA) == 120 + 30


def test_la_clave_va_por_tenant_y_nonce_sin_servicio():
    assert clave_nonce("demo", "N" * 22) == "orpycamcp:identidad:nonce:v3:demo:" + "N" * 22
    # Un tenant mal formado no abre claves distintas por entrada hostil.
    assert clave_nonce("DEMO; x", "N" * 22) == "orpycamcp:identidad:nonce:v3:-:" + "N" * 22


# ---------------------------------------------------------------------------
# La puerta: middleware sobre una app ASGI mínima
# ---------------------------------------------------------------------------


async def _app_ok(scope, receive, send):
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


class _Respuesta:
    def __init__(self, status: int, headers: dict[str, str], cuerpo: bytes) -> None:
        self.status = status
        self.headers = headers
        self.cuerpo = cuerpo

    def json(self):
        return json.loads(self.cuerpo)


async def _llamar(cabeceras: dict[str, str], *, metodo="POST", ruta=RUTA) -> _Respuesta:
    mw = IdentidadFirmadaMiddleware(_app_ok)
    scope = {
        "type": "http",
        "method": metodo,
        "path": ruta,
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in cabeceras.items()],
    }
    enviados: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        enviados.append(msg)

    await mw(scope, receive, send)
    inicio = next(m for m in enviados if m["type"] == "http.response.start")
    cuerpo = b"".join(m.get("body", b"") for m in enviados if m["type"] == "http.response.body")
    return _Respuesta(
        inicio["status"], {k.decode(): v.decode() for k, v in inicio["headers"]}, cuerpo
    )


async def test_la_misma_identidad_dos_veces_200_y_luego_401():
    identidad.fijar_registro(RegistroNoncesMemoria())
    firmadas = firmar(_identidad(), **OP)
    assert (await _llamar(firmadas)).status == 200
    segunda = await _llamar(firmadas)
    assert segunda.status == 401
    assert segunda.json()["error"] == "identidad_no_autentica"


async def test_dos_identidades_distintas_pasan_las_dos():
    identidad.fijar_registro(RegistroNoncesMemoria())
    assert (await _llamar(firmar(_identidad(), **OP))).status == 200
    assert (await _llamar(firmar(_identidad(), **OP))).status == 200


async def test_la_reutilizacion_deja_traza_sin_el_nonce_en_claro(caplog):
    identidad.fijar_registro(RegistroNoncesMemoria())
    firmadas = firmar(_identidad(), **OP)
    await _llamar(firmadas)
    with caplog.at_level("WARNING", logger="orpycamcp_common.identidad"):
        await _llamar(firmadas)
    texto = "\n".join(r.getMessage() for r in caplog.records)
    assert "identidad_reutilizada" in texto and "demo" in texto
    assert firmadas[CABECERA_NONCE] not in texto


async def test_un_nonce_mal_formado_da_401_sin_tocar_el_registro(monkeypatch):
    registro = RegistroNoncesMemoria()
    identidad.fijar_registro(registro)
    monkeypatch.setattr(identidad.secrets, "token_urlsafe", lambda _n: "corto")
    assert (await _llamar(firmar(_identidad(), **OP))).status == 401
    assert registro.llamadas == 0


async def test_una_firma_mala_no_escribe_en_el_registro():
    """`reclamar` va DESPUÉS de verificar: sin el secreto nadie quema nonces ajenos."""
    registro = RegistroNoncesMemoria()
    identidad.fijar_registro(registro)
    firmadas = firmar(_identidad(), **OP)
    firmadas["X-Tenant-Slug"] = "otra"
    assert (await _llamar(firmadas)).status == 401
    assert registro.llamadas == 0


async def test_una_identidad_caducada_no_escribe_en_el_registro():
    registro = RegistroNoncesMemoria()
    identidad.fijar_registro(registro)
    vieja = firmar(_identidad(), **OP, ahora=1.0)
    assert (await _llamar(vieja)).status == 401
    assert registro.llamadas == 0


class _RegistroCaido:
    llamadas = 0

    async def reclamar(self, clave: str, ttl_s: int) -> bool:
        type(self).llamadas += 1
        raise RegistroNoDisponible("redis caido")


async def test_registro_caido_da_503_con_retry_after_y_no_200():
    identidad.fijar_registro(_RegistroCaido())
    r = await _llamar(firmar(_identidad(), **OP))
    assert r.status == 503
    assert r.json()["error"] == "identidad_no_verificable"
    assert r.headers["retry-after"] == "1"


async def test_sin_identidad_de_persona_no_se_consulta_el_registro():
    """La ventanilla pública, /health y los webhooks viven con Redis caído."""
    registro = RegistroNoncesMemoria()
    identidad.fijar_registro(registro)
    assert (await _llamar({"X-Tenant-Slug": "demo"}, metodo="GET", ruta="/health")).status == 200
    assert registro.llamadas == 0


async def test_sin_secreto_no_se_consulta_el_registro(monkeypatch):
    monkeypatch.delenv("IDENTITY_SIGNING_SECRET")
    registro = RegistroNoncesMemoria()
    identidad.fijar_registro(registro)
    assert (await _llamar(_identidad())).status == 200
    assert registro.llamadas == 0


# ---------------------------------------------------------------------------
# Redis real caído: el registro de Redis traduce TODO error a RegistroNoDisponible
# ---------------------------------------------------------------------------


async def test_el_registro_redis_con_un_puerto_cerrado_es_no_disponible():
    registro = identidad.RegistroNoncesRedis("redis://127.0.0.1:1/15")
    with pytest.raises(RegistroNoDisponible):
        await registro.reclamar("itest:x", 5)


async def test_el_registro_redis_sin_url_es_no_disponible(monkeypatch):
    monkeypatch.delenv("REDIS_URL")
    with pytest.raises(RegistroNoDisponible):
        await identidad.RegistroNoncesRedis().reclamar("itest:x", 5)


# ---------------------------------------------------------------------------
# Arranque: configuración incoherente = no arranca
# ---------------------------------------------------------------------------


async def _lifespan(app) -> list[dict]:
    mensajes = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    enviados: list[dict] = []

    async def receive():
        return next(mensajes)

    async def send(msg):
        enviados.append(msg)

    await app({"type": "lifespan"}, receive, send)
    return enviados


async def _app_con_lifespan(scope, receive, send):
    if scope["type"] == "lifespan":
        await receive()
        await send({"type": "lifespan.startup.complete"})
        await receive()
        await send({"type": "lifespan.shutdown.complete"})


async def test_con_secreto_y_sin_redis_url_el_arranque_falla(monkeypatch):
    monkeypatch.delenv("REDIS_URL")
    enviados = await _lifespan(IdentidadFirmadaMiddleware(_app_con_lifespan))
    assert enviados[0]["type"] == "lifespan.startup.failed"
    assert "REDIS_URL" in enviados[0]["message"]


async def test_con_secreto_y_redis_url_el_arranque_sigue_y_aunque_redis_no_responda():
    enviados = await _lifespan(IdentidadFirmadaMiddleware(_app_con_lifespan))
    assert enviados[0]["type"] == "lifespan.startup.complete"


async def test_sin_secreto_el_arranque_no_exige_redis(monkeypatch):
    monkeypatch.delenv("REDIS_URL")
    monkeypatch.delenv("IDENTITY_SIGNING_SECRET")
    enviados = await _lifespan(IdentidadFirmadaMiddleware(_app_con_lifespan))
    assert enviados[0]["type"] == "lifespan.startup.complete"


# ---------------------------------------------------------------------------
# Higiene: el registro de pruebas solo se instala desde pruebas
# ---------------------------------------------------------------------------


def test_fijar_registro_solo_aparece_bajo_tests():
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[2]
    fuera = []
    for base in ("services", "shared", "scripts"):
        for f in (raiz / base).rglob("*.py"):
            partes = f.relative_to(raiz).parts
            if "tests" in partes or "node_modules" in partes or "site-packages" in partes:
                continue
            if "fijar_registro(" in f.read_text(encoding="utf-8") and f.name != "identidad.py":
                fuera.append(str(f.relative_to(raiz)))
    assert not fuera, fuera
