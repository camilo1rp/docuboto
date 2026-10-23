# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""ADR-037 / S1: con un proveedor de embeddings NO local, la ingesta no egresa texto de una
entidad que apagó el asistente. Con proveedor local la bandera ni se lee (índice al día).

Matriz (worker de `created`, `/ingest`, `reembed`): local+apagada indexa, remoto+apagada
salta CON asiento, remoto+encendida indexa. El replay de la DLQ reusa el worker.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core import config as cfg_module
from app.ops import reembed
from app.services import ingestion
from tests.conftest import TEST_INTERNAL_TOKEN

TENANT = "demo"
TRACKING = "2026-DEMO-E-000001"
PAYLOAD = {"tracking_number": TRACKING, "nivel_seguridad": 1, "subject": "Solicitud de contrato"}


def _repo(MockRepo):  # type: ignore[no-untyped-def]
    r = MockRepo.return_value
    r.get_chunk_state = AsyncMock(return_value=None)
    r.upsert_chunk = AsyncMock()
    r.tombstone_by_source = AsyncMock()
    return r


async def _created(monkeypatch, *, provider: str, bandera: bool):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(cfg_module.settings, "embedding_provider", provider)
    leida = AsyncMock(return_value=bandera)
    with (
        patch("app.services.ingestion.KnowledgeRepository") as MockRepo,
        patch("app.services.ingestion.asistente_habilitado", leida),
        patch("app.services.ingestion.embed", return_value=[0.0] * 4),
        patch("app.services.ingestion.to_pgvector", return_value="[0]"),
        patch("app.services.ingestion.audit.append", new_callable=AsyncMock) as asiento,
    ):
        repo = _repo(MockRepo)
        await ingestion.ingest_radicado_created(AsyncMock(), tenant_slug=TENANT, payload=PAYLOAD)
    return repo, asiento, leida


@pytest.mark.asyncio
async def test_local_con_bandera_apagada_indexa_y_ni_lee_la_bandera(monkeypatch) -> None:
    repo, asiento, leida = await _created(monkeypatch, provider="stub", bandera=False)
    repo.upsert_chunk.assert_awaited_once()
    asiento.assert_not_awaited()
    leida.assert_not_awaited()


@pytest.mark.asyncio
async def test_remoto_con_bandera_apagada_salta_con_asiento(monkeypatch) -> None:
    repo, asiento, _ = await _created(monkeypatch, provider="ollama", bandera=False)
    repo.upsert_chunk.assert_not_awaited()
    kw = asiento.await_args.kwargs
    assert kw["action"] == "knowledge.ingest_skipped_asistente_apagado"
    assert kw["object_ref"] == TRACKING
    assert kw["payload"]["embedding_provider"] == "ollama"


@pytest.mark.asyncio
async def test_remoto_con_bandera_encendida_indexa(monkeypatch) -> None:
    repo, asiento, leida = await _created(monkeypatch, provider="ollama", bandera=True)
    repo.upsert_chunk.assert_awaited_once()
    asiento.assert_not_awaited()
    leida.assert_awaited_once()


@pytest.mark.asyncio
async def test_remoto_y_registro_caido_falla_cerrado(monkeypatch) -> None:
    """Si no se puede leer la bandera, el evento FALLA (se reintenta); no egresa a ciegas."""
    monkeypatch.setattr(cfg_module.settings, "embedding_provider", "ollama")
    with (
        patch("app.services.ingestion.KnowledgeRepository") as MockRepo,
        patch("app.services.ingestion.asistente_habilitado", AsyncMock(side_effect=RuntimeError("x"))),
        patch("app.services.ingestion.embed") as embed,
    ):
        repo = _repo(MockRepo)
        with pytest.raises(RuntimeError):
            await ingestion.ingest_radicado_created(AsyncMock(), tenant_slug=TENANT, payload=PAYLOAD)
    repo.upsert_chunk.assert_not_awaited()
    embed.assert_not_called()


# --- POST /ingest -------------------------------------------------------------------------


async def _post_ingest(client, conn, monkeypatch, *, provider, bandera):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(cfg_module.settings, "internal_service_token", TEST_INTERNAL_TOKEN)
    monkeypatch.setattr(cfg_module.settings, "embedding_provider", provider)
    conn.fetchrow.return_value = {
        "id": uuid.uuid4(), "source_type": "radicado", "source_ref": "R-1", "created_at": datetime.now(UTC),
    }
    cuerpo = {"source_type": "radicado", "source_ref": "R-1", "texto": "hola", "nivel_seguridad": 1}
    with (
        patch("app.services.ingestion.asistente_habilitado", AsyncMock(return_value=bandera)),
        patch("app.routers.knowledge.embed", return_value=[0.0] * 4),
        patch("app.routers.knowledge.to_pgvector", return_value="[0]"),
        patch("app.routers.knowledge.audit.append", new_callable=AsyncMock) as asiento,
    ):
        resp = await client.post(
            "/api/v1/knowledge/ingest", json=cuerpo,
            headers={"X-Tenant-Slug": "demo", "X-Internal-Token": TEST_INTERNAL_TOKEN},
        )
    return resp, asiento


@pytest.mark.asyncio
async def test_ingest_local_apagada_201(client_with_conn, monkeypatch) -> None:
    client, conn = client_with_conn
    resp, asiento = await _post_ingest(client, conn, monkeypatch, provider="stub", bandera=False)
    assert resp.status_code == 201
    asiento.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_remoto_apagada_403_con_asiento(client_with_conn, monkeypatch) -> None:
    client, conn = client_with_conn
    resp, asiento = await _post_ingest(client, conn, monkeypatch, provider="ollama", bandera=False)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "asistente_deshabilitado"
    assert asiento.await_args.kwargs["action"] == "knowledge.ingest_skipped_asistente_apagado"
    assert not [c for c in conn.fetchrow.call_args_list if "knowledge_chunk" in str(c.args[0])]


@pytest.mark.asyncio
async def test_ingest_remoto_encendida_201(client_with_conn, monkeypatch) -> None:
    client, conn = client_with_conn
    resp, asiento = await _post_ingest(client, conn, monkeypatch, provider="ollama", bandera=True)
    assert resp.status_code == 201
    asiento.assert_not_awaited()


# --- reembed ---------------------------------------------------------------------------------


async def _reembed(monkeypatch, *, provider: str, bandera: bool):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(cfg_module.settings, "embedding_provider", provider)
    prov = MagicMock()
    prov.embed_documents = MagicMock(return_value=[[0.0]])
    fila = {"id": uuid.uuid4(), "texto": "t", "nivel_seguridad": 1, "source_type": "radicado", "source_ref": "R"}
    with (
        patch("app.ops.reembed.KnowledgeRepository") as MockRepo,
        patch("app.services.ingestion.asistente_habilitado", AsyncMock(return_value=bandera)),
        patch("app.ops.reembed._audit.append", new_callable=AsyncMock) as asiento,
        patch("app.ops.reembed.to_pgvector", return_value="[0]"),
    ):
        repo = MockRepo.return_value
        repo.list_pending_reembed_ids = AsyncMock(return_value=[fila["id"]])
        repo.fetch_chunks_by_ids = AsyncMock(return_value=[fila])
        repo.update_embedding = AsyncMock()
        res = await reembed._reembed_tenant(
            AsyncMock(), schema="tenant_demo", slug=TENANT, batch_size=8, dry_run=False, provider=prov
        )
    return res, prov, asiento


@pytest.mark.asyncio
async def test_reembed_local_apagada_re_embebe(monkeypatch) -> None:
    res, prov, _ = await _reembed(monkeypatch, provider="stub", bandera=False)
    assert res == (1, 0, 0)
    prov.embed_documents.assert_called_once()


@pytest.mark.asyncio
async def test_reembed_remoto_apagada_salta_con_asiento(monkeypatch) -> None:
    res, prov, asiento = await _reembed(monkeypatch, provider="ollama", bandera=False)
    assert res == (0, 1, 0)
    prov.embed_documents.assert_not_called()
    assert asiento.await_args.kwargs["action"] == "knowledge.reembed_skipped_asistente_apagado"


@pytest.mark.asyncio
async def test_reembed_remoto_encendida_re_embebe(monkeypatch) -> None:
    res, prov, _ = await _reembed(monkeypatch, provider="ollama", bandera=True)
    assert res == (1, 0, 0)
    prov.embed_documents.assert_called_once()
