# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Antirreplay ALTCHA contra Redis REAL y con peticiones simultáneas (C16, ADR-038).

El doble `RedisUnaVez` de `test_ventanilla_antirrobots.py` es secuencial: no puede
decir si `SET NX EX` es atómico, porque su `set` no cede el control. Aquí el cliente
es el de producción y las N peticiones arrancan a la vez con el MISMO reto: si el
consumo fuera «leer y luego escribir», varias verían la clave libre y radicarían.

Se llama `*antirrobots*` para que el autouse del conftest no sustituya el gate. Solo
`DocumentService.create` es un doble (la base no es lo que se mide: lo que se mide
es cuántas peticiones llegan a radicar). Con `RUN_INTEGRATION=1`.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
import redis.asyncio as aioredis

from app.core import altcha
from app.core.config import settings
from app.core.database import get_redis
from app.main import app
from tests.test_ventanilla import (
    BASE,
    _cuerpo,
    _fetchrow_por_consulta,
    _fetchval_por_consulta,
    _multipart,
    _resultado_radicado,
)

pytestmark = pytest.mark.integration

PQRS = "/api/v1/public/demo/pqrs"


@pytest_asyncio.fixture
async def redis_real() -> AsyncGenerator[aioredis.Redis, None]:
    cliente = aioredis.from_url(settings.redis_url)
    assert await cliente.ping()
    # Latencia inyectada ANTES de cada operación: un consumo «leer y luego escribir»
    # deja a todas las peticiones ver la clave libre (ventana de 20 ms). El SET NX
    # atómico no depende del orden, así que este retardo no puede favorecerlo.
    class _Lento:
        async def set(self, *a, **k):  # type: ignore[no-untyped-def]
            await asyncio.sleep(0.02)
            return await cliente.set(*a, **k)

        async def get(self, *a, **k):  # type: ignore[no-untyped-def]
            await asyncio.sleep(0.02)
            return await cliente.get(*a, **k)

        def __getattr__(self, nombre):  # type: ignore[no-untyped-def]
            return getattr(cliente, nombre)

    app.dependency_overrides[get_redis] = lambda: _Lento()
    claves: list[str] = []
    cliente.claves_de_prueba = claves  # type: ignore[attr-defined]
    try:
        yield cliente
    finally:
        if claves:
            await cliente.delete(*claves)
        await cliente.aclose()


async def _reto(client, redis_real) -> str:
    resp = await client.get(f"{BASE}/reto")
    assert resp.status_code == 200
    reto = resp.json()
    redis_real.claves_de_prueba.append(f"altcha:usado:demo:{reto['challenge']}")
    return altcha.codificar_payload(reto, altcha.resolver_reto(reto))


def _challenge_de(payload: str) -> str:
    return json.loads(base64.b64decode(payload))["challenge"]


def _pqrs(payload: str) -> dict:
    return {
        "tipo": "peticion", "nombre": "Ana", "asunto": "A", "descripcion": "D",
        "acepta_tratamiento": True, "politica_version": "2026-01", "altcha": payload,
    }


def _preparar(conn) -> None:
    conn.fetchval.side_effect = _fetchval_por_consulta("Derecho de petición", "cod")
    conn.fetchrow.side_effect = _fetchrow_por_consulta()


async def _crear_lento(*_a, **_k):
    await asyncio.sleep(0.05)  # ensancha la ventana: los perdedores llegan mientras el ganador radica
    return _resultado_radicado()


@pytest.mark.asyncio
async def test_veinte_peticiones_simultaneas_con_el_mismo_reto_radican_una_sola_vez(
    client_with_conn, redis_real
) -> None:
    client, conn = client_with_conn
    _preparar(conn)
    payload = await _reto(client, redis_real)

    with patch(
        "app.services.document_service.DocumentService.create", AsyncMock(side_effect=_crear_lento)
    ) as crear:
        respuestas = await asyncio.gather(
            *[client.post(BASE, **_multipart(_cuerpo(altcha=payload))) for _ in range(20)]
        )

    codigos = sorted(r.status_code for r in respuestas)
    assert codigos == [201] + [409] * 19, codigos
    for r in respuestas:
        if r.status_code == 409:
            assert r.json()["detail"]["error"] == "antirrobots_usado"
    assert crear.await_count == 1, "un reto, un número de radicado"
    # La marca la dejó la ganadora, con TTL real (restante + holgura), no eterna.
    ttl = await redis_real.ttl(f"altcha:usado:demo:{_challenge_de(payload)}")
    esperado = settings.altcha_ttl_segundos + settings.altcha_skew_segundos
    assert esperado - 10 <= ttl <= esperado


@pytest.mark.asyncio
async def test_el_mismo_reto_no_sirve_a_la_vez_por_ventanilla_y_por_pqrs(
    client_with_conn, redis_real
) -> None:
    """Las dos puertas comparten el espacio de retos: no hay un reto por puerta."""
    client, conn = client_with_conn
    _preparar(conn)
    payload = await _reto(client, redis_real)

    with patch(
        "app.services.document_service.DocumentService.create", AsyncMock(side_effect=_crear_lento)
    ) as crear:
        respuestas = await asyncio.gather(
            *[client.post(BASE, **_multipart(_cuerpo(altcha=payload))) for _ in range(8)],
            *[client.post(PQRS, json=_pqrs(payload)) for _ in range(8)],
        )

    assert sorted(r.status_code for r in respuestas) == [201] + [409] * 15
    assert crear.await_count == 1


@pytest.mark.asyncio
async def test_retos_distintos_simultaneos_radican_todos(client_with_conn, redis_real) -> None:
    """Control positivo: el 409 anterior no es 'Redis rechaza todo en paralelo'."""
    client, conn = client_with_conn
    _preparar(conn)
    payloads = [await _reto(client, redis_real) for _ in range(12)]

    with patch(
        "app.services.document_service.DocumentService.create", AsyncMock(side_effect=_crear_lento)
    ) as crear:
        respuestas = await asyncio.gather(
            *[client.post(BASE, **_multipart(_cuerpo(altcha=p))) for p in payloads]
        )

    assert [r.status_code for r in respuestas] == [201] * 12
    assert crear.await_count == 12
    assert len({_challenge_de(p) for p in payloads}) == 12


@pytest.mark.asyncio
async def test_un_reto_con_la_firma_alterada_no_deja_marca_en_redis(
    client_with_conn, redis_real
) -> None:
    """Se consume DESPUÉS de verificar: el basura no escribe claves (ni agota memoria)."""
    client, conn = client_with_conn
    _preparar(conn)
    payload = await _reto(client, redis_real)
    datos = json.loads(base64.b64decode(payload))
    datos["signature"] = "0" * 64
    malo = base64.b64encode(json.dumps(datos).encode()).decode()

    with patch("app.services.document_service.DocumentService.create", AsyncMock()) as crear:
        resp = await client.post(BASE, **_multipart(_cuerpo(altcha=malo)))

    assert resp.status_code == 422
    assert await redis_real.exists(f"altcha:usado:demo:{datos['challenge']}") == 0
    crear.assert_not_awaited()
