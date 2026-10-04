# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-11-11 / CC-02 contra Postgres REAL: aceptación humana de sugerencias.

Lo que un mock no puede probar y este tier sí:
  - la propiedad (`creada_por`) se decide en el ``WHERE`` del motor: otra persona,
    o una fila anterior a la migración 012 (sin creador), no la acepta;
  - la aceptación y su asiento son UN acto: si el asiento falla, la aceptación se
    revierte (`aceptada_en` sigue NULL);
  - una segunda aceptación no pisa a la primera.

AREA-11-12 en la misma pasada: con ``AI_PROVIDER=ollama_local`` y una ``OLLAMA_URL``
pública, un chunk nivel >= RESERVADA leído de Postgres real NO llega al proveedor.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.routers.knowledge as knowledge_router
from app.core import config as cfg_module
from app.core.clearance import CLASIFICADA, PUBLICA, RESERVADA
from app.repositories.kb_suggestion_repository import KbSuggestionRepository
from app.repositories.knowledge_repository import KnowledgeRepository
from app.routers.knowledge import RagRequest, aceptar_sugerencia, rag
from app.services.embeddings import embed, to_pgvector
from app.services.llm import GeneratedAnswer, RetrievedChunk

pytestmark = pytest.mark.integration


def _request(tenant_slug: str) -> Request:
    return Request({"type": "http", "headers": [(b"x-tenant-slug", tenant_slug.encode())]})


async def _crear(conn: asyncpg.Connection, *, creada_por: uuid.UUID | None) -> uuid.UUID:
    row = await KbSuggestionRepository(conn).insert(
        tipo="rag_respuesta",
        objeto_ref=None,
        sugerencia={"query": "q", "answer": "a", "citations": []},
        proveedor="ollama_local",
        modelo="m",
        confianza=0.5,
        canal="api",
        creada_por=creada_por,
    )
    return row["id"]


async def _aceptar(slug: str, conn: asyncpg.Connection, sid: uuid.UUID, actor: uuid.UUID):
    return await aceptar_sugerencia(
        sid, request=_request(slug), conn=conn, actor=actor, canal="api"
    )


async def _estado(conn: asyncpg.Connection, sid: uuid.UUID) -> asyncpg.Record:
    return await conn.fetchrow(
        "SELECT aceptada_por, aceptada_en FROM kb_suggestions WHERE id = $1", sid
    )


async def _asientos(conn: asyncpg.Connection, sid: uuid.UUID) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM public.audit_log "
        "WHERE action = 'rag.aceptada' AND object_ref = $1",
        str(sid),
    )


async def test_quien_la_genero_la_acepta_y_queda_asiento(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    yo = uuid.uuid4()
    sid = await _crear(conn, creada_por=yo)

    resp = await _aceptar(slug, conn, sid, yo)

    assert resp.aceptada_por == yo
    estado = await _estado(conn, sid)
    assert estado["aceptada_por"] == yo and estado["aceptada_en"] is not None
    assert await _asientos(conn, sid) == 1


async def test_el_asiento_fija_que_texto_se_acepto(tenant_ctx) -> None:
    """La fila es mutable y está fuera de la cadena hash: el asiento lleva la huella."""
    import hashlib
    import json

    slug, conn = tenant_ctx
    yo = uuid.uuid4()
    sid = await _crear(conn, creada_por=yo)

    await _aceptar(slug, conn, sid, yo)

    payload = await conn.fetchval(
        "SELECT payload FROM public.audit_log "
        "WHERE action = 'rag.aceptada' AND object_ref = $1",
        str(sid),
    )
    payload = json.loads(payload) if isinstance(payload, str) else payload
    assert payload["sha256_texto"] == hashlib.sha256(b"a").hexdigest()
    assert payload["proveedor"] == "ollama_local" and payload["modelo"] == "m"
    assert payload["fuentes"] == []


async def test_otra_persona_no_la_acepta_y_no_se_distingue_de_no_existe(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    sid = await _crear(conn, creada_por=uuid.uuid4())

    with pytest.raises(HTTPException) as ajena:
        await _aceptar(slug, conn, sid, uuid.uuid4())
    with pytest.raises(HTTPException) as inexistente:
        await _aceptar(slug, conn, uuid.uuid4(), uuid.uuid4())

    assert ajena.value.status_code == inexistente.value.status_code == 404
    assert ajena.value.detail == inexistente.value.detail
    assert (await _estado(conn, sid))["aceptada_en"] is None
    assert await _asientos(conn, sid) == 0


async def test_una_fila_sin_creador_no_es_aceptable(tenant_ctx) -> None:
    """Anterior a la migración 012: no hay forma honesta de saber de quién es."""
    slug, conn = tenant_ctx
    sid = await _crear(conn, creada_por=None)

    with pytest.raises(HTTPException) as exc:
        await _aceptar(slug, conn, sid, uuid.uuid4())

    assert exc.value.status_code == 404
    assert (await _estado(conn, sid))["aceptada_en"] is None


async def test_la_segunda_aceptacion_da_409_y_no_pisa_la_primera(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    yo = uuid.uuid4()
    sid = await _crear(conn, creada_por=yo)
    await _aceptar(slug, conn, sid, yo)
    primera = (await _estado(conn, sid))["aceptada_en"]

    with pytest.raises(HTTPException) as exc:
        await _aceptar(slug, conn, sid, yo)

    assert exc.value.status_code == 409
    assert (await _estado(conn, sid))["aceptada_en"] == primera
    assert await _asientos(conn, sid) == 1


async def test_si_el_asiento_falla_la_aceptacion_se_revierte(
    tenant_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomicidad: aceptación y asiento en la MISMA transacción."""
    slug, conn = tenant_ctx
    yo = uuid.uuid4()
    sid = await _crear(conn, creada_por=yo)

    async def _roto(*_a, **_k):
        raise RuntimeError("audit_log caído")

    monkeypatch.setattr(knowledge_router.audit, "append", _roto)

    with pytest.raises(RuntimeError):
        await _aceptar(slug, conn, sid, yo)

    assert (await _estado(conn, sid))["aceptada_en"] is None


class _Proveedor:
    model_id = "fake"

    def __init__(self) -> None:
        self.recibidos: list[RetrievedChunk] | None = None

    def generate_grounded(self, query: str, chunks: list[RetrievedChunk]) -> GeneratedAnswer:
        self.recibidos = list(chunks)
        return GeneratedAnswer(answer="r", citations=[], model_id=self.model_id)


async def _alto(_c, _u) -> int:
    return CLASIFICADA


@pytest.mark.parametrize(
    ("url", "sale"),
    [("https://ollama.example.com", False), ("http://ollama:11434", True)],
)
async def test_ollama_local_con_url_publica_no_recibe_material_reservado(
    tenant_ctx, monkeypatch: pytest.MonkeyPatch, url: str, sale: bool
) -> None:
    slug, conn = tenant_ctx
    texto = "licencia de construcción reservada"
    repo = KnowledgeRepository(conn)
    for ref, nivel in (("PUB-1", PUBLICA), ("RES-1", RESERVADA)):
        await repo.upsert_chunk(
            source_type="radicado", source_ref=ref, texto=texto + ref,
            embedding_literal=to_pgvector(embed(texto + ref)), metadata={}, acl={},
            nivel_seguridad=nivel,
        )
    monkeypatch.setattr(cfg_module.settings, "ai_provider", "ollama_local")
    monkeypatch.setattr(cfg_module.settings, "ollama_url", url)
    prov = _Proveedor()
    monkeypatch.setattr(knowledge_router, "get_llm_provider", lambda name=None: prov)
    monkeypatch.setattr(knowledge_router, "resolve_caller_clearance", _alto)

    await rag(
        RagRequest(query=texto, top_k=10), request=_request(slug), conn=conn,
        caller_id=None, canal="system",
    )

    refs = {c.source_ref for c in prov.recibidos}
    assert "PUB-1" in refs
    assert ("RES-1" in refs) is sale
