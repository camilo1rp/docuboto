# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Antirreplay ALTCHA contra PostgreSQL REAL y con peticiones simultáneas (C16, ADR-049).

El doble `AltchaUnaVez` de `test_ventanilla_antirrobots.py` es secuencial: no puede
decir si el consumo es atómico, porque no cede el control. Aquí el consumo va a la
tabla `altcha_usados` de un schema de entidad real, cada petición por SU conexión de
un pool, y las N peticiones arrancan a la vez con el MISMO reto: si el consumo fuera
«leer y luego escribir», varias verían el reto libre y radicarían.

Se llama `*antirrobots*` para que el autouse del conftest no sustituya el gate. El
resto de la petición sigue sobre la conexión simulada y `DocumentService.create` es
un doble: lo que se mide es cuántas peticiones llegan a radicar. Con `RUN_INTEGRATION=1`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
import pytest_asyncio
from orpycamcp_common.database import tenant_schema

from docuboto.config import document as settings
from document.core import altcha
from tests.document.test_ventanilla import (
    BASE,
    _cuerpo,
    _fetchrow_por_consulta,
    _fetchval_por_consulta,
    _multipart,
    _resultado_radicado,
)

pytestmark = pytest.mark.integration

PQRS = "/api/v1/public/demo/pqrs"


class _AltchaReal:
    """Envía las consultas de `altcha_usados` a Postgres real, una conexión por consulta."""

    def __init__(self, pool: asyncpg.Pool, schema: str) -> None:
        self.pool = pool
        self.schema = schema

    async def ejecutar(self, metodo: str, sql: str, args: tuple):  # type: ignore[no-untyped-def]
        # Latencia ANTES de cada consulta: un consumo «leer y luego escribir» deja a
        # todas las peticiones ver el reto libre (ventana de 20 ms). El INSERT … ON
        # CONFLICT atómico no depende del orden, así que esto no puede favorecerlo.
        await asyncio.sleep(0.02)
        async with self.pool.acquire() as conn:
            await conn.execute(f'SET search_path = "{self.schema}"')
            return await getattr(conn, metodo)(sql, *args)

    async def vida(self, challenge: str) -> float | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                f'SELECT extract(epoch FROM expira_en - now()) FROM "{self.schema}".altcha_usados '
                "WHERE challenge = $1",
                challenge,
            )


@pytest_asyncio.fixture
async def altcha_real(tenant_ctx) -> AsyncGenerator[_AltchaReal, None]:  # type: ignore[no-untyped-def]
    slug, _conn = tenant_ctx
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    pool = await asyncpg.create_pool(dsn, min_size=4, max_size=24)
    try:
        yield _AltchaReal(pool, tenant_schema(slug))
    finally:
        await pool.close()


def _preparar(conn, real: _AltchaReal) -> None:
    resto = _fetchval_por_consulta("Derecho de petición", "cod")

    async def _fetchval(sql, *args, **kw):  # type: ignore[no-untyped-def]
        if "altcha_usados" in sql:
            return await real.ejecutar("fetchval", sql, args)
        return await resto(sql, *args, **kw)

    async def _execute(sql, *args, **kw):  # type: ignore[no-untyped-def]
        if "altcha_usados" in sql:
            return await real.ejecutar("execute", sql, args)
        return "OK"

    conn.fetchval.side_effect = _fetchval
    conn.execute.side_effect = _execute
    conn.fetchrow.side_effect = _fetchrow_por_consulta()


async def _reto(client) -> str:
    resp = await client.get(f"{BASE}/reto")
    assert resp.status_code == 200
    reto = resp.json()
    return altcha.codificar_payload(reto, altcha.resolver_reto(reto))


def _challenge_de(payload: str) -> str:
    return json.loads(base64.b64decode(payload))["challenge"]


def _pqrs(payload: str) -> dict:
    return {
        "tipo": "peticion", "nombre": "Ana", "asunto": "A", "descripcion": "D",
        "acepta_tratamiento": True, "politica_version": "2026-01", "altcha": payload,
    }


async def _crear_lento(*_a, **_k):
    await asyncio.sleep(0.05)  # ensancha la ventana: los perdedores llegan mientras el ganador radica
    return _resultado_radicado()


@pytest.mark.asyncio
async def test_veinte_peticiones_simultaneas_con_el_mismo_reto_radican_una_sola_vez(
    client_with_conn, altcha_real
) -> None:
    client, conn = client_with_conn
    _preparar(conn, altcha_real)
    payload = await _reto(client)

    with patch(
        "document.services.document_service.DocumentService.create", AsyncMock(side_effect=_crear_lento)
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
    # La marca la dejó la ganadora, con vida real (restante + holgura), no eterna.
    ttl = await altcha_real.vida(_challenge_de(payload))
    assert ttl is not None
    esperado = settings.altcha_ttl_segundos + settings.altcha_skew_segundos
    assert esperado - 10 <= ttl <= esperado


@pytest.mark.asyncio
async def test_el_mismo_reto_no_sirve_a_la_vez_por_ventanilla_y_por_pqrs(
    client_with_conn, altcha_real
) -> None:
    """Las dos puertas comparten el espacio de retos: no hay un reto por puerta."""
    client, conn = client_with_conn
    _preparar(conn, altcha_real)
    payload = await _reto(client)

    with patch(
        "document.services.document_service.DocumentService.create", AsyncMock(side_effect=_crear_lento)
    ) as crear:
        respuestas = await asyncio.gather(
            *[client.post(BASE, **_multipart(_cuerpo(altcha=payload))) for _ in range(8)],
            *[client.post(PQRS, json=_pqrs(payload)) for _ in range(8)],
        )

    assert sorted(r.status_code for r in respuestas) == [201] + [409] * 15
    assert crear.await_count == 1


@pytest.mark.asyncio
async def test_retos_distintos_simultaneos_radican_todos(client_with_conn, altcha_real) -> None:
    """Control positivo: el 409 anterior no es 'la base rechaza todo en paralelo'."""
    client, conn = client_with_conn
    _preparar(conn, altcha_real)
    payloads = [await _reto(client) for _ in range(12)]

    with patch(
        "document.services.document_service.DocumentService.create", AsyncMock(side_effect=_crear_lento)
    ) as crear:
        respuestas = await asyncio.gather(
            *[client.post(BASE, **_multipart(_cuerpo(altcha=p))) for p in payloads]
        )

    assert [r.status_code for r in respuestas] == [201] * 12
    assert crear.await_count == 12
    assert len({_challenge_de(p) for p in payloads}) == 12


@pytest.mark.asyncio
async def test_un_reto_con_la_firma_alterada_no_deja_marca(
    client_with_conn, altcha_real
) -> None:
    """Se consume DESPUÉS de verificar: el basura no escribe filas."""
    client, conn = client_with_conn
    _preparar(conn, altcha_real)
    payload = await _reto(client)
    datos = json.loads(base64.b64decode(payload))
    datos["signature"] = "0" * 64
    malo = base64.b64encode(json.dumps(datos).encode()).decode()

    with patch("document.services.document_service.DocumentService.create", AsyncMock()) as crear:
        resp = await client.post(BASE, **_multipart(_cuerpo(altcha=malo)))

    assert resp.status_code == 422
    assert await altcha_real.vida(datos["challenge"]) is None
    crear.assert_not_awaited()
