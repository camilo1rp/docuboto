# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Antirrobots ALTCHA en la puerta pública, de punta a punta (C16, ADR-038).

El gate REAL corre aquí (el conftest solo lo sustituye en las suites que no se
llaman `*altcha*`/`*antirrobots*`). Todo reto sale de `GET .../ventanilla/reto` y
se resuelve con la misma fuerza bruta del widget: así el control positivo (un reto
bueno radica) acompaña a cada rechazo.

Lo que fija, en orden de importancia:

1. Un reto sirve UNA vez (409 `antirrobots_usado`). Mutación: aceptar un reto ya
   usado debe tumbar `test_un_reto_ya_usado_no_radica_dos_veces`.
2. Redis caído => 503, fail-closed, y no se sube ningún anexo ni se gasta número.
3. El reintento por `Idempotency-Key` NO exige un reto nuevo (devuelve lo ya radicado).
4. Un 409 de política quema el reto (declarado en el diseño).
5. Ventanilla y PQRS pasan por el mismo gate.
6. Emitir un reto no toca la base de datos ni Redis.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.core import altcha
from app.core.config import settings
from app.core.database import get_redis
from app.main import app
from app.schemas.public import VentanillaRespuesta
from app.services.ventanilla_service import VentanillaService
from tests.test_ventanilla import (
    BASE,
    PDF,
    _cuerpo,
    _fetchrow_por_consulta,
    _fetchval_por_consulta,
    _multipart,
    _resultado_radicado,
)


class RedisUnaVez:
    """Doble mínimo de `SET key val NX EX n`: recuerda las claves y su TTL."""

    def __init__(self, caido: bool = False) -> None:
        self.claves: dict[str, int | None] = {}
        self.caido = caido

    async def ping(self) -> bool:
        if self.caido:
            raise ConnectionError("redis caído")
        return True

    async def set(self, clave, valor, *, nx=False, ex=None):  # type: ignore[no-untyped-def]
        if self.caido:
            raise ConnectionError("redis caído")
        if nx and clave in self.claves:
            return None
        self.claves[clave] = ex
        return True


@pytest.fixture
def redis_doble() -> RedisUnaVez:
    r = RedisUnaVez()
    app.dependency_overrides[get_redis] = lambda: r
    return r


async def _payload_valido(client, tenant: str = "demo") -> str:
    """Pide un reto al endpoint real y lo resuelve como el widget."""
    resp = await client.get(f"/api/v1/public/{tenant}/ventanilla/reto")
    assert resp.status_code == 200, resp.text
    reto = resp.json()
    return altcha.codificar_payload(reto, altcha.resolver_reto(reto))


def _preparar(conn) -> None:
    conn.fetchval.side_effect = _fetchval_por_consulta("Derecho de petición", "cod")
    conn.fetchrow.side_effect = _fetchrow_por_consulta()


def _crear():
    return patch(
        "app.services.document_service.DocumentService.create",
        AsyncMock(return_value=_resultado_radicado()),
    )


# --------------------------------------------------------------------- el reto


@pytest.mark.asyncio
async def test_emitir_un_reto_no_toca_ni_la_base_ni_redis(client_with_conn, redis_doble) -> None:
    client, conn = client_with_conn
    resp = await client.get(f"{BASE}/reto")

    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    cuerpo = resp.json()
    assert set(cuerpo) == {"algorithm", "challenge", "maxnumber", "salt", "signature"}
    assert cuerpo["maxnumber"] == settings.altcha_maxnumber
    assert conn.mock_calls == [], "emitir un reto es puro cálculo: nada de base de datos"
    assert redis_doble.claves == {}, "emitir no consume nada: se consume al radicar"


@pytest.mark.asyncio
async def test_un_reto_emitido_para_una_entidad_no_sirve_en_otra(
    client_with_conn, redis_doble
) -> None:
    client, conn = client_with_conn
    _preparar(conn)
    payload = await _payload_valido(client, "otra")  # reto atado a `otra`
    with _crear() as crear:
        resp = await client.post(BASE, **_multipart(_cuerpo(altcha=payload)))  # entidad `demo`
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "antirrobots_invalido"
    crear.assert_not_awaited()


@pytest.mark.asyncio
async def test_el_reto_con_slug_invalido_es_400(client_with_conn) -> None:
    client, _ = client_with_conn
    resp = await client.get("/api/v1/public/Bad_Slug/ventanilla/reto")
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_tenant_slug"


# ------------------------------------------------------------- el gate real


@pytest.mark.asyncio
async def test_un_reto_bueno_radica_y_queda_consumido_con_su_ttl(
    client_with_conn, redis_doble
) -> None:
    """Control positivo + TTL de la marca de «usado» = lo que le resta al reto + holgura."""
    client, conn = client_with_conn
    _preparar(conn)
    payload = await _payload_valido(client)

    with _crear() as crear:
        resp = await client.post(BASE, **_multipart(_cuerpo(altcha=payload)))

    assert resp.status_code == 201, resp.text
    crear.assert_awaited_once()
    (clave, ttl), = redis_doble.claves.items()
    assert clave.startswith("altcha:usado:demo:") and len(clave.rsplit(":", 1)[1]) == 64
    esperado = settings.altcha_ttl_segundos + settings.altcha_skew_segundos
    assert esperado - 5 <= ttl <= esperado


@pytest.mark.asyncio
async def test_un_reto_ya_usado_no_radica_dos_veces(client_with_conn, redis_doble) -> None:
    client, conn = client_with_conn
    _preparar(conn)
    payload = await _payload_valido(client)

    with _crear() as crear:
        primera = await client.post(BASE, **_multipart(_cuerpo(altcha=payload)))
        segunda = await client.post(BASE, **_multipart(_cuerpo(altcha=payload)))

    assert primera.status_code == 201
    assert segunda.status_code == 409
    assert segunda.json()["detail"]["error"] == "antirrobots_usado"
    assert crear.await_count == 1, "el segundo uso no gasta número de radicado"


@pytest.mark.asyncio
async def test_sin_payload_altcha_no_se_radica_ni_en_ventanilla_ni_en_pqrs(
    client_with_conn, redis_doble
) -> None:
    client, conn = client_with_conn
    _preparar(conn)
    cuerpo = _cuerpo()
    del cuerpo["altcha"]
    pqrs = {
        "tipo": "peticion", "nombre": "Ana", "asunto": "A", "descripcion": "D",
        "acepta_tratamiento": True, "politica_version": "2026-01",
    }
    with _crear() as crear:
        v = await client.post(BASE, **_multipart(cuerpo))
        p = await client.post("/api/v1/public/demo/pqrs", json=pqrs)

    assert v.status_code == 422 and p.status_code == 422
    crear.assert_not_awaited()


@pytest.mark.asyncio
async def test_pqrs_pasa_por_el_mismo_gate(client_with_conn, redis_doble) -> None:
    client, conn = client_with_conn
    _preparar(conn)
    payload = await _payload_valido(client)
    pqrs = {
        "tipo": "peticion", "nombre": "Ana", "asunto": "A", "descripcion": "D",
        "acepta_tratamiento": True, "politica_version": "2026-01", "altcha": payload,
    }
    with _crear() as crear:
        ok = await client.post("/api/v1/public/demo/pqrs", json=pqrs)
        repetido = await client.post("/api/v1/public/demo/pqrs", json=pqrs)
        basura = await client.post(
            "/api/v1/public/demo/pqrs", json=pqrs | {"altcha": "x" * 40}
        )

    assert ok.status_code == 201, ok.text
    assert repetido.status_code == 409
    assert repetido.json()["detail"]["error"] == "antirrobots_usado"
    assert basura.status_code == 422
    assert basura.json()["detail"]["error"] == "antirrobots_invalido"
    assert crear.await_count == 1


@pytest.mark.asyncio
async def test_un_reto_vencido_se_rechaza(client_with_conn, redis_doble) -> None:
    client, conn = client_with_conn
    _preparar(conn)
    reto = altcha.emitir_reto(
        clave=settings.altcha_hmac_key.encode(), tenant="demo", ahora=1_000,
        ttl=60, maxnumber=settings.altcha_maxnumber,
    )  # emitido en 1970
    payload = altcha.codificar_payload(reto, altcha.resolver_reto(reto))
    with _crear() as crear:
        resp = await client.post(BASE, **_multipart(_cuerpo(altcha=payload)))
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "antirrobots_vencido"
    crear.assert_not_awaited()
    assert redis_doble.claves == {}, "un reto vencido no deja marca en Redis"


@pytest.mark.asyncio
async def test_redis_caido_es_503_y_no_sube_anexos_ni_gasta_numero(client_with_conn) -> None:
    client, conn = client_with_conn
    _preparar(conn)
    caido = RedisUnaVez(caido=True)
    app.dependency_overrides[get_redis] = lambda: caido
    payload = await _payload_valido(client)

    with (
        patch("app.services.ventanilla_service.upload_anexo_externo", AsyncMock()) as subir,
        _crear() as crear,
    ):
        resp = await client.post(
            BASE,
            **_multipart(_cuerpo(altcha=payload), [("soporte.pdf", PDF, "application/pdf")]),
        )

    assert resp.status_code == 503
    detalle = resp.json()["detail"]
    assert detalle["error"] == "antirrobots_no_disponible"
    assert "ventanilla" in detalle["detail"]  # nombra el canal alterno
    subir.assert_not_awaited()
    crear.assert_not_awaited()


@pytest.mark.asyncio
async def test_sin_clave_configurada_no_se_emite_ni_se_verifica(
    client_with_conn, redis_doble, monkeypatch
) -> None:
    client, _ = client_with_conn
    monkeypatch.setattr(settings, "altcha_hmac_key", "")
    resp = await client.get(f"{BASE}/reto")
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "antirrobots_no_disponible"


@pytest.mark.asyncio
async def test_el_reintento_con_la_misma_idempotency_key_no_pide_un_reto_nuevo(
    client_with_conn, redis_doble
) -> None:
    """El reintento reenvía el MISMO reto, ya consumido, y debe recibir el radicado ya hecho.

    Si el gate corriera antes del camino de reintento, esto sería 409. La huella
    excluye `altcha` por la misma razón.
    """
    client, conn = client_with_conn
    _preparar(conn)
    payload = await _payload_valido(client)
    ya_radicado = VentanillaRespuesta(
        radicado_id=uuid.uuid4(), tracking_number="2026-DEMO-E-000042", verification_code="cod"
    )
    clave = "k" * 20

    with (
        patch.object(
            VentanillaService, "_respuesta_existente", AsyncMock(side_effect=[None, ya_radicado])
        ),
        _crear() as crear,
    ):
        primera = await client.post(
            BASE, **_multipart(_cuerpo(altcha=payload)), headers={"Idempotency-Key": clave}
        )
        reintento = await client.post(
            BASE, **_multipart(_cuerpo(altcha=payload)), headers={"Idempotency-Key": clave}
        )

    assert primera.status_code == 201
    assert reintento.status_code == 201, reintento.text
    assert reintento.json()["tracking_number"] == "2026-DEMO-E-000042"
    assert crear.await_count == 1


@pytest.mark.asyncio
async def test_la_huella_de_idempotencia_no_incluye_el_reto(client_with_conn, redis_doble) -> None:
    client, conn = client_with_conn
    _preparar(conn)
    vistas: list[str] = []

    async def espia(self, clave, huella):  # type: ignore[no-untyped-def]
        vistas.append(huella)
        return None

    with patch.object(VentanillaService, "_respuesta_existente", espia), _crear():
        for _ in range(2):
            payload = await _payload_valido(client)  # retos DISTINTOS, mismo formulario
            await client.post(
                BASE, **_multipart(_cuerpo(altcha=payload)), headers={"Idempotency-Key": "h" * 20}
            )

    assert len(vistas) == 2 and vistas[0] == vistas[1]


@pytest.mark.asyncio
async def test_un_409_de_politica_quema_el_reto(client_with_conn, redis_doble) -> None:
    """Declarado en el diseño: el cliente pide otro reto (cuesta milisegundos)."""
    client, conn = client_with_conn
    _preparar(conn)
    payload = await _payload_valido(client)

    with _crear():
        mala = await client.post(
            BASE, **_multipart(_cuerpo(altcha=payload, politica_version="vieja"))
        )
        otra_vez = await client.post(BASE, **_multipart(_cuerpo(altcha=payload)))
        con_reto_nuevo = await client.post(
            BASE, **_multipart(_cuerpo(altcha=await _payload_valido(client)))
        )

    assert mala.status_code == 409
    assert mala.json()["detail"]["error"] == "politica_desactualizada"
    assert otra_vez.status_code == 409
    assert otra_vez.json()["detail"]["error"] == "antirrobots_usado"
    assert con_reto_nuevo.status_code == 201


@pytest.mark.asyncio
async def test_el_gate_va_antes_de_subir_anexos(client_with_conn, redis_doble) -> None:
    client, conn = client_with_conn
    _preparar(conn)
    with (
        patch("app.services.ventanilla_service.upload_anexo_externo", AsyncMock()) as subir,
        _crear(),
    ):
        resp = await client.post(
            BASE,
            **_multipart(_cuerpo(altcha="x" * 40), [("soporte.pdf", PDF, "application/pdf")]),
        )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "antirrobots_invalido"
    subir.assert_not_awaited()


# ------------------------------------------------ estado y contacto (auditoría C-3)

_PARAMS_ENTIDAD = [
    {"key": "entidad_nombre", "value": '"Alcaldía de Prueba"'},
    {"key": "entidad_direccion", "value": '"Calle 1 # 2-3, Bogotá"'},
    {"key": "entidad_email", "value": '"correspondencia@prueba.gov.co"'},
    {"key": "entidad_telefono", "value": '""'},  # vacío: no se publica
]


@pytest.mark.asyncio
async def test_el_estado_dice_si_el_antirrobots_esta_disponible_y_el_contacto(
    client_with_conn, redis_doble
) -> None:
    client, conn = client_with_conn
    conn.fetch.return_value = _PARAMS_ENTIDAD
    resp = await client.get(f"{BASE}/estado")

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["antirrobots_disponible"] is True
    assert cuerpo["contacto"] == {
        "nombre": "Alcaldía de Prueba",
        "direccion": "Calle 1 # 2-3, Bogotá",
        "telefono": None,
        "email": "correspondencia@prueba.gov.co",
        "web": None,
    }, "solo lo que la entidad declaró: nada inventado (y no hay horario)"


@pytest.mark.asyncio
async def test_sin_redis_o_sin_clave_el_estado_lo_dice(
    client_with_conn, monkeypatch
) -> None:
    client, conn = client_with_conn
    conn.fetch.return_value = []
    caido = RedisUnaVez(caido=True)
    app.dependency_overrides[get_redis] = lambda: caido
    assert (await client.get(f"{BASE}/estado")).json()["antirrobots_disponible"] is False

    app.dependency_overrides[get_redis] = lambda: RedisUnaVez()
    assert (await client.get(f"{BASE}/estado")).json()["antirrobots_disponible"] is True  # control
    monkeypatch.setattr(settings, "altcha_hmac_key", "corta")
    assert (await client.get(f"{BASE}/estado")).json()["antirrobots_disponible"] is False
    assert (await client.get(f"{BASE}/estado")).json()["contacto"] is None


@pytest.mark.asyncio
async def test_el_503_del_antirrobots_lleva_el_contacto_real_de_la_entidad(
    client_with_conn,
) -> None:
    client, conn = client_with_conn
    _preparar(conn)
    conn.fetch.return_value = _PARAMS_ENTIDAD
    app.dependency_overrides[get_redis] = lambda: RedisUnaVez(caido=True)
    payload = await _payload_valido(client)

    with _crear() as crear:
        resp = await client.post(BASE, **_multipart(_cuerpo(altcha=payload)))

    assert resp.status_code == 503
    detalle = resp.json()["detail"]
    assert detalle["error"] == "antirrobots_no_disponible"
    assert detalle["contacto"]["direccion"] == "Calle 1 # 2-3, Bogotá"
    assert "correspondencia@prueba.gov.co" in detalle["detail"]
    assert "telefono" not in detalle["contacto"]
    crear.assert_not_awaited()


@pytest.mark.asyncio
async def test_sin_parametros_de_contacto_el_503_no_inventa_ninguno(client_with_conn) -> None:
    client, conn = client_with_conn
    _preparar(conn)
    conn.fetch.return_value = []
    app.dependency_overrides[get_redis] = lambda: RedisUnaVez(caido=True)
    payload = await _payload_valido(client)
    with _crear():
        resp = await client.post(BASE, **_multipart(_cuerpo(altcha=payload)))
    assert resp.status_code == 503
    assert "contacto" not in resp.json()["detail"]
