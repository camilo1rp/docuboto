# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Vincular y luego fusionar: la cadena de fusiones se lee ENTERA y se bloquea (C17, auditoría C-3).

Antes `radicados_de` resolvía `fusionado_en` en UN nivel: P -> D (vinculada) y luego D -> X
dejaba los radicados de P fuera de X. Y `conciliar` validaba el destino sin bloquearlo: una
fusión entre la validación y el UPDATE dejaba P atada a una ficha que ya no era superviviente.
Contra Postgres real, con dos sesiones donde la carrera importa.
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
import pytest_asyncio

from app.core.database import tenant_schema
from app.repositories.tercero_repository import TerceroRepository
from app.services.tercero_service import TerceroService
from tests.integration.conftest import _require_dsn
from tests.integration.test_ficha_provisional_integration import (
    _anonimo,
    _conciliar,
    _entorno,
    _existente,
)

pytestmark = pytest.mark.integration

OTRO_ACTOR = uuid.uuid4()


@pytest_asyncio.fixture
async def conn2(tenant_ctx):
    slug, _ = tenant_ctx
    c = await asyncpg.connect(dsn=_require_dsn())
    await c.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    try:
        yield c
    finally:
        await c.close()


async def _duplicada(conn) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO tercero (nombre, tipo, email) VALUES ('María G. (dup)', 'ciudadano', "
        "'maria2@real.co') RETURNING id"
    )


async def _tn(conn, asunto: str) -> str:
    return await conn.fetchval("SELECT tracking_number FROM radicados WHERE subject = $1", asunto)


@pytest.mark.asyncio
async def test_vincular_y_luego_fusionar_no_pierde_los_radicados(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    d, x = await _existente(conn), await _duplicada(conn)
    p = await _anonimo(conn, slug, asunto="Radicado de P")
    tn = await _tn(conn, "Radicado de P")
    svc = TerceroService(conn, slug)

    await _conciliar(conn, slug, p, "vincular", destino_id=d)
    assert [r["tracking_number"] for r in await svc.radicados_de(d, max_clearance=3)] == [tn]
    await svc.fusionar(x, d, actor=OTRO_ACTOR)  # D -> X, con P ya colgando de D

    for ficha in (x, d):  # la cadena P -> D -> X se lee entera
        assert tn in [r["tracking_number"] for r in await svc.radicados_de(ficha, max_clearance=3)]


@pytest.mark.asyncio
async def test_la_cadena_entera_respeta_el_nivel_de_seguridad(tenant_ctx) -> None:
    """El gate de nivel sigue en el MISMO WHERE aunque la ficha esté a dos saltos."""
    slug, conn = await _entorno(tenant_ctx)
    d, x = await _existente(conn), await _duplicada(conn)
    p = await _anonimo(conn, slug, asunto="Reservada de P", nivel=3)
    tn = await _tn(conn, "Reservada de P")
    await _conciliar(conn, slug, p, "vincular", destino_id=d)
    svc = TerceroService(conn, slug)
    await svc.fusionar(x, d, actor=OTRO_ACTOR)

    assert tn not in [r["tracking_number"] for r in await svc.radicados_de(x, max_clearance=1)]
    assert tn in [r["tracking_number"] for r in await svc.radicados_de(x, max_clearance=3)]


@pytest.mark.asyncio
async def test_un_ciclo_en_fusionado_en_no_cuelga_la_lectura(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    d, x = await _existente(conn), await _duplicada(conn)
    p = await _anonimo(conn, slug, asunto="Con ciclo")
    await _conciliar(conn, slug, p, "vincular", destino_id=d)
    await conn.execute("UPDATE tercero SET fusionado_en=$1, fusionado_at=now() WHERE id=$2", x, d)
    await conn.execute("UPDATE tercero SET fusionado_en=$1, fusionado_at=now() WHERE id=$2", d, x)

    filas = await asyncio.wait_for(
        TerceroService(conn, slug).radicados_de(x, max_clearance=3), timeout=10
    )
    assert len(filas) == 1


@pytest.mark.asyncio
async def test_fusionar_el_destino_no_se_cuela_entre_la_validacion_y_el_update(
    tenant_ctx, conn2, monkeypatch
) -> None:
    """`conciliar` bloquea P y D juntos: la fusión concurrente de D ESPERA (aquí, timeout),
    así que P nunca queda atada a una ficha que dejó de ser superviviente."""
    slug, conn = await _entorno(tenant_ctx)
    d, x = await _existente(conn), await _duplicada(conn)
    p = await _anonimo(conn, slug, asunto="Radicado de P2")
    tn = await _tn(conn, "Radicado de P2")
    bloqueada = {}

    original = TerceroRepository.conciliar_vincular

    async def _con_carrera(self, *a, **kw):
        try:
            await asyncio.wait_for(
                TerceroService(conn2, slug).fusionar(x, d, actor=OTRO_ACTOR), timeout=2
            )
            bloqueada["fusiono"] = True
        except TimeoutError:
            bloqueada["fusiono"] = False
        return await original(self, *a, **kw)

    monkeypatch.setattr(TerceroRepository, "conciliar_vincular", _con_carrera)
    res = await _conciliar(conn, slug, p, "vincular", destino_id=d)

    assert res["conciliacion"] == "vinculada"
    assert bloqueada == {"fusiono": False}, "la fusión de D debía esperar a la conciliación"
    assert await conn.fetchval("SELECT fusionado_en FROM tercero WHERE id=$1", p) == d
    assert await conn.fetchval("SELECT fusionado_en FROM tercero WHERE id=$1", d) is None
    # Control positivo: terminada la conciliación, la fusión sí procede y X lo ve todo.
    await TerceroService(conn, slug).fusionar(x, d, actor=OTRO_ACTOR)
    assert tn in [
        r["tracking_number"]
        for r in await TerceroService(conn, slug).radicados_de(x, max_clearance=3)
    ]


@pytest.mark.asyncio
async def test_el_update_exige_un_destino_vivo_sin_bloqueo_previo(tenant_ctx) -> None:
    """El `UPDATE` lleva su propio `EXISTS` del destino: aunque el bloqueo faltara, no vincula
    a una ficha ya fusionada."""
    slug, conn = await _entorno(tenant_ctx)
    d, x = await _existente(conn), await _duplicada(conn)
    p = await _anonimo(conn, slug, asunto="Destino ya fusionado")
    await TerceroService(conn, slug).fusionar(x, d, actor=OTRO_ACTOR)  # D ya no es superviviente

    repo = TerceroRepository(conn, slug)
    assert (
        await repo.conciliar_vincular(
            p, d, actor=OTRO_ACTOR, motivo=None, medio="presencial_documento", max_clearance=3
        )
        is None
    )
    assert await conn.fetchval("SELECT conciliacion FROM tercero WHERE id=$1", p) == "pendiente"
    # Control positivo contra el superviviente real.
    assert (
        await repo.conciliar_vincular(
            p, x, actor=OTRO_ACTOR, motivo="misma persona", medio="presencial_documento",
            max_clearance=3,
        )
        is not None
    )
