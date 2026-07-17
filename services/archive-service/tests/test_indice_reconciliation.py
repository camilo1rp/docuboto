# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Reconciliación del sellado del índice electrónico (residual Ac. AGN
001/2024 art. 4.3.2.4): backoff de `IndexRepository`, control compensatorio
`IndexService.firmar_indice_close`, listado operacional
`GET /expedientes/indices/pendientes-firma` (no-read-up) y el job
`app/jobs/indice_reconciliation.py`.

Todo pure-unit (conexiones/pool mockeados) — la atomicidad/idempotencia REAL
se prueba aparte en `tests/integration/` (Postgres real).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.signature_client import SignedIndice, SignOutcome
from app.core.config import settings
from app.core.constants import SYSTEM_RECONCILER_ID
from app.repositories.index_repository import IndexRepository
from app.services.index_service import IndexService
from tests.conftest import TENANT_HEADERS

TENANT = "demo"


def _mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _signed() -> SignedIndice:
    return SignedIndice(
        firma_id=uuid.uuid4(),
        minio_key_firmado="file-id/indice.xml",
        nivel_conformidad="xades_b_no_acreditado",
        xades_level="XAdES-T",
        tipo_firma="sello_sistema",
        acreditado=False,
        certificado_fingerprint="ab:cd",
    )


# ---------------------------------------------------------------------------
# IndexRepository — nuevos métodos (migración 025)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_pendiente_sets_motivo_and_backoff_columns() -> None:
    conn = _mock_conn()
    repo = IndexRepository(conn)
    indice_id = uuid.uuid4()

    await repo.mark_pendiente(indice_id, motivo="sello_ausente")

    sql, *params = conn.execute.call_args[0]
    assert "reconcile_ultimo_error" in sql
    assert "reconcile_proximo_at" in sql
    assert "now()" in sql
    assert "estado <> 'firmado'" in sql
    assert params == [indice_id, "sello_ausente"]


@pytest.mark.asyncio
async def test_mark_pendiente_rearms_agotado_backoff() -> None:
    """C1: toda degradación a 'pendiente_firma' vía `IndexRepository.
    mark_pendiente` (invocada SOLO por rutas humano-adyacentes -- reintento
    manual y cierre, nunca por el job, que degrada vía `bump_intento`) debe
    re-armar `reconcile_agotado`/`reconcile_intentos` -- si no, un índice que
    había quedado `agotado=TRUE` y cuyo reintento manual vuelve a fallar
    queda invisible tanto para el job (excluido por `reconcile_agotado`) como
    aparentemente para el operador, hasta un reintento manual con ÉXITO."""
    conn = _mock_conn()
    repo = IndexRepository(conn)
    indice_id = uuid.uuid4()

    await repo.mark_pendiente(indice_id, motivo="sellado_xades_no_obtenido")

    sql, *_params = conn.execute.call_args[0]
    assert "reconcile_agotado=FALSE" in sql
    assert "reconcile_intentos=0" in sql


@pytest.mark.asyncio
async def test_mark_signed_resets_backoff_columns() -> None:
    conn = _mock_conn()
    repo = IndexRepository(conn)
    indice_id = uuid.uuid4()
    firma_id = uuid.uuid4()

    await repo.mark_signed(indice_id, firma_id, "minio/key.xml")

    sql, *params = conn.execute.call_args[0]
    assert "reconcile_intentos=0" in sql
    assert "reconcile_agotado=FALSE" in sql
    assert "reconcile_proximo_at=NULL" in sql
    assert "reconcile_ultimo_error=NULL" in sql
    assert params == [indice_id, firma_id, "minio/key.xml"]


@pytest.mark.asyncio
async def test_list_pending_signature_filters_estado_status_and_backoff() -> None:
    conn = _mock_conn()
    conn.fetch.return_value = [{"id": uuid.uuid4()}]
    repo = IndexRepository(conn)

    rows = await repo.list_pending_signature(50)

    assert len(rows) == 1
    sql, limit = conn.fetch.call_args[0]
    assert "estado = 'pendiente_firma'" in sql
    assert "status IN ('closed', 'transferred')" in sql
    assert "reconcile_agotado = FALSE" in sql
    assert "reconcile_proximo_at IS NULL OR vigente.reconcile_proximo_at <= now()" in sql
    assert "DISTINCT ON (expediente_id)" in sql
    assert limit == 50


@pytest.mark.asyncio
async def test_bump_intento_increments_atomically_and_sets_fields() -> None:
    conn = _mock_conn()
    repo = IndexRepository(conn)
    indice_id = uuid.uuid4()
    from datetime import UTC, datetime

    proximo = datetime.now(tz=UTC)

    await repo.bump_intento(indice_id, proximo_at=proximo, ultimo_error="transient", agotado=False)

    sql, *params = conn.execute.call_args[0]
    assert "reconcile_intentos = reconcile_intentos + 1" in sql
    assert params == [indice_id, proximo, "transient", False]


@pytest.mark.asyncio
async def test_bump_intento_agotado_accepts_none_proximo_at() -> None:
    conn = _mock_conn()
    repo = IndexRepository(conn)
    indice_id = uuid.uuid4()

    await repo.bump_intento(indice_id, proximo_at=None, ultimo_error="sellado_xades_no_obtenido", agotado=True)

    _, *params = conn.execute.call_args[0]
    assert params == [indice_id, None, "sellado_xades_no_obtenido", True]


@pytest.mark.asyncio
async def test_list_pending_signature_page_filters_by_clearance() -> None:
    conn = _mock_conn()
    conn.fetchval.return_value = 3
    conn.fetch.return_value = [
        {
            "indice_id": uuid.uuid4(),
            "expediente_id": uuid.uuid4(),
            "version": 1,
            "reconcile_intentos": 2,
            "reconcile_agotado": False,
            "reconcile_ultimo_error": "sellado_xades_no_obtenido",
            "expediente_code": "EXP-2026-0001",
        }
    ]
    repo = IndexRepository(conn)

    rows, total = await repo.list_pending_signature_page(max_clearance=1, offset=0, limit=20)

    assert total == 3
    assert len(rows) == 1
    count_sql, count_clearance = conn.fetchval.call_args[0]
    assert "nivel_seguridad <= $1" in count_sql
    assert count_clearance == 1
    page_sql, page_clearance, limit, offset = conn.fetch.call_args[0]
    assert "nivel_seguridad <= $1" in page_sql
    assert (page_clearance, limit, offset) == (1, 20, 0)


# ---------------------------------------------------------------------------
# IndexService.firmar_indice_close — control compensatorio (PARTE 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_firmar_indice_close_ok_first_attempt_marks_firmado() -> None:
    conn = _mock_conn()
    conn.fetchrow.return_value = {"id": uuid.uuid4(), "estado": "vigente", "xml": "<indice/>", "version": 1}
    # S1: `mark_signed` reporta `affected=1` (ganó la carrera) -- el default
    # del mock (`execute.return_value = None`) simularía una carrera perdida.
    conn.execute.return_value = "UPDATE 1"
    svc = IndexService(conn, TENANT)
    signed = _signed()
    exp_id = uuid.uuid4()

    with patch(
        "app.clients.signature_client.sign_xml_outcome",
        new=AsyncMock(return_value=SignOutcome(signed=signed)),
    ) as mock_outcome:
        result = await svc.firmar_indice_close(exp_id, tenant_slug=TENANT, actor_id=str(uuid.uuid4()))

    assert result is signed
    mock_outcome.assert_awaited_once()
    assert any("estado='firmado'" in str(c.args[0]) for c in conn.execute.call_args_list)
    # C3: el asiento del CIERRE distingue su origen de manual/reconciliation.
    import json

    audit_calls = [
        c.args
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    payload = json.loads(audit_calls[0][8])
    assert payload["origen"] == "close"


@pytest.mark.asyncio
async def test_firmar_indice_close_lost_race_returns_none_no_phantom_audit() -> None:
    """S1 (RF-FIR-14): si `mark_signed` casa 0 filas (otro proceso -- p. ej.
    el job de reconciliación -- ya firmó esta misma fila entre la lectura y
    el UPDATE), el sello recién obtenido queda huérfano: NO se escribe el
    asiento `archive.indice_firmado` fantasma y el cierre NUNCA se bloquea
    (best-effort) -- se degrada a `None`, igual que cualquier otra rama de
    `firmar_indice_close`."""
    conn = _mock_conn()
    conn.fetchrow.return_value = {"id": uuid.uuid4(), "estado": "vigente", "xml": "<indice/>", "version": 1}
    conn.execute.return_value = "UPDATE 0"  # perdió la carrera
    svc = IndexService(conn, TENANT)
    signed = _signed()

    with patch(
        "app.clients.signature_client.sign_xml_outcome",
        new=AsyncMock(return_value=SignOutcome(signed=signed)),
    ) as mock_outcome:
        result = await svc.firmar_indice_close(uuid.uuid4(), tenant_slug=TENANT, actor_id=str(uuid.uuid4()))

    assert result is None
    mock_outcome.assert_awaited_once()
    audit_calls = [
        c
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert audit_calls == []


@pytest.mark.asyncio
async def test_firmar_indice_close_seal_absent_degrades_without_retry(monkeypatch) -> None:
    """`seal_absent` degrada de INMEDIATO — un único intento, sin sleep ni
    reintentos, aunque `close_sign_max_sync_attempts` permita más de uno."""
    monkeypatch.setattr(settings, "close_sign_max_sync_attempts", 3)
    monkeypatch.setattr(settings, "close_sign_sync_delay_seconds", 0)
    conn = _mock_conn()
    conn.fetchrow.return_value = {"id": uuid.uuid4(), "estado": "vigente", "xml": "<indice/>", "version": 1}
    svc = IndexService(conn, TENANT)

    with patch(
        "app.clients.signature_client.sign_xml_outcome",
        new=AsyncMock(return_value=SignOutcome(signed=None, seal_absent=True)),
    ) as mock_outcome:
        result = await svc.firmar_indice_close(uuid.uuid4(), tenant_slug=TENANT, actor_id=str(uuid.uuid4()))

    assert result is None
    mock_outcome.assert_awaited_once()  # UN solo intento — seal_absent no reintenta
    assert any("estado='pendiente_firma'" in str(c.args[0]) for c in conn.execute.call_args_list)
    # Motivo distintivo (distinto de 'sellado_xades_no_obtenido').
    pendiente_calls = [
        c for c in conn.execute.call_args_list if "estado='pendiente_firma'" in str(c.args[0])
    ]
    assert pendiente_calls[0].args[2] == "sello_ausente"


@pytest.mark.asyncio
async def test_firmar_indice_close_transient_then_success_second_attempt(monkeypatch) -> None:
    monkeypatch.setattr(settings, "close_sign_max_sync_attempts", 2)
    monkeypatch.setattr(settings, "close_sign_sync_delay_seconds", 0)
    conn = _mock_conn()
    conn.fetchrow.return_value = {"id": uuid.uuid4(), "estado": "vigente", "xml": "<indice/>", "version": 1}
    # S1: `mark_signed` reporta `affected=1` en el segundo intento (éxito).
    conn.execute.return_value = "UPDATE 1"
    svc = IndexService(conn, TENANT)
    signed = _signed()

    outcomes = [SignOutcome(signed=None, transient=True), SignOutcome(signed=signed)]
    with patch(
        "app.clients.signature_client.sign_xml_outcome",
        new=AsyncMock(side_effect=outcomes),
    ) as mock_outcome:
        result = await svc.firmar_indice_close(uuid.uuid4(), tenant_slug=TENANT, actor_id=str(uuid.uuid4()))

    assert result is signed
    assert mock_outcome.await_count == 2
    assert any("estado='firmado'" in str(c.args[0]) for c in conn.execute.call_args_list)


@pytest.mark.asyncio
async def test_firmar_indice_close_transient_exhausted_degrades(monkeypatch) -> None:
    monkeypatch.setattr(settings, "close_sign_max_sync_attempts", 2)
    monkeypatch.setattr(settings, "close_sign_sync_delay_seconds", 0)
    conn = _mock_conn()
    conn.fetchrow.return_value = {"id": uuid.uuid4(), "estado": "vigente", "xml": "<indice/>", "version": 1}
    svc = IndexService(conn, TENANT)

    with patch(
        "app.clients.signature_client.sign_xml_outcome",
        new=AsyncMock(return_value=SignOutcome(signed=None, transient=True)),
    ) as mock_outcome:
        result = await svc.firmar_indice_close(uuid.uuid4(), tenant_slug=TENANT, actor_id=str(uuid.uuid4()))

    assert result is None
    assert mock_outcome.await_count == 2  # exactamente el tope configurado
    pendiente_calls = [
        c for c in conn.execute.call_args_list if "estado='pendiente_firma'" in str(c.args[0])
    ]
    assert pendiente_calls[0].args[2] == "sellado_xades_no_obtenido"


@pytest.mark.asyncio
async def test_firmar_indice_close_sin_actor_id_no_intenta_http() -> None:
    conn = _mock_conn()
    conn.fetchrow.return_value = {"id": uuid.uuid4(), "estado": "vigente", "xml": "<indice/>", "version": 1}
    svc = IndexService(conn, TENANT)

    with patch(
        "app.clients.signature_client.sign_xml_outcome", new=AsyncMock()
    ) as mock_outcome:
        result = await svc.firmar_indice_close(uuid.uuid4(), tenant_slug=TENANT, actor_id=None)

    assert result is None
    mock_outcome.assert_not_called()


@pytest.mark.asyncio
async def test_firmar_indice_close_ya_firmado_no_intenta_http() -> None:
    conn = _mock_conn()
    conn.fetchrow.return_value = {"id": uuid.uuid4(), "estado": "firmado", "xml": "<indice/>", "version": 1}
    svc = IndexService(conn, TENANT)

    with patch(
        "app.clients.signature_client.sign_xml_outcome", new=AsyncMock()
    ) as mock_outcome:
        result = await svc.firmar_indice_close(uuid.uuid4(), tenant_slug=TENANT, actor_id=str(uuid.uuid4()))

    assert result is None
    mock_outcome.assert_not_called()


# ---------------------------------------------------------------------------
# IndexService.list_pendientes_firma — no-read-up (PARTE 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pendientes_firma_resolves_clearance_and_delegates() -> None:
    conn = _mock_conn()
    svc = IndexService(conn, TENANT)
    user_id = uuid.uuid4()

    with (
        patch.object(svc._archive, "caller_clearance", new=AsyncMock(return_value=1)) as mock_clearance,
        patch.object(
            svc._index,
            "list_pending_signature_page",
            new=AsyncMock(return_value=([], 0)),
        ) as mock_page,
    ):
        items, total = await svc.list_pendientes_firma(page=2, size=10, user_id=user_id)

    assert items == []
    assert total == 0
    mock_clearance.assert_awaited_once_with(user_id)
    mock_page.assert_awaited_once_with(max_clearance=1, offset=10, limit=10)


@pytest.mark.asyncio
async def test_get_indices_pendientes_firma_endpoint_sets_total_count_header(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE + caller_clearance path
    mock_conn.fetchrow.return_value = {"is_root": False, "max_level": 1}
    mock_conn.fetch.return_value = []

    with patch.object(
        IndexRepository, "list_pending_signature_page", new=AsyncMock(return_value=([], 0))
    ):
        resp = await client.get(
            "/api/v1/expedientes/indices/pendientes-firma", headers=TENANT_HEADERS
        )

    assert resp.status_code == 200
    assert resp.json() == []
    assert resp.headers["X-Total-Count"] == "0"


# ---------------------------------------------------------------------------
# Job — app/jobs/indice_reconciliation.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_active_tenants_queries_public_tenants() -> None:
    from app.jobs import indice_reconciliation

    pool = MagicMock()
    conn = AsyncMock()
    conn.fetch.return_value = [{"slug": "tenant-a"}, {"slug": "tenant-b"}]
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    slugs = await indice_reconciliation.list_active_tenants(pool)

    assert slugs == ["tenant-a", "tenant-b"]
    sql = conn.fetch.call_args[0][0]
    assert "public.tenants" in sql
    assert "status = 'active'" in sql
    assert "provisioning_status = 'active'" in sql


@pytest.mark.asyncio
async def test_reconciliar_indice_success_marks_signed_and_audits_reconciliation() -> None:
    from app.jobs.indice_reconciliation import _reconciliar_indice

    conn = _mock_conn()
    conn.execute.return_value = "UPDATE 1"  # mark_signed SÍ ganó la fila (affected=1)
    signed = _signed()
    row = {
        "id": uuid.uuid4(),
        "expediente_id": uuid.uuid4(),
        "version": 1,
        "xml": "<indice/>",
        "reconcile_intentos": 1,
        "expediente_code": "EXP-2026-0001",
    }

    with patch(
        "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
        new=AsyncMock(return_value=SignOutcome(signed=signed)),
    ):
        resultado = await _reconciliar_indice(conn, TENANT, row)

    assert resultado == "sellado"
    assert any("estado='firmado'" in str(c.args[0]) for c in conn.execute.call_args_list)
    audit_calls = [
        c.args
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    # args: (sql, tenant, service, actor, canal, action, object_type, object_ref, payload_json, prev_hash, hash)
    import json

    assert audit_calls[0][3] == str(SYSTEM_RECONCILER_ID)
    assert audit_calls[0][5] == "archive.indice_firmado"
    assert audit_calls[0][4] == "system"
    payload = json.loads(audit_calls[0][8])
    assert payload["origen"] == "reconciliation"


@pytest.mark.asyncio
async def test_reconciliar_indice_lost_race_does_not_write_phantom_audit() -> None:
    """`mark_signed` devuelve `affected=0` ("UPDATE 0" — otra conexión ya
    firmó esta fila entre el listado y este UPDATE): `_reconciliar_indice`
    NO debe escribir un asiento `archive.indice_firmado` fantasma sobre un
    efecto que esta llamada no produjo (idempotencia real de la traza, no
    solo del UPDATE — ver el caso concurrente real en
    `tests/integration/test_indice_reconciliation_atomicity.py`)."""
    from app.jobs.indice_reconciliation import _reconciliar_indice

    conn = _mock_conn()
    conn.execute.return_value = "UPDATE 0"  # perdió la carrera
    row = {
        "id": uuid.uuid4(),
        "expediente_id": uuid.uuid4(),
        "version": 1,
        "xml": "<indice/>",
        "reconcile_intentos": 1,
        "expediente_code": "EXP-2026-0004",
    }

    with patch(
        "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
        new=AsyncMock(return_value=SignOutcome(signed=_signed())),
    ):
        resultado = await _reconciliar_indice(conn, TENANT, row)

    assert resultado == "sellado"  # el índice SÍ terminó firmado (por la otra conexión)
    audit_calls = [
        c
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert audit_calls == []


@pytest.mark.asyncio
async def test_reconciliar_indice_failure_schedules_backoff_not_agotado(monkeypatch) -> None:
    from app.jobs.indice_reconciliation import _reconciliar_indice

    monkeypatch.setattr(settings, "indice_reconciliation_max_intentos", 8)
    conn = _mock_conn()
    row = {
        "id": uuid.uuid4(),
        "expediente_id": uuid.uuid4(),
        "version": 1,
        "xml": "<indice/>",
        "reconcile_intentos": 2,  # tras este fallo: 3, < 8 -> no agotado
        "expediente_code": "EXP-2026-0002",
    }

    with patch(
        "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
        new=AsyncMock(return_value=SignOutcome(signed=None, transient=True)),
    ):
        resultado = await _reconciliar_indice(conn, TENANT, row)

    assert resultado == "fallido"
    bump_calls = [
        c for c in conn.execute.call_args_list if "reconcile_intentos = reconcile_intentos + 1" in str(c.args[0])
    ]
    assert len(bump_calls) == 1
    _, indice_id, proximo_at, ultimo_error, agotado = bump_calls[0].args
    assert indice_id == row["id"]
    assert proximo_at is not None
    assert ultimo_error == "sellado_xades_no_obtenido"
    assert agotado is False
    # Ningún asiento 'archive.indice_reconcile_agotado' — todavía no se agotó.
    audit_actions = [
        c.args[5]
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert "archive.indice_reconcile_agotado" not in audit_actions


@pytest.mark.asyncio
async def test_reconciliar_indice_reaches_max_intentos_marks_agotado(monkeypatch) -> None:
    from app.jobs.indice_reconciliation import _reconciliar_indice

    monkeypatch.setattr(settings, "indice_reconciliation_max_intentos", 3)
    conn = _mock_conn()
    row = {
        "id": uuid.uuid4(),
        "expediente_id": uuid.uuid4(),
        "version": 1,
        "xml": "<indice/>",
        "reconcile_intentos": 2,  # tras este fallo: 3, == max_intentos -> agotado
        "expediente_code": "EXP-2026-0003",
    }

    with patch(
        "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
        new=AsyncMock(return_value=SignOutcome(signed=None, seal_absent=True)),
    ):
        resultado = await _reconciliar_indice(conn, TENANT, row)

    assert resultado == "agotado"
    bump_calls = [
        c for c in conn.execute.call_args_list if "reconcile_intentos = reconcile_intentos + 1" in str(c.args[0])
    ]
    _, indice_id, proximo_at, ultimo_error, agotado = bump_calls[0].args
    assert proximo_at is None  # ya no hay "próximo intento" que programar
    # C2: mismo motivo textual que `firmar_indice_close` para la misma
    # condición (sello institucional ausente) -- unificado, ya no el código de
    # error de transporte `signing_seal_unavailable` de signature-service.
    assert ultimo_error == "sello_ausente"
    assert agotado is True
    audit_actions = [
        c.args[5]
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert "archive.indice_reconcile_agotado" in audit_actions


@pytest.mark.asyncio
async def test_run_once_aggregates_across_tenants_and_respects_advisory_lock() -> None:
    from app.jobs import indice_reconciliation

    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}, {"slug": "tenant-b"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    # tenant-a: obtiene el advisory lock, tiene 1 índice pendiente que sella OK.
    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True  # pg_try_advisory_lock -> adquirido
    row_a = {
        "id": uuid.uuid4(),
        "expediente_id": uuid.uuid4(),
        "version": 1,
        "xml": "<indice/>",
        "reconcile_intentos": 0,
        "expediente_code": "EXP-A-0001",
    }
    conn_a.fetch.return_value = [row_a]

    # tenant-b: NO obtiene el lock (otra instancia ya lo sostiene) -> se salta
    # por completo, ni siquiera se listan sus pendientes.
    conn_b = _mock_conn()
    conn_b.fetchval.return_value = False  # pg_try_advisory_lock -> no adquirido

    conns = {"tenant-a": conn_a, "tenant-b": conn_b}

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conns[slug]

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
        patch(
            "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
            new=AsyncMock(return_value=SignOutcome(signed=_signed())),
        ),
    ):
        summary = await indice_reconciliation.run_once()

    assert summary["pendientes_vistos"] == 1
    assert summary["sellados"] == 1
    assert summary["fallidos"] == 0
    assert summary["agotados"] == 0
    # tenant-b nunca llegó a listar pendientes (fetch solo lo llamó tenant-a).
    conn_b.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_run_once_isolates_per_index_failure() -> None:
    """Un índice que revienta con una excepción inesperada NO aborta la
    barrida del resto del batch de ese tenant."""
    from app.jobs import indice_reconciliation

    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True
    row_boom = {
        "id": uuid.uuid4(),
        "expediente_id": uuid.uuid4(),
        "version": 1,
        "xml": "<indice/>",
        "reconcile_intentos": 0,
        "expediente_code": "EXP-A-BOOM",
    }
    row_ok = {
        "id": uuid.uuid4(),
        "expediente_id": uuid.uuid4(),
        "version": 1,
        "xml": "<indice/>",
        "reconcile_intentos": 0,
        "expediente_code": "EXP-A-OK",
    }
    conn_a.fetch.return_value = [row_boom, row_ok]

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    async def _sign_side_effect(*, indice_id, **kwargs):
        if indice_id == str(row_boom["id"]):
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
        summary = await indice_reconciliation.run_once()

    assert summary["pendientes_vistos"] == 2
    assert summary["sellados"] == 1
    assert summary["fallidos"] == 1
