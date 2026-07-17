# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Firma XAdES del acta de transferencia (H-H, E06/F4): backoff de
`TransferenciaRepository` (migración 026), sellado síncrono acotado
post-commit (`TransferenciaService._sellar_acta_post_commit`), lectura
enriquecida (`GET /acta`), reintento manual
(`POST /acta/firmar`) y la segunda fase del job de reconciliación
(`app/jobs/indice_reconciliation.py::run_once_actas`).

Todo pure-unit (conexiones/pool mockeados) — la atomicidad/idempotencia REAL
se prueba aparte en `tests/integration/` (Postgres real).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.signature_client import SignedIndice, SignOutcome, VerifyResult
from app.core.actor import Actor
from app.core.config import settings
from app.core.constants import SYSTEM_RECONCILER_ID
from app.repositories.transferencia_repository import TransferenciaRepository
from app.services.transferencia_service import TransferenciaService
from tests.conftest import TENANT_HEADERS, USER_ID

TENANT = "demo"


def _mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _signed(*, xades_level: str = "XAdES-T", acreditado: bool = False) -> SignedIndice:
    return SignedIndice(
        firma_id=uuid.uuid4(),
        minio_key_firmado="file-id/acta-transferencia.xml",
        nivel_conformidad="xades_b_no_acreditado",
        xades_level=xades_level,
        tipo_firma="sello_sistema",
        acreditado=acreditado,
        certificado_fingerprint="ab:cd",
    )


def _acta_firma_row(**kw) -> dict:
    base = {
        "transferencia_id": uuid.uuid4(),
        "estado": "pendiente_firma",
        "firma_id": None,
        "minio_key_firmado": None,
        "xades_level": None,
        "acreditado": False,
        "last_error": None,
        "reconcile_intentos": 0,
        "reconcile_proximo_at": None,
        "reconcile_agotado": False,
        "created_at": datetime.now(tz=UTC),
        "firmado_at": None,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# TransferenciaRepository — nuevos métodos (migración 026)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_acta_firma_pendiente_inserts_row() -> None:
    conn = _mock_conn()
    repo = TransferenciaRepository(conn, TENANT)
    tid = uuid.uuid4()

    await repo.insert_acta_firma_pendiente(tid)

    sql, *params = conn.execute.call_args[0]
    assert "INSERT INTO transferencia_acta_firma" in sql
    assert params == [tid]


@pytest.mark.asyncio
async def test_mark_acta_firmada_resets_backoff_and_guards_estado() -> None:
    conn = _mock_conn()
    conn.execute.return_value = "UPDATE 1"
    repo = TransferenciaRepository(conn, TENANT)
    tid = uuid.uuid4()
    firma_id = uuid.uuid4()

    affected = await repo.mark_acta_firmada(
        tid, firma_id=firma_id, minio_key="k/acta.xml", xades_level="XAdES-T", acreditado=False
    )

    assert affected == 1
    sql, *params = conn.execute.call_args[0]
    assert "estado='firmado'" in sql
    assert "reconcile_intentos=0" in sql
    assert "reconcile_agotado=false" in sql
    assert "estado <> 'firmado'" in sql
    assert params == [tid, firma_id, "k/acta.xml", "XAdES-T", False]


@pytest.mark.asyncio
async def test_mark_acta_firmada_lost_race_returns_zero() -> None:
    conn = _mock_conn()
    conn.execute.return_value = "UPDATE 0"
    repo = TransferenciaRepository(conn, TENANT)

    affected = await repo.mark_acta_firmada(
        uuid.uuid4(), firma_id=uuid.uuid4(), minio_key=None, xades_level=None, acreditado=False
    )

    assert affected == 0


@pytest.mark.asyncio
async def test_mark_acta_firma_pendiente_rearms_backoff() -> None:
    conn = _mock_conn()
    repo = TransferenciaRepository(conn, TENANT)
    tid = uuid.uuid4()

    await repo.mark_acta_firma_pendiente(tid, last_error="sello_ausente")

    sql, *params = conn.execute.call_args[0]
    assert "reconcile_agotado=false" in sql
    assert "reconcile_intentos=0" in sql
    assert "now()" in sql
    assert "estado <> 'firmado'" in sql
    assert params == [tid, "sello_ausente"]


@pytest.mark.asyncio
async def test_bump_acta_firma_intento_increments_atomically() -> None:
    conn = _mock_conn()
    repo = TransferenciaRepository(conn, TENANT)
    tid = uuid.uuid4()
    proximo = datetime.now(tz=UTC)

    await repo.bump_acta_firma_intento(tid, proximo_at=proximo, last_error="transient", agotado=False)

    sql, *params = conn.execute.call_args[0]
    assert "reconcile_intentos = reconcile_intentos + 1" in sql
    assert params == [tid, proximo, "transient", False]


@pytest.mark.asyncio
async def test_list_acta_firmas_pendientes_filters_estado_and_backoff() -> None:
    conn = _mock_conn()
    conn.fetch.return_value = [{"transferencia_id": uuid.uuid4()}]
    repo = TransferenciaRepository(conn, TENANT)

    rows = await repo.list_acta_firmas_pendientes(50)

    assert len(rows) == 1
    sql, limit = conn.fetch.call_args[0]
    assert "estado = 'pendiente_firma'" in sql
    assert "reconcile_agotado = false" in sql
    assert "reconcile_proximo_at IS NULL OR taf.reconcile_proximo_at <= now()" in sql
    assert "JOIN transferencia_acta" in sql
    assert limit == 50


# ---------------------------------------------------------------------------
# TransferenciaService._sellar_acta_post_commit — PARTE 3
# ---------------------------------------------------------------------------


def _service(conn: AsyncMock) -> TransferenciaService:
    return TransferenciaService(conn, TENANT)


@pytest.mark.asyncio
async def test_sellar_acta_ok_first_attempt_marks_firmado_and_audits_manual() -> None:
    conn = _mock_conn()
    conn.execute.return_value = "UPDATE 1"
    svc = _service(conn)
    signed = _signed()
    tid = uuid.uuid4()
    actor = Actor(id=USER_ID)

    with patch(
        "app.clients.signature_client.sign_xml_outcome",
        new=AsyncMock(return_value=SignOutcome(signed=signed)),
    ) as mock_outcome:
        await svc._sellar_acta_post_commit(tid, acta_xml="<acta/>", indice_version=1, actor=actor)

    mock_outcome.assert_awaited_once()
    call_kwargs = mock_outcome.await_args.kwargs
    assert call_kwargs["objeto_tipo"] == "acta_transferencia"
    assert call_kwargs["indice_id"] is None
    assert call_kwargs["objeto_id"] == str(tid)
    assert any("estado='firmado'" in str(c.args[0]) for c in conn.execute.call_args_list)

    import json

    audit_calls = [
        c.args
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0][5] == "archive.acta_firmada"
    payload = json.loads(audit_calls[0][8])
    assert payload["origen"] == "manual"


@pytest.mark.asyncio
async def test_sellar_acta_lost_race_no_phantom_audit() -> None:
    conn = _mock_conn()
    conn.execute.return_value = "UPDATE 0"  # perdió la carrera
    svc = _service(conn)
    actor = Actor(id=USER_ID)

    with patch(
        "app.clients.signature_client.sign_xml_outcome",
        new=AsyncMock(return_value=SignOutcome(signed=_signed())),
    ):
        await svc._sellar_acta_post_commit(
            uuid.uuid4(), acta_xml="<acta/>", indice_version=1, actor=actor
        )

    audit_calls = [
        c
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert audit_calls == []


@pytest.mark.asyncio
async def test_sellar_acta_seal_absent_degrades_without_retry(monkeypatch) -> None:
    monkeypatch.setattr(settings, "acta_sign_max_sync_attempts", 3)
    monkeypatch.setattr(settings, "acta_sign_sync_delay_seconds", 0)
    conn = _mock_conn()
    svc = _service(conn)
    actor = Actor(id=USER_ID)

    with patch(
        "app.clients.signature_client.sign_xml_outcome",
        new=AsyncMock(return_value=SignOutcome(signed=None, seal_absent=True)),
    ) as mock_outcome:
        await svc._sellar_acta_post_commit(
            uuid.uuid4(), acta_xml="<acta/>", indice_version=1, actor=actor
        )

    mock_outcome.assert_awaited_once()  # un solo intento
    pendiente_calls = [
        c for c in conn.execute.call_args_list if "estado='pendiente_firma'" in str(c.args[0])
    ]
    assert len(pendiente_calls) == 1
    assert pendiente_calls[0].args[2] == "sello_ausente"


@pytest.mark.asyncio
async def test_sellar_acta_transient_then_success_second_attempt(monkeypatch) -> None:
    monkeypatch.setattr(settings, "acta_sign_max_sync_attempts", 2)
    monkeypatch.setattr(settings, "acta_sign_sync_delay_seconds", 0)
    conn = _mock_conn()
    conn.execute.return_value = "UPDATE 1"
    svc = _service(conn)
    actor = Actor(id=USER_ID)
    outcomes = [SignOutcome(signed=None, transient=True), SignOutcome(signed=_signed())]

    with patch(
        "app.clients.signature_client.sign_xml_outcome", new=AsyncMock(side_effect=outcomes)
    ) as mock_outcome:
        await svc._sellar_acta_post_commit(
            uuid.uuid4(), acta_xml="<acta/>", indice_version=1, actor=actor
        )

    assert mock_outcome.await_count == 2
    assert any("estado='firmado'" in str(c.args[0]) for c in conn.execute.call_args_list)


@pytest.mark.asyncio
async def test_sellar_acta_transient_exhausted_degrades(monkeypatch) -> None:
    monkeypatch.setattr(settings, "acta_sign_max_sync_attempts", 2)
    monkeypatch.setattr(settings, "acta_sign_sync_delay_seconds", 0)
    conn = _mock_conn()
    svc = _service(conn)
    actor = Actor(id=USER_ID)

    with patch(
        "app.clients.signature_client.sign_xml_outcome",
        new=AsyncMock(return_value=SignOutcome(signed=None, transient=True)),
    ) as mock_outcome:
        await svc._sellar_acta_post_commit(
            uuid.uuid4(), acta_xml="<acta/>", indice_version=1, actor=actor
        )

    assert mock_outcome.await_count == 2
    pendiente_calls = [
        c for c in conn.execute.call_args_list if "estado='pendiente_firma'" in str(c.args[0])
    ]
    assert pendiente_calls[0].args[2] == "sellado_xades_no_obtenido"


# ---------------------------------------------------------------------------
# TransferenciaService.acta() — bloque `firma` (PARTE 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acta_firma_block_pendiente_when_no_firma_row() -> None:
    conn = _mock_conn()
    svc = _service(conn)
    tid = uuid.uuid4()

    info = await svc._build_firma_info(None, verify=False)

    assert info.presente is False
    assert info.valida is None
    assert info.motivo is None


@pytest.mark.asyncio
async def test_acta_firma_block_presente_without_verify_leaves_valida_none() -> None:
    conn = _mock_conn()
    svc = _service(conn)
    row = _acta_firma_row(estado="firmado", xades_level="XAdES-T", acreditado=False)

    info = await svc._build_firma_info(row, verify=False)

    assert info.presente is True
    assert info.xades_level == "XAdES-T"
    assert info.valida is None  # no re-verifica salvo ?verify=true


@pytest.mark.asyncio
async def test_acta_firma_block_verify_true_downloads_and_verifies() -> None:
    conn = _mock_conn()
    svc = _service(conn)
    firma_id = uuid.uuid4()
    row = _acta_firma_row(
        estado="firmado", firma_id=firma_id, minio_key_firmado="k/acta.xml", xades_level="XAdES-T"
    )

    with (
        patch(
            "app.clients.storage_client.download_signed_xml",
            new=AsyncMock(return_value="<acta><ds:Signature/></acta>"),
        ) as mock_download,
        patch(
            "app.clients.signature_client.verify",
            new=AsyncMock(
                return_value=VerifyResult(
                    valida=True, nivel_conformidad="xades_b_no_acreditado", acreditado=False, motivo=None
                )
            ),
        ) as mock_verify,
    ):
        info = await svc._build_firma_info(row, verify=True)

    mock_download.assert_awaited_once()
    mock_verify.assert_awaited_once()
    assert info.valida is True


@pytest.mark.asyncio
async def test_acta_firma_block_verify_true_signed_xml_unavailable() -> None:
    conn = _mock_conn()
    svc = _service(conn)
    row = _acta_firma_row(
        estado="firmado", firma_id=uuid.uuid4(), minio_key_firmado="k/acta.xml"
    )

    with patch(
        "app.clients.storage_client.download_signed_xml", new=AsyncMock(return_value=None)
    ):
        info = await svc._build_firma_info(row, verify=True)

    assert info.valida is False
    assert info.motivo == "signed_xml_unavailable"


# ---------------------------------------------------------------------------
# GET /transferencias/{id}/acta — router-level, bloque `firma`
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_acta_response_includes_firma_block(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    from tests.test_transferencias import _acta_row, _transfer_row
    from tests.conftest import make_expediente_record

    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida"),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        _acta_firma_row(transferencia_id=tid, estado="firmado", xades_level="XAdES-T"),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.get(f"/api/v1/transferencias/{tid}/acta", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    firma = resp.json()["firma"]
    assert firma["presente"] is True
    assert firma["xades_level"] == "XAdES-T"
    assert firma["valida"] is None


# ---------------------------------------------------------------------------
# firmar_acta_retry — reintento manual (PARTE 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_firmar_acta_retry_transferencia_not_found() -> None:
    conn = _mock_conn()
    svc = _service(conn)
    conn.fetchrow.return_value = None

    with pytest.raises(Exception) as exc_info:
        await svc.firmar_acta_retry(uuid.uuid4(), Actor(id=USER_ID))
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"] == "transferencia_not_found"


@pytest.mark.asyncio
async def test_firmar_acta_retry_acta_not_found() -> None:
    conn = _mock_conn()
    svc = _service(conn)
    tid = uuid.uuid4()
    conn.fetchrow.side_effect = [
        {"id": tid, "estado": "recibida"},  # repo.get
        None,  # repo.get_acta -> aún no congelada
    ]

    with pytest.raises(Exception) as exc_info:
        await svc.firmar_acta_retry(tid, Actor(id=USER_ID))
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"] == "acta_not_found"


@pytest.mark.asyncio
async def test_firmar_acta_retry_already_signed_returns_409() -> None:
    conn = _mock_conn()
    svc = _service(conn)
    tid = uuid.uuid4()
    conn.fetchrow.side_effect = [
        {"id": tid, "estado": "recibida"},
        {"transferencia_id": tid, "acta_fuid_xml": "<acta/>", "indice_version": 1},
        _acta_firma_row(transferencia_id=tid, estado="firmado"),
    ]

    with pytest.raises(Exception) as exc_info:
        await svc.firmar_acta_retry(tid, Actor(id=USER_ID))
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "acta_ya_firmada"


@pytest.mark.asyncio
async def test_firmar_acta_retry_success_returns_updated_acta() -> None:
    conn = _mock_conn()
    svc = _service(conn)
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    from tests.test_transferencias import _acta_row, _transfer_row
    from tests.conftest import make_expediente_record

    conn.fetchrow.side_effect = [
        {"id": tid, "estado": "recibida", "expediente_id": exp_id},  # repo.get
        {"transferencia_id": tid, "acta_fuid_xml": "<acta/>", "indice_version": 1},  # get_acta
        _acta_firma_row(transferencia_id=tid, estado="pendiente_firma"),  # get_acta_firma (guard)
        # post-attempt re-check -> confirma el sellado:
        _acta_firma_row(transferencia_id=tid, estado="firmado", xades_level="XAdES-T"),
        # acta() call at the end:
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida"),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        _acta_firma_row(transferencia_id=tid, estado="firmado", xades_level="XAdES-T"),
    ]
    conn.fetch.return_value = []

    with patch.object(
        TransferenciaService, "_sellar_acta_post_commit", new=AsyncMock(return_value=None)
    ) as mock_sellar:
        result = await svc.firmar_acta_retry(tid, Actor(id=USER_ID))

    mock_sellar.assert_awaited_once()
    assert result.firma.presente is True


@pytest.mark.asyncio
async def test_firmar_acta_retry_still_failing_returns_503() -> None:
    conn = _mock_conn()
    svc = _service(conn)
    tid = uuid.uuid4()
    conn.fetchrow.side_effect = [
        {"id": tid, "estado": "recibida"},
        {"transferencia_id": tid, "acta_fuid_xml": "<acta/>", "indice_version": 1},
        _acta_firma_row(transferencia_id=tid, estado="pendiente_firma"),  # guard
        _acta_firma_row(transferencia_id=tid, estado="pendiente_firma"),  # post-attempt re-read
    ]

    with patch.object(
        TransferenciaService, "_sellar_acta_post_commit", new=AsyncMock(return_value=None)
    ):
        with pytest.raises(Exception) as exc_info:
            await svc.firmar_acta_retry(tid, Actor(id=USER_ID))
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "acta_signing_failed"


# ---------------------------------------------------------------------------
# Job — segunda fase, app/jobs/indice_reconciliation.py::run_once_actas
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciliar_acta_success_marks_signed_and_audits_reconciliation() -> None:
    from app.jobs.indice_reconciliation import _reconciliar_acta

    conn = _mock_conn()
    conn.execute.return_value = "UPDATE 1"
    signed = _signed()
    row = {
        "transferencia_id": uuid.uuid4(),
        "acta_fuid_xml": "<acta/>",
        "indice_version": 1,
        "reconcile_intentos": 0,
    }

    with patch(
        "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
        new=AsyncMock(return_value=SignOutcome(signed=signed)),
    ) as mock_sign:
        resultado = await _reconciliar_acta(conn, TENANT, row)

    assert resultado == "sellado"
    call_kwargs = mock_sign.await_args.kwargs
    assert call_kwargs["objeto_tipo"] == "acta_transferencia"
    assert call_kwargs["indice_id"] is None

    import json

    audit_calls = [
        c.args
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0][3] == str(SYSTEM_RECONCILER_ID)
    assert audit_calls[0][5] == "archive.acta_firmada"
    payload = json.loads(audit_calls[0][8])
    assert payload["origen"] == "reconciliation"


@pytest.mark.asyncio
async def test_reconciliar_acta_lost_race_no_phantom_audit() -> None:
    from app.jobs.indice_reconciliation import _reconciliar_acta

    conn = _mock_conn()
    conn.execute.return_value = "UPDATE 0"
    row = {
        "transferencia_id": uuid.uuid4(),
        "acta_fuid_xml": "<acta/>",
        "indice_version": 1,
        "reconcile_intentos": 0,
    }

    with patch(
        "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
        new=AsyncMock(return_value=SignOutcome(signed=_signed())),
    ):
        resultado = await _reconciliar_acta(conn, TENANT, row)

    assert resultado == "sellado"
    audit_calls = [
        c
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert audit_calls == []


@pytest.mark.asyncio
async def test_reconciliar_acta_failure_schedules_backoff(monkeypatch) -> None:
    from app.jobs.indice_reconciliation import _reconciliar_acta

    monkeypatch.setattr(settings, "indice_reconciliation_max_intentos", 8)
    conn = _mock_conn()
    row = {
        "transferencia_id": uuid.uuid4(),
        "acta_fuid_xml": "<acta/>",
        "indice_version": 1,
        "reconcile_intentos": 2,
    }

    with patch(
        "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
        new=AsyncMock(return_value=SignOutcome(signed=None, transient=True)),
    ):
        resultado = await _reconciliar_acta(conn, TENANT, row)

    assert resultado == "fallido"
    bump_calls = [
        c
        for c in conn.execute.call_args_list
        if "reconcile_intentos = reconcile_intentos + 1" in str(c.args[0])
    ]
    assert len(bump_calls) == 1
    _, tid, proximo_at, last_error, agotado = bump_calls[0].args
    assert proximo_at is not None
    assert last_error == "sellado_xades_no_obtenido"
    assert agotado is False


@pytest.mark.asyncio
async def test_reconciliar_acta_reaches_max_intentos_marks_agotado(monkeypatch) -> None:
    from app.jobs.indice_reconciliation import _reconciliar_acta

    monkeypatch.setattr(settings, "indice_reconciliation_max_intentos", 3)
    conn = _mock_conn()
    row = {
        "transferencia_id": uuid.uuid4(),
        "acta_fuid_xml": "<acta/>",
        "indice_version": 1,
        "reconcile_intentos": 2,
    }

    with patch(
        "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
        new=AsyncMock(return_value=SignOutcome(signed=None, seal_absent=True)),
    ):
        resultado = await _reconciliar_acta(conn, TENANT, row)

    assert resultado == "agotado"
    audit_actions = [
        c.args[5]
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert "archive.acta_reconcile_agotado" in audit_actions


@pytest.mark.asyncio
async def test_run_once_actas_uses_own_advisory_lock_namespace() -> None:
    """El namespace del advisory lock del acta es DISTINTO del índice (no
    colisiona si ambos loops corrieran en el mismo instante)."""
    from app.jobs import indice_reconciliation

    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True
    conn_a.fetch.return_value = []

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
    ):
        summary = await indice_reconciliation.run_once_actas()

    assert summary["pendientes_vistos"] == 0
    lock_call = conn_a.fetchval.call_args[0]
    assert "orpycamcp:archive:acta_reconcile" in lock_call[0]


@pytest.mark.asyncio
async def test_run_once_actas_aggregates_and_isolates_per_acta_failure() -> None:
    from app.jobs import indice_reconciliation

    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True
    row_boom = {
        "transferencia_id": uuid.uuid4(),
        "acta_fuid_xml": "<acta/>",
        "indice_version": 1,
        "reconcile_intentos": 0,
    }
    row_ok = {
        "transferencia_id": uuid.uuid4(),
        "acta_fuid_xml": "<acta/>",
        "indice_version": 1,
        "reconcile_intentos": 0,
    }
    conn_a.fetch.return_value = [row_boom, row_ok]

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    async def _sign_side_effect(*, objeto_id, **kwargs):
        if objeto_id == str(row_boom["transferencia_id"]):
            raise RuntimeError("boom (itest)")
        return SignOutcome(signed=_signed())

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
        patch(
            "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
            new=AsyncMock(side_effect=_sign_side_effect),
        ),
    ):
        summary = await indice_reconciliation.run_once_actas()

    assert summary["pendientes_vistos"] == 2
    assert summary["sellados"] == 1
    assert summary["fallidos"] == 1
