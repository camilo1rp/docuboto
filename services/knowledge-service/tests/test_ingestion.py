# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de la ingesta event-driven del índice de conocimiento (E21).

Cubre `app/services/ingestion.py`:
  - created embeddable → upsert con nivel EFECTIVO correcto, texto = subject/sender.
  - created con nivel ausente → fail-closed a CLASIFICADA (3).
  - created Reservado/Clasificado + proveedor externo → tombstone + SKIP auditado.
  - created cuyo chunk YA existe a un nivel más alto (fuga cerrada, hallazgo
    tenant-security-auditor #1) → nivel EFECTIVO = max(evento, existente); si el
    efectivo no es embebible, tombstonea y NO reenvía al proveedor externo.
  - created cuyo chunk YA existe voided (residual "purga-en-anulación") → SKIP
    TOTAL, incondicional, sin importar el nivel del evento.
  - reclassified sube a Reservado + proveedor externo → tombstone_by_source (R1).
  - reclassified a Pública (o proveedor stub) → update_nivel_by_source exacto.
  - reclassified sin nivel_nuevo → fail-closed a CLASIFICADA (#3) + asiento
    best-effort `knowledge.reclassify_malformed_failclose` en audit_log (#Media).
  - fallo del repositorio (embed/BD) → la excepción propaga (el worker no ackea).
  - annulled → void_radicado purga PERMANENTEMENTE el chunk (voided=true).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.core import config as cfg_module
from app.core.clearance import CLASIFICADA, PUBLICA, RESERVADA
from app.services import ingestion

TENANT = "demo"
TRACKING = "2026-DEMO-E-000001"


def _mock_repo(MockRepo, *, nivel_existente: int | None = None, voided: bool = False):
    """Cablea un KnowledgeRepository mockeado con los AsyncMock necesarios."""
    mock_repo = MockRepo.return_value
    mock_repo.get_nivel_by_source = AsyncMock(return_value=nivel_existente)
    mock_repo.get_chunk_state = AsyncMock(
        return_value=(
            {"nivel_seguridad": nivel_existente, "voided": voided}
            if nivel_existente is not None or voided
            else None
        )
    )
    mock_repo.upsert_chunk = AsyncMock()
    mock_repo.tombstone_by_source = AsyncMock()
    mock_repo.update_nivel_by_source = AsyncMock()
    mock_repo.delete_by_source = AsyncMock()
    mock_repo.void_by_source = AsyncMock()
    return mock_repo


@pytest.mark.asyncio
async def test_ingest_embeddable_upserts_with_derived_text_and_level() -> None:
    """Nivel Público (proveedor stub por defecto), sin chunk previo: se embebe."""
    conn = AsyncMock()
    payload = {
        "id": "doc-id-1",
        "tracking_number": TRACKING,
        "nivel_seguridad": PUBLICA,
        "subject": "Solicitud de información sobre contrato",
        "sender_name": "Juan Pérez",
        "sender_entity": "Alcaldía de Demo",
        "doc_type": "E",
        "doc_class": "peticion",
        "dest_dept": "Jurídica",
        "dest_dept_code": 10,
        "origin_dept": "Externo",
    }

    with patch("app.services.ingestion.KnowledgeRepository") as MockRepo:
        mock_repo = _mock_repo(MockRepo, nivel_existente=None)
        await ingestion.ingest_radicado_created(conn, tenant_slug=TENANT, payload=payload)

    mock_repo.get_chunk_state.assert_awaited_once_with("radicado", TRACKING)
    mock_repo.upsert_chunk.assert_awaited_once()
    kw = mock_repo.upsert_chunk.await_args.kwargs
    assert kw["source_type"] == "radicado"
    assert kw["source_ref"] == TRACKING
    assert kw["texto"] == "Solicitud de información sobre contrato\nJuan Pérez\nAlcaldía de Demo"
    assert kw["nivel_seguridad"] == PUBLICA
    assert kw["metadata"] == {
        "id": "doc-id-1",
        "tracking_number": TRACKING,
        "doc_type": "E",
        "doc_class": "peticion",
        "dest_dept": "Jurídica",
        "dest_dept_code": 10,
        "origin_dept": "Externo",
    }
    mock_repo.tombstone_by_source.assert_not_called()


@pytest.mark.asyncio
async def test_ingest_omits_falsy_text_fields() -> None:
    """El texto es el join por \\n de los campos NO vacíos entre subject/sender_*."""
    conn = AsyncMock()
    payload = {
        "tracking_number": TRACKING,
        "nivel_seguridad": PUBLICA,
        "subject": "Asunto único",
        "sender_name": None,
        "sender_entity": "",
    }

    with patch("app.services.ingestion.KnowledgeRepository") as MockRepo:
        mock_repo = _mock_repo(MockRepo, nivel_existente=None)
        await ingestion.ingest_radicado_created(conn, tenant_slug=TENANT, payload=payload)

    assert mock_repo.upsert_chunk.await_args.kwargs["texto"] == "Asunto único"


@pytest.mark.asyncio
async def test_ingest_missing_nivel_fails_closed_to_clasificada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nivel ausente en el payload → fail-closed a CLASIFICADA (3); con proveedor
    externo configurado, el guard is_embeddable lo bloquea → tombstone + SKIP."""
    monkeypatch.setattr(cfg_module.settings, "embedding_provider", "external")
    conn = AsyncMock()
    payload = {"tracking_number": TRACKING, "subject": "algo"}  # sin nivel_seguridad

    with (
        patch("app.services.ingestion.KnowledgeRepository") as MockRepo,
        patch("app.services.ingestion.audit") as mock_audit,
    ):
        mock_repo = _mock_repo(MockRepo, nivel_existente=None)
        mock_audit.append = AsyncMock()
        await ingestion.ingest_radicado_created(conn, tenant_slug=TENANT, payload=payload)

    mock_repo.upsert_chunk.assert_not_called()
    mock_repo.tombstone_by_source.assert_awaited_once_with("radicado", TRACKING, CLASIFICADA)
    mock_audit.append.assert_awaited_once()
    kw = mock_audit.append.call_args.kwargs
    assert kw["action"] == "knowledge.ingest_skipped_classified"
    assert kw["payload"]["nivel_seguridad"] == CLASIFICADA
    assert kw["payload"]["nivel_efectivo"] == CLASIFICADA
    assert kw["actor"] == "system"
    assert kw["object_ref"] == TRACKING


@pytest.mark.asyncio
async def test_ingest_reservada_con_proveedor_externo_hace_skip_y_tombstonea(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-02/RF-SEG-08: material Reservado con proveedor externo → SKIP (no se
    embebe, no se reenvía) pero SÍ se asegura el tombstone al nivel efectivo —
    la fila-centinela debe existir para el candado GREATEST (ver #1)."""
    monkeypatch.setattr(cfg_module.settings, "embedding_provider", "openai")
    conn = AsyncMock()
    payload = {
        "tracking_number": TRACKING,
        "nivel_seguridad": RESERVADA,
        "subject": "Información reservada",
    }

    with (
        patch("app.services.ingestion.KnowledgeRepository") as MockRepo,
        patch("app.services.ingestion.audit") as mock_audit,
    ):
        mock_repo = _mock_repo(MockRepo, nivel_existente=None)
        mock_audit.append = AsyncMock()
        await ingestion.ingest_radicado_created(conn, tenant_slug=TENANT, payload=payload)

    mock_repo.upsert_chunk.assert_not_called()
    mock_repo.tombstone_by_source.assert_awaited_once_with("radicado", TRACKING, RESERVADA)
    mock_audit.append.assert_awaited_once()
    assert mock_audit.append.call_args.kwargs["action"] == "knowledge.ingest_skipped_classified"


@pytest.mark.asyncio
async def test_ingest_tardio_respeta_nivel_existente_mas_alto_no_reenvia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1: un `created(1)` tardío que llega DESPUÉS de que el chunk ya fue
    tombstoneado a nivel 3 (reclassified previo) calcula nivel EFECTIVO=3, lo
    encuentra no-embebible con proveedor externo, y NO reenvía el texto ni baja
    el nivel — solo refresca/asegura el tombstone al efectivo."""
    monkeypatch.setattr(cfg_module.settings, "embedding_provider", "openai")
    conn = AsyncMock()
    payload = {
        "tracking_number": TRACKING,
        "nivel_seguridad": PUBLICA,  # evento viejo, nivel bajo
        "subject": "contenido que ya no debe salir",
    }

    with (
        patch("app.services.ingestion.KnowledgeRepository") as MockRepo,
        patch("app.services.ingestion.embed") as mock_embed,
        patch("app.services.ingestion.audit") as mock_audit,
    ):
        mock_repo = _mock_repo(MockRepo, nivel_existente=CLASIFICADA)
        mock_audit.append = AsyncMock()
        await ingestion.ingest_radicado_created(conn, tenant_slug=TENANT, payload=payload)

    mock_embed.assert_not_called()
    mock_repo.upsert_chunk.assert_not_called()
    mock_repo.tombstone_by_source.assert_awaited_once_with("radicado", TRACKING, CLASIFICADA)
    kw = mock_audit.append.call_args.kwargs
    assert kw["payload"]["nivel_seguridad"] == PUBLICA
    assert kw["payload"]["nivel_efectivo"] == CLASIFICADA


@pytest.mark.asyncio
async def test_ingest_tardio_con_proveedor_stub_sigue_embebiendo_al_efectivo() -> None:
    """Con proveedor local (stub), is_embeddable ignora el nivel: un `created`
    tardío tras un tombstone a nivel 3 sigue embebiendo, pero al nivel EFECTIVO
    (3) — nunca al nivel bajo del evento viejo."""
    conn = AsyncMock()
    payload = {
        "tracking_number": TRACKING,
        "nivel_seguridad": PUBLICA,
        "subject": "contenido",
    }

    with patch("app.services.ingestion.KnowledgeRepository") as MockRepo:
        mock_repo = _mock_repo(MockRepo, nivel_existente=RESERVADA)
        await ingestion.ingest_radicado_created(conn, tenant_slug=TENANT, payload=payload)

    mock_repo.upsert_chunk.assert_awaited_once()
    assert mock_repo.upsert_chunk.await_args.kwargs["nivel_seguridad"] == RESERVADA


@pytest.mark.asyncio
async def test_ingest_repository_failure_propagates() -> None:
    """Un fallo de BD/embed en el repositorio propaga — el worker NO ackea (PEL)."""
    conn = AsyncMock()
    payload = {"tracking_number": TRACKING, "nivel_seguridad": PUBLICA, "subject": "x"}

    with patch("app.services.ingestion.KnowledgeRepository") as MockRepo:
        mock_repo = _mock_repo(MockRepo, nivel_existente=None)
        mock_repo.upsert_chunk = AsyncMock(side_effect=OSError("db down"))
        with pytest.raises(OSError, match="db down"):
            await ingestion.ingest_radicado_created(conn, tenant_slug=TENANT, payload=payload)


# ---------------------------------------------------------------------------
# Residual "purga-en-anulación" (E21, hallazgo archival-compliance-auditor):
# created respeta voided incondicionalmente; annulled purga PERMANENTEMENTE.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_skips_totally_when_chunk_already_voided() -> None:
    """El chunk ya fue purgado por anulación (`voided=true`, `void_by_source`
    procesado antes, en cualquier orden). Este `created` — sea la ingesta
    original tardía o un redelivery at-least-once — hace SKIP TOTAL: no embebe,
    no des-voidea (no llama upsert_chunk NI tombstone_by_source), y deja traza."""
    conn = AsyncMock()
    payload = {
        "tracking_number": TRACKING,
        "nivel_seguridad": PUBLICA,
        "subject": "contenido que nunca debe reindexarse",
    }

    with (
        patch("app.services.ingestion.KnowledgeRepository") as MockRepo,
        patch("app.services.ingestion.embed") as mock_embed,
        patch("app.services.ingestion.audit") as mock_audit,
    ):
        mock_repo = _mock_repo(MockRepo, nivel_existente=CLASIFICADA, voided=True)
        mock_audit.append = AsyncMock()
        await ingestion.ingest_radicado_created(conn, tenant_slug=TENANT, payload=payload)

    mock_embed.assert_not_called()
    mock_repo.upsert_chunk.assert_not_called()
    mock_repo.tombstone_by_source.assert_not_called()
    mock_repo.void_by_source.assert_not_called()  # created nunca escribe voided
    mock_audit.append.assert_awaited_once()
    kw = mock_audit.append.call_args.kwargs
    assert kw["tenant_slug"] == TENANT
    assert kw["action"] == "knowledge.ingest_skipped_voided"
    assert kw["object_type"] == "radicado"
    assert kw["object_ref"] == TRACKING
    assert kw["actor"] == "system"
    assert kw["payload"]["motivo"] == "radicado_anulado_purga_permanente"


@pytest.mark.asyncio
async def test_ingest_skips_when_voided_regardless_of_evento_nivel() -> None:
    """El SKIP por voided es INCONDICIONAL — no depende del nivel del evento (ni
    siquiera un evento a nivel bajo con proveedor stub, que normalmente SÍ sería
    embebible, escapa del guard)."""
    conn = AsyncMock()
    payload = {"tracking_number": TRACKING, "nivel_seguridad": PUBLICA, "subject": "x"}

    with (
        patch("app.services.ingestion.KnowledgeRepository") as MockRepo,
        patch("app.services.ingestion.audit") as mock_audit,
    ):
        mock_repo = _mock_repo(MockRepo, nivel_existente=PUBLICA, voided=True)
        mock_audit.append = AsyncMock()
        await ingestion.ingest_radicado_created(conn, tenant_slug=TENANT, payload=payload)

    mock_repo.upsert_chunk.assert_not_called()


@pytest.mark.asyncio
async def test_void_radicado_purges_chunk_permanently() -> None:
    """`void_radicado` (handler de `document.radicado.annulled`) delega en
    `void_by_source` — purga PERMANENTE, sin cálculo de nivel EFECTIVO (el evento
    no transporta nivel_seguridad; el fail-safe vive en el repositorio)."""
    conn = AsyncMock()
    payload = {"id": "doc-id-1", "tracking_number": TRACKING}

    with patch("app.services.ingestion.KnowledgeRepository") as MockRepo:
        mock_repo = _mock_repo(MockRepo)
        await ingestion.void_radicado(conn, tenant_slug=TENANT, payload=payload)

    mock_repo.void_by_source.assert_awaited_once_with("radicado", TRACKING)


@pytest.mark.asyncio
async def test_reclassify_sube_a_reservada_con_proveedor_externo_tombstonea(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1: reclasificación a Reservado/Clasificado con proveedor externo →
    tombstone_by_source (NO delete_by_source — ver fix #1: un DELETE anularía el
    candado GREATEST que protege contra un `created` redelivered tarde)."""
    monkeypatch.setattr(cfg_module.settings, "embedding_provider", "openai")
    conn = AsyncMock()
    payload = {"tracking_number": TRACKING, "nivel_nuevo": RESERVADA, "nivel_anterior": PUBLICA}

    with patch("app.services.ingestion.KnowledgeRepository") as MockRepo:
        mock_repo = _mock_repo(MockRepo)
        await ingestion.apply_reclassification(conn, tenant_slug=TENANT, payload=payload)

    mock_repo.tombstone_by_source.assert_awaited_once_with("radicado", TRACKING, RESERVADA, occurred_at=None)
    mock_repo.update_nivel_by_source.assert_not_called()
    mock_repo.delete_by_source.assert_not_called()


@pytest.mark.asyncio
async def test_reclassify_a_publica_actualiza_nivel_exacto() -> None:
    """Baja a Pública (o proveedor stub): update_nivel_by_source exacto (sin GREATEST —
    una baja legítima explícita del evento reclassified, no la ruta de created)."""
    conn = AsyncMock()
    payload = {"tracking_number": TRACKING, "nivel_nuevo": PUBLICA, "nivel_anterior": RESERVADA}

    with patch("app.services.ingestion.KnowledgeRepository") as MockRepo:
        mock_repo = _mock_repo(MockRepo)
        await ingestion.apply_reclassification(conn, tenant_slug=TENANT, payload=payload)

    mock_repo.update_nivel_by_source.assert_awaited_once_with("radicado", TRACKING, PUBLICA, occurred_at=None)
    mock_repo.tombstone_by_source.assert_not_called()
    mock_repo.delete_by_source.assert_not_called()


@pytest.mark.asyncio
async def test_reclassify_sube_a_reservada_con_proveedor_stub_actualiza_no_tombstonea() -> None:
    """Con proveedor local (stub) subir a Reservado NO tombstonea: se actualiza el nivel."""
    conn = AsyncMock()
    payload = {"tracking_number": TRACKING, "nivel_nuevo": RESERVADA, "nivel_anterior": PUBLICA}

    with patch("app.services.ingestion.KnowledgeRepository") as MockRepo:
        mock_repo = _mock_repo(MockRepo)
        await ingestion.apply_reclassification(conn, tenant_slug=TENANT, payload=payload)

    mock_repo.update_nivel_by_source.assert_awaited_once_with("radicado", TRACKING, RESERVADA, occurred_at=None)
    mock_repo.tombstone_by_source.assert_not_called()
    mock_repo.delete_by_source.assert_not_called()


@pytest.mark.asyncio
async def test_reclassify_sin_nivel_nuevo_falla_cerrado_a_clasificada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#3: `nivel_nuevo` ausente en el payload → fail-CLOSE a CLASIFICADA (3), NUNCA
    conserva el nivel viejo. Con proveedor externo, eso implica tombstone (no
    update exacto al nivel ausente). El fail-close también deja un asiento
    best-effort en audit_log (#Media)."""
    monkeypatch.setattr(cfg_module.settings, "embedding_provider", "openai")
    conn = AsyncMock()
    payload = {"tracking_number": TRACKING, "nivel_anterior": PUBLICA}  # sin nivel_nuevo

    with (
        patch("app.services.ingestion.KnowledgeRepository") as MockRepo,
        patch("app.services.ingestion.audit") as mock_audit,
    ):
        mock_repo = _mock_repo(MockRepo)
        mock_audit.append = AsyncMock()
        await ingestion.apply_reclassification(conn, tenant_slug=TENANT, payload=payload)

    mock_repo.tombstone_by_source.assert_awaited_once_with("radicado", TRACKING, CLASIFICADA, occurred_at=None)
    mock_repo.update_nivel_by_source.assert_not_called()
    mock_audit.append.assert_awaited_once()
    kw = mock_audit.append.call_args.kwargs
    assert kw["tenant_slug"] == TENANT
    assert kw["action"] == "knowledge.reclassify_malformed_failclose"
    assert kw["object_type"] == "radicado"
    assert kw["object_ref"] == TRACKING
    assert kw["actor"] == "system"
    assert kw["payload"]["nivel_aplicado"] == CLASIFICADA


@pytest.mark.asyncio
async def test_reclassify_sin_nivel_nuevo_con_proveedor_stub_actualiza_a_clasificada() -> None:
    """#3 con proveedor stub: fail-close a CLASIFICADA también pasa por la rama
    update_nivel_by_source exacto (stub nunca tombstonea)."""
    conn = AsyncMock()
    payload = {"tracking_number": TRACKING, "nivel_anterior": PUBLICA}  # sin nivel_nuevo

    with (
        patch("app.services.ingestion.KnowledgeRepository") as MockRepo,
        patch("app.services.ingestion.audit") as mock_audit,
    ):
        mock_repo = _mock_repo(MockRepo)
        mock_audit.append = AsyncMock()
        await ingestion.apply_reclassification(conn, tenant_slug=TENANT, payload=payload)

    mock_repo.update_nivel_by_source.assert_awaited_once_with("radicado", TRACKING, CLASIFICADA, occurred_at=None)
    mock_repo.tombstone_by_source.assert_not_called()
    mock_audit.append.assert_awaited_once()
    assert mock_audit.append.call_args.kwargs["action"] == "knowledge.reclassify_malformed_failclose"
