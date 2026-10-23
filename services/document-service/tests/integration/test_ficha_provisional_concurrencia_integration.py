# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Refuerzo C-3 contra Postgres REAL: concurrencia, no-oráculo y trigger de la 049.

Complementa `test_ficha_provisional_integration.py` (que usa UNA conexión y por
tanto nunca compite consigo misma) con lo que solo dos sesiones reales pueden decir:

1. **Dos conciliaciones simultáneas sobre la misma ficha: gana UNA.** Dos conexiones,
   `gather`, y se comprueba el estado final, el asiento único y que el perdedor recibe
   `409 tercero_ya_conciliado` (no un 500 ni un doble efecto).
2. **La respuesta pública no delata si el documento existía.** Por HTTP de punta a
   punta (gate ALTCHA real contra Redis real, Postgres real): mismo cuerpo, mismas
   cabeceras, y —lo que sustituye a un cronómetro frágil— la MISMA secuencia de
   sentencias SQL con o sin ficha previa. Más un cronómetro grueso, a propósito laxo.
3. **El trigger y los CHECK de la 049**: lo declarado es inmutable por cada columna y
   los estados terminales no se rehacen, ni siquiera de uno terminal a otro.
"""

from __future__ import annotations

import asyncio
import json
import re
import statistics
import time
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import asyncpg
import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.core import altcha
from app.core.config import settings
from app.core.database import (
    get_public_tenant_conn_escritura,
    get_redis,
    get_tenant_client,
    tenant_schema,
)
from app.main import app
from tests.integration.conftest import _require_dsn
from tests.integration.politica_stub import ENTIDAD
from tests.integration.test_ficha_provisional_integration import (
    ACTOR,
    _anonimo,
    _asientos,
    _conciliar,
    _entorno,
    _existente,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def conn2(tenant_ctx: tuple[str, asyncpg.Connection]) -> AsyncGenerator[asyncpg.Connection, None]:
    """Segunda sesión REAL sobre el mismo schema: lo que hace posible la carrera."""
    slug, _ = tenant_ctx
    c = await asyncpg.connect(dsn=_require_dsn())
    await c.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    try:
        yield c
    finally:
        await c.close()


async def _resultado(coro: Any) -> Any:
    try:
        return await coro
    except HTTPException as exc:
        return exc


# ---------------------------------------------------------------- 1. carrera


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "a,b",
    [
        ("vincular", "vincular"),
        ("vincular", "descartar"),
        ("confirmar", "vincular"),
        ("descartar", "descartar"),
    ],
)
async def test_dos_conciliaciones_simultaneas_de_la_misma_ficha_gana_una(
    tenant_ctx, conn2, a: str, b: str
) -> None:
    slug, conn = await _entorno(tenant_ctx)
    # Un documento declarado distinto del de `real` para que `confirmar` no choque con
    # `ux_tercero_documento`: aquí se mide la carrera, no esa colisión.
    real = await _existente(conn, "777")
    # `real` no coincide con el documento declarado => `vincular` pide motivo.
    motivo = "Es la misma persona, confirmado por teléfono"

    def kw(accion: str) -> dict[str, Any]:
        return {
            "destino_id": real if accion == "vincular" else None,
            "motivo": motivo,
        }

    for vuelta in range(4):  # la carrera no es determinista: varias vueltas, fichas nuevas
        ficha = await _anonimo(conn, slug, documento=f"900{vuelta}", asunto=f"Carrera {a}-{b}-{vuelta}")
        n_antes = len(await _asientos(conn, slug, "document.tercero_conciliado"))

        r1, r2 = await asyncio.gather(
            _resultado(_conciliar(conn, slug, ficha, a, **kw(a))),
            _resultado(_conciliar(conn2, slug, ficha, b, **kw(b))),
        )

        ganadores = [r for r in (r1, r2) if isinstance(r, dict)]
        perdedores = [r for r in (r1, r2) if isinstance(r, HTTPException)]
        assert len(ganadores) == 1 and len(perdedores) == 1, (r1, r2)
        assert perdedores[0].status_code == 409
        assert perdedores[0].detail["error"] == "tercero_ya_conciliado"
        # Un solo asiento nuevo, y es el del ganador.
        nuevos = (await _asientos(conn, slug, "document.tercero_conciliado"))[n_antes:]
        assert len(nuevos) == 1
        assert nuevos[0]["payload"]["accion"] == ganadores[0]["accion"]
        fila = await conn.fetchrow(
            "SELECT conciliacion, fusionado_en, conciliada_por FROM tercero WHERE id = $1", ficha
        )
        assert fila["conciliacion"] == ganadores[0]["conciliacion"]
        assert fila["conciliada_por"] == ACTOR
        assert (fila["fusionado_en"] is not None) == (ganadores[0]["accion"] == "vincular")


@pytest.mark.asyncio
async def test_la_carrera_no_afloja_con_el_clearance_en_juego(tenant_ctx, conn2) -> None:
    """Misma carrera con la ficha atada a un radicado reservado: gana UNA con clearance
    suficiente y quien no lo tiene recibe 404 (nunca 409 que confirme que existe)."""
    slug, conn = await _entorno(tenant_ctx)
    reservada = await _anonimo(conn, slug, documento="31", asunto="Reservada carrera", nivel=3)

    alto, bajo = await asyncio.gather(
        _resultado(_conciliar(conn, slug, reservada, "descartar", motivo="descartada por alto", clearance=3)),
        _resultado(_conciliar(conn2, slug, reservada, "descartar", motivo="intento del bajo!", clearance=1)),
    )
    assert isinstance(alto, dict), alto
    assert isinstance(bajo, HTTPException) and bajo.status_code == 404
    assert bajo.detail["error"] == "tercero_no_encontrado"


# ------------------------------------------------------------ 2. no-oráculo


class _Grabadora:
    """Envuelve la conexión y apunta el SQL (normalizado) que se ejecuta, en orden."""

    _METODOS = {"fetch", "fetchrow", "fetchval", "execute", "executemany"}

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn
        self.sql: list[str] = []

    def __getattr__(self, nombre: str) -> Any:
        atributo = getattr(self._conn, nombre)
        if nombre not in self._METODOS:
            return atributo

        async def _con_registro(consulta: str, *a: Any, **k: Any) -> Any:
            self.sql.append(" ".join(consulta.split()))
            return await getattr(self._conn, nombre)(consulta, *a, **k)

        return _con_registro


class _RedisSoloAntireplay:
    """`SET NX` va al Redis real (lo que se mide); los eventos del outbox, a la nada
    (no se ensucia el stream de desarrollo con un tenant de prueba)."""

    def __init__(self, real: aioredis.Redis) -> None:
        self._real = real
        self._otro = AsyncMock()

    async def set(self, *a: Any, **k: Any) -> Any:
        return await self._real.set(*a, **k)

    def __getattr__(self, nombre: str) -> Any:
        return getattr(self._otro, nombre)


class _Tenant:
    async def get_params_estricto(self, *, tenant_slug: str) -> dict:
        return await ENTIDAD.get_params_estricto(tenant_slug=tenant_slug)

    async def get_dependencia(self, *, tenant_slug: str, codigo: int) -> dict:
        return {"codigo": codigo, "nombre": "Ventanilla Única"}


@pytest_asyncio.fixture
async def puerta(tenant_ctx) -> AsyncGenerator[tuple[AsyncClient, _Grabadora, str, asyncpg.Connection], None]:
    slug, conn = await _entorno(tenant_ctx)
    grabadora = _Grabadora(conn)
    real = aioredis.from_url(settings.redis_url)
    claves: list[str] = []
    original = real.set

    async def _anota(clave: str, *a: Any, **k: Any) -> Any:
        claves.append(clave)
        return await original(clave, *a, **k)

    real.set = _anota  # type: ignore[method-assign]

    async def _conn() -> AsyncGenerator[Any, None]:
        yield grabadora

    app.dependency_overrides[get_public_tenant_conn_escritura] = _conn
    app.dependency_overrides[get_tenant_client] = lambda: _Tenant()
    app.dependency_overrides[get_redis] = lambda: _RedisSoloAntireplay(real)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac, grabadora, slug, conn
    finally:
        app.dependency_overrides.clear()
        if claves:
            await real.delete(*claves)
        await real.aclose()


async def _radicar_http(client: AsyncClient, slug: str, identificacion: str, asunto: str):
    reto = (await client.get(f"/api/v1/public/{slug}/ventanilla/reto")).json()
    payload = altcha.codificar_payload(reto, altcha.resolver_reto(reto))
    cuerpo = {
        "tipo_documental": "DEM-PET", "nombre": "Suplantador", "tipo_identificacion": "CC",
        "identificacion": identificacion, "email": "otro@falso.co", "asunto": asunto,
        "descripcion": "Cuerpo de la petición", "acepta_tratamiento": True,
        "politica_version": "2026-01", "altcha": payload,
    }
    t0 = time.perf_counter()
    resp = await client.post(
        f"/api/v1/public/{slug}/ventanilla", data={"datos": json.dumps(cuerpo)}
    )
    return resp, time.perf_counter() - t0


def _forma(resp: Any) -> dict[str, Any]:
    """Todo lo observable de la respuesta salvo los valores aleatorios (ids, números)."""
    cuerpo = resp.json()
    return {
        "status": resp.status_code,
        "claves": sorted(cuerpo),
        "tipos": {k: type(v).__name__ for k, v in cuerpo.items()},
        "mensaje": cuerpo.get("mensaje"),
        "tracking": re.sub(r"\d", "9", cuerpo["tracking_number"]),
        "longitud_codigo": len(cuerpo["verification_code"]),
        "cabeceras": sorted(k for k in resp.headers if k not in {"date"}),
        "content-length": resp.headers["content-length"],
    }


@pytest.mark.asyncio
async def test_la_respuesta_publica_es_identica_exista_o_no_el_documento(puerta) -> None:
    client, grabadora, slug, conn = puerta
    await _existente(conn, "1020304050")
    # Calentamiento: el primer radicado del año crea la fila de la secuencia y su SQL
    # difiere del de los siguientes; no tiene que ver con el documento.
    calentamiento, _ = await _radicar_http(client, slug, "000", "Calentamiento")
    assert calentamiento.status_code == 201, calentamiento.text

    grabadora.sql.clear()
    con, _ = await _radicar_http(client, slug, "1020304050", "Con documento existente")
    sql_con = list(grabadora.sql)
    grabadora.sql.clear()
    sin, _ = await _radicar_http(client, slug, "9999999999", "Con documento nuevo")
    sql_sin = list(grabadora.sql)

    assert con.status_code == sin.status_code == 201, (con.text, sin.text)
    assert _forma(con) == _forma(sin)
    # Lo que sustituye al cronómetro: el servidor ejecuta EXACTAMENTE las mismas
    # sentencias, en el mismo orden. Sin una rama que mire el directorio por documento,
    # no hay diferencia de trabajo que medir.
    assert sql_con == sql_sin
    assert sql_con, "la grabadora no vio nada: la prueba no estaría midiendo"
    # Y ninguna de ellas consulta `tercero` por documento (la puerta de C17).
    # (leer la ficha recién creada POR ID es legítimo; por documento, no).
    leen_tercero = [
        s for s in sql_con
        if s.lower().startswith("select") and re.search(r"\bfrom tercero\b.*\bwhere\b.*(numero_documento|documento_declarado)", s, re.I)
    ]
    assert not leen_tercero, leen_tercero
    # Control: la ficha existente no se tocó y ambas peticiones dejaron su provisional.
    assert await conn.fetchval("SELECT count(*) FROM tercero WHERE conciliacion = 'pendiente'") == 3


@pytest.mark.asyncio
async def test_el_tiempo_de_respuesta_no_depende_del_documento(puerta) -> None:
    """Cronómetro GRUESO (el criterio fuerte es la igualdad de sentencias de arriba).

    Medianas de 10 peticiones alternadas: si existir el documento costara una consulta
    extra de verdad, la mediana se separaría. El umbral es laxo a propósito (un CI
    ruidoso no debe producir falsos rojos): solo caza diferencias groseras.
    """
    client, _, slug, conn = puerta
    await _existente(conn, "1020304050")
    await _radicar_http(client, slug, "000", "Calentamiento")

    con: list[float] = []
    sin: list[float] = []
    for i in range(10):
        r1, t1 = await _radicar_http(client, slug, "1020304050", f"Con {i}")
        r2, t2 = await _radicar_http(client, slug, f"8{i:09d}", f"Sin {i}")
        assert r1.status_code == r2.status_code == 201
        con.append(t1)
        sin.append(t2)

    m_con, m_sin = statistics.median(con), statistics.median(sin)
    assert abs(m_con - m_sin) <= max(0.030, 0.5 * min(m_con, m_sin)), (m_con, m_sin)


# ------------------------------------------------------ 3. trigger y CHECK 049


async def _rechaza(conn: asyncpg.Connection, sql: str, *args: Any) -> asyncpg.PostgresError:
    with pytest.raises(asyncpg.PostgresError) as exc:
        async with conn.transaction():
            await conn.execute(sql, *args)
    return exc.value  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_lo_declarado_es_inmutable_columna_por_columna_y_tambien_tras_confirmar(
    tenant_ctx,
) -> None:
    slug, conn = await _entorno(tenant_ctx)
    ficha = await _anonimo(conn, slug, documento="4455", asunto="Inmutable")

    # El trigger nombra lo declarado (no es un CHECK genérico que otra cosa pudiera disparar).
    e = await _rechaza(conn, "UPDATE tercero SET documento_declarado_numero='1' WHERE id=$1", ficha)
    assert "evidencia" in str(e)
    e = await _rechaza(conn, "UPDATE tercero SET documento_declarado_tipo='TI' WHERE id=$1", ficha)
    assert "evidencia" in str(e)
    e = await _rechaza(conn, "UPDATE tercero SET documento_declarado_tipo=NULL WHERE id=$1", ficha)
    assert "evidencia" in str(e)

    # Control positivo: lo NO declarado sí se edita (el trigger no congela la fila entera).
    await conn.execute("UPDATE tercero SET nombre='Nombre corregido', telefono='3000000' WHERE id=$1", ficha)

    # Tras confirmar, la evidencia sigue congelada aunque la ficha ya tenga identidad.
    await _conciliar(conn, slug, ficha, "confirmar")
    assert await conn.fetchval("SELECT identidad FROM tercero WHERE id=$1", ficha) == "confirmada"
    e = await _rechaza(conn, "UPDATE tercero SET documento_declarado_numero='2' WHERE id=$1", ficha)
    assert "evidencia" in str(e)
    e = await _rechaza(conn, "UPDATE tercero SET documento_declarado_numero=NULL WHERE id=$1", ficha)
    assert "evidencia" in str(e)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["vinculada", "confirmada", "descartada"])
async def test_un_estado_terminal_no_se_rehace_ni_salta_a_otro(tenant_ctx, terminal: str) -> None:
    slug, conn = await _entorno(tenant_ctx)
    real = await _existente(conn, "123456")
    ficha = await _anonimo(conn, slug, documento="123456", asunto=f"Terminal {terminal}")
    accion = {"vinculada": "vincular", "confirmada": "confirmar", "descartada": "descartar"}[terminal]
    if accion == "confirmar":  # el documento ya es de `real`: se libera para poder confirmar
        await conn.execute("UPDATE tercero SET numero_documento='0' WHERE id=$1", real)
    await _conciliar(
        conn, slug, ficha, accion,
        destino_id=real if accion == "vincular" else None,
        motivo="motivo suficientemente largo",
    )
    assert await conn.fetchval("SELECT conciliacion FROM tercero WHERE id=$1", ficha) == terminal

    otros = {"pendiente", "vinculada", "confirmada", "descartada"} - {terminal}
    for destino in sorted(otros):
        e = await _rechaza(
            conn,
            "UPDATE tercero SET conciliacion=$2, conciliada_at=now(), conciliada_por=$3, "
            "conciliacion_motivo='motivo suficientemente largo' WHERE id=$1",
            ficha, destino, ACTOR,
        )
        # O lo para el trigger, o lo para un CHECK: ambos son la base imponiéndolo.
        assert isinstance(e, (asyncpg.RaiseError, asyncpg.CheckViolationError)), (destino, e)
    assert await conn.fetchval("SELECT conciliacion FROM tercero WHERE id=$1", ficha) == terminal


@pytest.mark.asyncio
async def test_el_trigger_para_el_salto_terminal_a_terminal_aunque_los_check_lo_dejen(
    tenant_ctx,
) -> None:
    """El caso donde SOLO el trigger decide: vinculada -> descartada con todos los
    campos coherentes (autor, fecha, motivo, fusionado_en). Si alguien quita el
    trigger, ningún CHECK lo atrapa."""
    slug, conn = await _entorno(tenant_ctx)
    real = await _existente(conn, "55")
    ficha = await _anonimo(conn, slug, documento="55", asunto="Salto")
    await _conciliar(conn, slug, ficha, "vincular", destino_id=real)

    e = await _rechaza(
        conn,
        "UPDATE tercero SET conciliacion='descartada', conciliacion_motivo='motivo suficientemente largo' "
        "WHERE id=$1",
        ficha,
    )
    assert isinstance(e, asyncpg.RaiseError)
    assert "no se rehace" in str(e)


@pytest.mark.asyncio
async def test_una_confirmada_no_vuelve_a_provisional(tenant_ctx) -> None:
    """Rama propia del trigger: una confirmada con evidencia declarada (`conciliacion` NULL,
    por tanto sin estado terminal que lo ataje antes) no puede regresar a provisional.
    Se construye por INSERT porque ningún flujo normal la produce; es la defensa en
    profundidad de la regla."""
    slug, conn = await _entorno(tenant_ctx)
    ficha = await conn.fetchval(
        "INSERT INTO tercero (nombre, tipo, tipo_documento, numero_documento, "
        "documento_declarado_tipo, documento_declarado_numero) "
        "VALUES ('Confirmada', 'ciudadano', 'CC', '66', 'CC', '66') RETURNING id"
    )
    e = await _rechaza(
        conn,
        "UPDATE tercero SET identidad='provisional', tipo_documento=NULL, numero_documento=NULL, "
        "conciliacion='pendiente' WHERE id=$1",
        ficha,
    )
    assert isinstance(e, asyncpg.RaiseError)
    assert "no vuelve a provisional" in str(e)
    # Y la que sí pasó por conciliación tampoco (la ataja la rama de estados terminales).
    otra = await _anonimo(conn, slug, documento="67", asunto="Sin vuelta")
    await _conciliar(conn, slug, otra, "confirmar")
    e = await _rechaza(
        conn,
        "UPDATE tercero SET identidad='provisional', tipo_documento=NULL, numero_documento=NULL, "
        "conciliacion='pendiente', conciliada_at=NULL, conciliada_por=NULL WHERE id=$1",
        otra,
    )
    assert isinstance(e, asyncpg.RaiseError)


@pytest.mark.asyncio
async def test_los_check_de_la_049_cierran_las_demas_puertas(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    ficha = await _anonimo(conn, slug, documento="77", asunto="Checks")
    real = await _existente(conn, "88")

    # Motivo corto (< 10 caracteres tras btrim) aunque la conciliación sea coherente.
    await _rechaza(
        conn,
        "UPDATE tercero SET conciliacion='descartada', conciliada_at=now(), conciliada_por=$2, "
        "conciliacion_motivo='   corto   ' WHERE id=$1", ficha, ACTOR,
    )
    # `descartada` sin motivo.
    await _rechaza(
        conn,
        "UPDATE tercero SET conciliacion='descartada', conciliada_at=now(), conciliada_por=$2 WHERE id=$1",
        ficha, ACTOR,
    )
    # `vinculada` sin `fusionado_en`.
    await _rechaza(
        conn,
        "UPDATE tercero SET conciliacion='vinculada', conciliada_at=now(), conciliada_por=$2 WHERE id=$1",
        ficha, ACTOR,
    )
    # `pendiente` con autor (el biconditional lo cierra en ambos sentidos).
    await _rechaza(conn, "UPDATE tercero SET conciliada_por=$2 WHERE id=$1", ficha, ACTOR)
    # Valores fuera de catálogo.
    await _rechaza(conn, "UPDATE tercero SET identidad='dudosa' WHERE id=$1", ficha)
    await _rechaza(conn, "UPDATE tercero SET conciliacion='hecha' WHERE id=$1", ficha)
    # Una ficha confirmada de siempre no puede ponerse `pendiente` (ni fingirse en cola).
    await _rechaza(
        conn, "UPDATE tercero SET conciliacion='pendiente' WHERE id=$1", real
    )
    # Insertar una provisional que ya trae identidad, o sin documento declarado.
    await _rechaza(
        conn,
        "INSERT INTO tercero (nombre, tipo, identidad, tipo_documento, numero_documento, "
        "documento_declarado_numero, conciliacion) "
        "VALUES ('X','ciudadano','provisional','CC','1','1','pendiente')",
    )
    await _rechaza(
        conn,
        "INSERT INTO tercero (nombre, tipo, identidad, conciliacion) "
        "VALUES ('X','ciudadano','provisional','pendiente')",
    )
    # Control positivo: la ficha sigue pendiente e intacta tras tanto rechazo.
    fila = await conn.fetchrow("SELECT conciliacion, conciliada_por FROM tercero WHERE id=$1", ficha)
    assert (fila["conciliacion"], fila["conciliada_por"]) == ("pendiente", None)
    # y el índice parcial de la cola la cubre
    assert await conn.fetchval(
        "SELECT count(*) FROM pg_indexes WHERE indexname = 'ix_tercero_por_conciliar' "
        "AND schemaname = current_schema()"
    ) == 1
