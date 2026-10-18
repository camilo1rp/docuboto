# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""B10 ítem 3 contra Postgres REAL: gate vivo en el WHERE y barrido de reconciliación.

La garantía es del ``WHERE`` evaluado por el motor (``GREATEST`` + ``LEFT JOIN`` a
``radicados``), no de un mock: ningún doble puede decir si un fragmento con el nivel
viejo sale en una búsqueda cuando su radicado ya subió.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from orpycamcp_common.deadletter import RadicadosAusenteError

from app.core.config import settings
from app.jobs import reconciliacion_nivel
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services.embeddings import embed, to_pgvector

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

TEXTO = "contrato de arrendamiento de la sede norte"


async def _radicado(conn: asyncpg.Connection, nivel: int) -> str:
    tn = f"2026-ITEST-E-{uuid.uuid4().hex[:8]}"
    await conn.execute(
        "INSERT INTO radicados (tracking_number, nivel_seguridad) VALUES ($1, $2)", tn, nivel
    )
    return tn


async def _chunk(conn: asyncpg.Connection, ref: str, nivel: int, tipo: str = "radicado") -> None:
    await KnowledgeRepository(conn).upsert_chunk(
        source_type=tipo,
        source_ref=ref,
        texto=TEXTO,
        embedding_literal=to_pgvector(embed(TEXTO)),
        metadata={},
        acl={},
        nivel_seguridad=nivel,
    )


async def _buscar(conn: asyncpg.Connection, clearance: int) -> list[asyncpg.Record]:
    return await KnowledgeRepository(conn).search(
        embedding_literal=to_pgvector(embed(TEXTO)),
        top_k=10,
        max_clearance=clearance,
        acl_filter=None,
    )


async def _nivel_chunk(conn: asyncpg.Connection, ref: str) -> int:
    return await conn.fetchval(
        "SELECT nivel_seguridad FROM knowledge_chunk WHERE source_ref = $1", ref
    )


# ---------------------------------------------------------------------------
# Gate vivo
# ---------------------------------------------------------------------------


async def test_search_no_devuelve_chunk_cuyo_radicado_subio_de_nivel(tenant_ctx) -> None:
    """Fragmento guardado a nivel 1, radicado ya en 3 (el `reclassified` no llegó)."""
    _slug, conn = tenant_ctx
    tn = await _radicado(conn, 3)
    await _chunk(conn, tn, 1)

    assert await _buscar(conn, 1) == []
    assert await _buscar(conn, 2) == []
    assert [r["source_ref"] for r in await _buscar(conn, 3)] == [tn]  # control positivo


async def test_rag_usa_el_nivel_vivo_para_la_residencia(tenant_ctx) -> None:
    """El nivel devuelto es el vivo: es el que decide `is_generatable` en /rag."""
    _slug, conn = tenant_ctx
    tn = await _radicado(conn, 3)
    await _chunk(conn, tn, 1)

    hits = await _buscar(conn, 3)
    assert hits[0]["nivel_seguridad"] == 3  # no el 1 que guardó el fragmento


async def test_pivot_chunk_usa_el_nivel_vivo(tenant_ctx) -> None:
    _slug, conn = tenant_ctx
    tn = await _radicado(conn, 2)
    await _chunk(conn, tn, 1)

    pivote = await KnowledgeRepository(conn).get_pivot_chunk("radicado", tn, max_clearance=3)
    assert pivote is not None and pivote["nivel_seguridad"] == 2
    # El gate vive en el WHERE: sobre el clearance, ni fila ni embedding.
    assert await KnowledgeRepository(conn).get_pivot_chunk("radicado", tn, max_clearance=1) is None


async def test_el_nivel_vivo_no_baja_el_del_fragmento(tenant_ctx) -> None:
    """Radicado en 1 y fragmento en 3: manda el mayor (bajar es de un `reclassified`)."""
    _slug, conn = tenant_ctx
    tn = await _radicado(conn, 1)
    await _chunk(conn, tn, 3)

    assert await _buscar(conn, 1) == []
    assert (await _buscar(conn, 3))[0]["nivel_seguridad"] == 3


async def test_fuente_que_no_es_radicado_conserva_su_nivel(tenant_ctx) -> None:
    """El JOIN solo aplica a `source_type='radicado'`: un `source_ref` homónimo de otro
    tipo no hereda el nivel del radicado."""
    _slug, conn = tenant_ctx
    tn = await _radicado(conn, 3)
    await _chunk(conn, tn, 1, tipo="nota")

    assert [r["source_ref"] for r in await _buscar(conn, 1)] == [tn]


async def test_sin_la_tabla_radicados_el_gate_da_503_no_vacio(tenant_ctx) -> None:
    _slug, conn = tenant_ctx
    tn = await _radicado(conn, 1)
    await _chunk(conn, tn, 1)
    await conn.execute("DROP TABLE radicados")

    with pytest.raises(RadicadosAusenteError):
        await _buscar(conn, 3)
    with pytest.raises(RadicadosAusenteError):
        await KnowledgeRepository(conn).get_pivot_chunk("radicado", tn, max_clearance=3)


# ---------------------------------------------------------------------------
# Barrido
# ---------------------------------------------------------------------------


@pytest.fixture
def asientos(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    vistos: list[dict] = []

    class _Falso:
        @staticmethod
        async def append(conn, **kw):  # noqa: ANN001, ANN003
            vistos.append(kw)
            return "h"

    monkeypatch.setattr(reconciliacion_nivel, "audit", _Falso)
    return vistos


async def _barrer_con_proveedor(conn, slug, monkeypatch, proveedor: str) -> dict[str, int]:
    """El proveedor solo cambia durante el barrido: sembrar y buscar embeben con `stub`
    (un `ollama` real intentaría salir a la red)."""
    with monkeypatch.context() as m:
        m.setattr(settings, "embedding_provider", proveedor)
        return await reconciliacion_nivel.reconciliar_tenant(conn, slug)


async def test_barrido_sube_y_no_baja(tenant_ctx, asientos) -> None:
    slug, conn = tenant_ctx
    sube = await _radicado(conn, 3)
    baja = await _radicado(conn, 1)
    await _chunk(conn, sube, 1)
    await _chunk(conn, baja, 3)  # sobreclasificado

    r = await reconciliacion_nivel.reconciliar_tenant(conn, slug)

    assert await _nivel_chunk(conn, sube) == 3
    assert await _nivel_chunk(conn, baja) == 3  # NO baja
    assert r == {"subidos": 1, "tombstoneados": 0, "sobreclasificados": 1}


async def test_barrido_tombstonea_con_proveedor_externo(
    tenant_ctx, asientos, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    tn = await _radicado(conn, 2)
    await _chunk(conn, tn, 1)

    r = await _barrer_con_proveedor(conn, slug, monkeypatch, "ollama")  # no es local

    fila = await conn.fetchrow(
        "SELECT texto, embedding IS NULL AS sin_vector, nivel_seguridad "
        "FROM knowledge_chunk WHERE source_ref = $1", tn
    )
    assert r["tombstoneados"] == 1 and r["subidos"] == 0
    assert fila["texto"] == "" and fila["sin_vector"] and fila["nivel_seguridad"] == 2
    assert await _buscar(conn, 3) == []  # el centinela no es resultado de búsqueda


async def test_barrido_con_proveedor_local_no_vacia_el_material(
    tenant_ctx, asientos, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    tn = await _radicado(conn, 3)
    await _chunk(conn, tn, 1)

    await _barrer_con_proveedor(conn, slug, monkeypatch, "stub")

    assert await _nivel_chunk(conn, tn) == 3
    assert (await _buscar(conn, 3))[0]["source_ref"] == tn  # sigue indexado


@pytest.mark.parametrize("externo", [False, True])
async def test_barrido_no_toca_nivel_evento_at(
    tenant_ctx, asientos, monkeypatch: pytest.MonkeyPatch, externo: bool
) -> None:
    """El barrido no es un evento: si moviera `nivel_evento_at`, una reclasificación
    legítima con un `occurred_at` anterior quedaría ignorada."""
    slug, conn = tenant_ctx
    tn = await _radicado(conn, 3)
    await _chunk(conn, tn, 1)
    await conn.execute(
        "UPDATE knowledge_chunk SET nivel_evento_at = '2026-01-01T00:00:00Z' WHERE source_ref = $1",
        tn,
    )

    await _barrer_con_proveedor(conn, slug, monkeypatch, "ollama" if externo else "stub")

    assert await _nivel_chunk(conn, tn) == 3
    en = await conn.fetchval(
        "SELECT nivel_evento_at = '2026-01-01T00:00:00Z'::timestamptz "
        "FROM knowledge_chunk WHERE source_ref = $1", tn
    )
    assert en is True


async def test_barrido_deja_un_asiento_por_lote(tenant_ctx, asientos) -> None:
    slug, conn = tenant_ctx
    for _ in range(3):
        await _chunk(conn, await _radicado(conn, 2), 1)

    await reconciliacion_nivel.reconciliar_tenant(conn, slug)

    assert len(asientos) == 1
    a = asientos[0]
    assert a["action"] == "knowledge.nivel_reconciliado"
    assert a["actor"] == "system" and a["canal"] == "system"
    assert a["payload"]["subidos"] == 3 and len(a["payload"]["refs"]) == 3


async def test_barrido_es_idempotente_y_sin_cambios_no_deja_asiento(tenant_ctx, asientos) -> None:
    slug, conn = tenant_ctx
    await _chunk(conn, await _radicado(conn, 2), 1)
    await reconciliacion_nivel.reconciliar_tenant(conn, slug)
    asientos.clear()

    r = await reconciliacion_nivel.reconciliar_tenant(conn, slug)

    assert r["subidos"] == 0 and asientos == []


async def test_si_el_asiento_falla_el_lote_no_queda_corregido(
    tenant_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Correcciones y asiento: una sola transacción."""
    slug, conn = tenant_ctx
    tn = await _radicado(conn, 3)
    await _chunk(conn, tn, 1)

    class _Roto:
        @staticmethod
        async def append(conn, **kw):  # noqa: ANN001, ANN003
            raise RuntimeError("audit caído")

    monkeypatch.setattr(reconciliacion_nivel, "audit", _Roto)
    with pytest.raises(RuntimeError):
        await reconciliacion_nivel.reconciliar_tenant(conn, slug)

    assert await _nivel_chunk(conn, tn) == 1
