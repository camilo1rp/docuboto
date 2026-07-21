# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`POST /api/v1/knowledge/antecedentes` (E21 Inc.2, Patrón A) contra Postgres
REAL — invoca la función del router DIRECTAMENTE (mismo patrón que el resto de
`tests/integration/`: p. ej. `test_reclassify_tombstone_leak.py` llama a
`ingestion.ingest_radicado_created` sin pasar por el transporte ASGI/HTTP), con
`conn` = la conexión real del tenant desechable (`tenant_ctx`) y `caller_id` =
`None` explícito.

`caller_id=None` es DELIBERADO en todos los casos de este archivo: el tenant
desechable de `tenant_ctx` no provisiona `auth_users`/`auth_memberships`/
`role_clearance` (esas tablas las posee auth-service, ver
`app/core/clearance.py::resolve_caller_clearance`, que captura
`asyncpg.UndefinedTableError` y cae a PUBLICA(1) fail-closed incluso con un
`user_id` real). Pasar `caller_id=None` hace explícito el mismo resultado
(PUBLICA=1, SIN tocar la conexión) sin depender de esa captura de excepción —
y es exactamente el escenario "anónimo"/"clearance más bajo" que estos tests
necesitan para poder sembrar un chunk CLASIFICADA y demostrar que lo excluye.

Cubre (mandato Inc.2):
  (a) pre-filtro ACL real: un chunk sobre el clearance del llamante NUNCA
      aparece en el camino `query` — la garantía es del `WHERE` de
      `KnowledgeRepository.search`, no de un mock.
  (b) camino `radicado_ref`: `get_pivot_chunk` lee el embedding YA indexado, se
      lo pasa a `search` como vector pivote, y el propio pivote se EXCLUYE de
      su resultado.
  (c) anti-oráculo (CRÍTICO): `radicado_ref` inexistente y `radicado_ref` que
      existe pero excede el clearance dan el MISMO 404
      `antecedente_source_not_found` — y en el caso sobre-clearance `search()`
      (`conn.fetch`) NUNCA se invoca.
  (d) aislamiento tenant: un `radicado_ref` indexado en el tenant A no se
      encuentra desde una conexión con el `search_path` del tenant B.
"""

from __future__ import annotations

import asyncpg
import pytest
from fastapi import HTTPException

from app.core.clearance import CLASIFICADA, PUBLICA
from app.core.database import _schema as tenant_schema
from app.repositories.knowledge_repository import KnowledgeRepository
from app.routers.knowledge import ANTECEDENTES_ADVISORY, AntecedentesRequest, antecedentes
from app.services.embeddings import embed, to_pgvector
from migrations import runner

from .conftest import DSN, _make_slug

pytestmark = pytest.mark.integration


async def _seed(
    conn: asyncpg.Connection,
    *,
    source_ref: str,
    texto: str,
    nivel: int,
    source_type: str = "radicado",
) -> None:
    literal = to_pgvector(embed(texto))
    await KnowledgeRepository(conn).upsert_chunk(
        source_type=source_type,
        source_ref=source_ref,
        texto=texto,
        embedding_literal=literal,
        metadata={},
        acl={},
        nivel_seguridad=nivel,
    )


# --- (a) pre-filtro ACL real en el camino `query` ------------------------------


@pytest.mark.integration
async def test_query_path_acl_prefilter_excludes_over_clearance_chunk(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await _seed(
        conn,
        source_ref="2026-ITEST-E-000001",
        texto="contrato de obra pública para mantenimiento vial",
        nivel=PUBLICA,
    )
    await _seed(
        conn,
        source_ref="2026-ITEST-E-000002",
        texto="contrato de obra pública para mantenimiento vial reservado",
        nivel=CLASIFICADA,
    )

    resp = await antecedentes(
        AntecedentesRequest(query="contrato de obra pública mantenimiento vial", top_k=10),
        conn=conn,
        caller_id=None,
    )

    refs = {a.source_ref for a in resp.antecedentes}
    assert "2026-ITEST-E-000001" in refs
    assert "2026-ITEST-E-000002" not in refs, (
        "el pre-filtro ACL server-side (nivel_seguridad <= max_clearance) debe "
        "excluir el chunk CLASIFICADA del clearance PUBLICA del llamante"
    )
    assert resp.advisory == ANTECEDENTES_ADVISORY


# --- (b) camino `radicado_ref`: pivote real + auto-exclusión ------------------


@pytest.mark.integration
async def test_radicado_ref_pivot_excludes_itself_and_respects_top_k(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    pivot_ref = "2026-ITEST-E-000010"
    await _seed(conn, source_ref=pivot_ref, texto="solicitud de certificado laboral", nivel=PUBLICA)

    other_refs = []
    for i in range(5):
        ref = f"2026-ITEST-E-0000{20 + i}"
        await _seed(
            conn, source_ref=ref, texto=f"solicitud de certificado laboral variante {i}", nivel=PUBLICA
        )
        other_refs.append(ref)

    resp = await antecedentes(
        AntecedentesRequest(radicado_ref=pivot_ref, top_k=3),
        conn=conn,
        caller_id=None,
    )

    assert len(resp.antecedentes) == 3, "top_k debe respetarse DESPUÉS de excluir el pivote"
    refs = [a.source_ref for a in resp.antecedentes]
    assert pivot_ref not in refs, "el pivote nunca debe devolverse como antecedente de sí mismo"
    assert all(r in other_refs for r in refs)
    assert resp.advisory == ANTECEDENTES_ADVISORY


@pytest.mark.integration
async def test_radicado_ref_pivot_uses_indexed_embedding_not_fresh_embed(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """`get_pivot_chunk` debe leer el embedding YA indexado (columna
    `embedding`), no re-embeber el `radicado_ref` desde cero: se comprueba
    sembrando el pivote con un texto MUY distinto al de los candidatos, pero
    forzando (vía `upsert_chunk` directo) que su VECTOR sea el mismo que el de
    uno de los candidatos — si el endpoint re-embebiera el texto del pivote en
    vez de leer la columna, el vecino más cercano real no coincidiría con el
    candidato de vector idéntico."""
    slug, conn = tenant_ctx
    candidato_texto = "trámite de pensión de vejez"
    literal = to_pgvector(embed(candidato_texto))

    # Pivote: texto de superficie irrelevante, pero MISMO vector que el candidato.
    await KnowledgeRepository(conn).upsert_chunk(
        source_type="radicado",
        source_ref="PIVOT-VEC",
        texto="un texto de superficie completamente distinto sobre otro tema",
        embedding_literal=literal,
        metadata={},
        acl={},
        nivel_seguridad=PUBLICA,
    )
    await _seed(conn, source_ref="CANDIDATO-VEC", texto=candidato_texto, nivel=PUBLICA)
    # Ruido: vector claramente distinto, no debería quedar primero.
    await _seed(conn, source_ref="RUIDO-VEC", texto="factura de servicios públicos", nivel=PUBLICA)

    resp = await antecedentes(
        AntecedentesRequest(radicado_ref="PIVOT-VEC", top_k=1), conn=conn, caller_id=None
    )

    assert len(resp.antecedentes) == 1
    assert resp.antecedentes[0].source_ref == "CANDIDATO-VEC", (
        "el pivote debe usar su EMBEDDING indexado (idéntico al del candidato), "
        "no un re-embed de su propio texto de superficie"
    )


# --- (c) anti-oráculo: 404 idéntico, `search()` nunca invocado -----------------


@pytest.mark.integration
async def test_radicado_ref_404_collapses_missing_and_over_clearance(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    classified_ref = "2026-ITEST-E-000099"
    await _seed(conn, source_ref=classified_ref, texto="documento clasificado", nivel=CLASIFICADA)

    with pytest.raises(HTTPException) as exc_missing:
        await antecedentes(
            AntecedentesRequest(radicado_ref="no-existe-nunca"), conn=conn, caller_id=None
        )
    with pytest.raises(HTTPException) as exc_over_clearance:
        await antecedentes(
            AntecedentesRequest(radicado_ref=classified_ref), conn=conn, caller_id=None
        )

    assert exc_missing.value.status_code == 404
    assert exc_over_clearance.value.status_code == 404
    assert exc_missing.value.detail == exc_over_clearance.value.detail, (
        "el 404 de 'no existe' y el de 'existe pero excede el clearance' deben "
        "ser BYTE-A-BYTE indistinguibles (anti-oráculo de existencia)"
    )
    assert exc_missing.value.detail["error"] == "antecedente_source_not_found"


@pytest.mark.integration
async def test_radicado_ref_over_clearance_never_invokes_search(
    tenant_ctx: tuple[str, asyncpg.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contra Postgres real: si el pivote sobre-clearance se filtrara hacia
    `search` como vector query (en vez de cortar antes con 404), `search()` se
    invocaría. Se instala un espía que hace fallar el test con un tipo de
    excepción DISTINTO (`AssertionError`, no `HTTPException`) si eso ocurre —
    así una regresión que sí llame a `search` se distingue claramente de un 404
    legítimo."""
    slug, conn = tenant_ctx
    classified_ref = "2026-ITEST-E-000098"
    await _seed(conn, source_ref=classified_ref, texto="documento clasificado 2", nivel=CLASIFICADA)

    async def _boom(self: KnowledgeRepository, **_kwargs: object) -> None:
        raise AssertionError(
            "search() no debe invocarse cuando el pivote excede el clearance del llamante"
        )

    monkeypatch.setattr(KnowledgeRepository, "search", _boom)

    with pytest.raises(HTTPException) as exc:
        await antecedentes(
            AntecedentesRequest(radicado_ref=classified_ref), conn=conn, caller_id=None
        )
    assert exc.value.status_code == 404
    assert exc.value.detail["error"] == "antecedente_source_not_found"


@pytest.mark.integration
async def test_radicado_ref_missing_never_invokes_search(
    tenant_ctx: tuple[str, asyncpg.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gemelo del test anterior para la rama 'no existe': tampoco debe llamar a
    `search()` (no hay vector pivote que pasarle)."""
    slug, conn = tenant_ctx

    async def _boom(self: KnowledgeRepository, **_kwargs: object) -> None:
        raise AssertionError("search() no debe invocarse cuando el pivote no existe")

    monkeypatch.setattr(KnowledgeRepository, "search", _boom)

    with pytest.raises(HTTPException) as exc:
        await antecedentes(
            AntecedentesRequest(radicado_ref="no-existe-tampoco"), conn=conn, caller_id=None
        )
    assert exc.value.status_code == 404


# --- (d) aislamiento tenant -----------------------------------------------------


@pytest.mark.integration
async def test_radicado_ref_from_other_tenant_is_not_found(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Un `radicado_ref` indexado en el tenant A (`tenant_ctx`) no se encuentra
    desde una conexión SEGUNDA cuyo `search_path` apunta al tenant B — mismo
    patrón de conexión-gemela que `test_second_connection_sees_tombstone_durably`
    (tombstone leak test), pero aquí se usa para PROBAR la ausencia entre
    schemas, no la durabilidad dentro de uno solo."""
    slug_a, conn_a = tenant_ctx
    ref = "2026-ITEST-E-000050"
    await _seed(conn_a, source_ref=ref, texto="documento exclusivo del tenant A", nivel=PUBLICA)

    slug_b = _make_slug("antecedentes-tenant-b")
    schema_b = tenant_schema(slug_b)
    conn_b = await asyncpg.connect(dsn=DSN)
    try:
        await conn_b.execute(f'CREATE SCHEMA "{schema_b}"')
        for path in runner._sql_files(runner.TENANT_DIR):
            async with conn_b.transaction():
                await conn_b.execute(f'SET LOCAL search_path = "{schema_b}", public')
                await conn_b.execute(path.read_text())
        await conn_b.execute(f'SET search_path = "{schema_b}", public')

        with pytest.raises(HTTPException) as exc:
            await antecedentes(AntecedentesRequest(radicado_ref=ref), conn=conn_b, caller_id=None)
        assert exc.value.status_code == 404
        assert exc.value.detail["error"] == "antecedente_source_not_found"

        # Confirmación positiva: el MISMO ref sí se resuelve (no 404) dentro del
        # tenant A — no lanza, y la lista viene vacía porque `ref` es el único
        # chunk sembrado ahí (el pivote se excluye de su propio resultado).
        resp_a = await antecedentes(
            AntecedentesRequest(radicado_ref=ref), conn=conn_a, caller_id=None
        )
        assert resp_a.antecedentes == []
    finally:
        assert schema_b.startswith("tenant_itest_")
        await conn_b.execute(f'DROP SCHEMA IF EXISTS "{schema_b}" CASCADE')
        await conn_b.close()
