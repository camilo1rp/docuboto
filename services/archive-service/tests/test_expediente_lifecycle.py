# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del ciclo de vida del expediente: cierre/transferencia/índice (E02)."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import (
    TENANT_HEADERS,
    USER_ID,
    make_expediente_record,
    make_radicado_link_record,
    make_trd_serie_record,
)


@pytest.mark.asyncio
async def test_close_applies_trd_disposition(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    serie_id = uuid.uuid4()
    exp_id = uuid.uuid4()
    exp_open = make_expediente_record(exp_id=exp_id, trd_serie_id=serie_id, exp_status="open")
    trd = make_trd_serie_record(
        disposition="conserve", archivo_gestion_years=3, archivo_central_years=7
    )
    exp_closed = make_expediente_record(
        exp_id=exp_id,
        trd_serie_id=serie_id,
        exp_status="closed",
        disposition={"disposition_code": "CT", "disposition_label": "Conservación Total"},
    )
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    # fetchrow: exp(open) → trd → exp(closed update) → exp(reload).
    # El índice (regenerar+firmar) se aísla parcheando IndexService.
    mock_conn.fetchrow.side_effect = [exp_open, trd, exp_closed, exp_closed]
    mock_conn.fetch.return_value = []

    from app.services.index_service import IndexService

    with (
        patch.object(IndexService, "regenerate", new=AsyncMock()),
        patch.object(IndexService, "firmar_indice", new=AsyncMock(return_value=None)),
    ):
        resp = await client.post(f"/api/v1/expedientes/{exp_id}/close", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "closed"
    assert body["disposition"]["disposition_code"] == "CT"


@pytest.mark.asyncio
async def test_close_forwards_x_user_id_as_actor_id(client_with_conn) -> None:
    """Regresión del bugfix (E06 Inc.1): el cierre reenvía X-User-Id como
    `actor_id` a `IndexService.firmar_indice` (antes se perdía → 401
    permanente en signature-service)."""
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    exp_open = make_expediente_record(exp_id=exp_id, exp_status="open")
    exp_closed = make_expediente_record(exp_id=exp_id, exp_status="closed")
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.side_effect = [exp_open, exp_closed, exp_closed]
    mock_conn.fetch.return_value = []

    from app.services.index_service import IndexService

    with (
        patch.object(IndexService, "regenerate", new=AsyncMock()),
        patch.object(IndexService, "firmar_indice", new=AsyncMock(return_value=None)) as mock_firmar,
    ):
        resp = await client.post(f"/api/v1/expedientes/{exp_id}/close", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    mock_firmar.assert_awaited_once()
    assert mock_firmar.call_args.kwargs["actor_id"] == str(USER_ID)


@pytest.mark.asyncio
async def test_close_sin_firma_audita_indice_pendiente(client_with_conn) -> None:
    """Media (indice/audit): si el sellado falla al cierre, el audit_log de
    'archive.expediente_closed' lleva indice_estado='pendiente_firma' Y se
    emite una entrada adicional 'archive.indice_firma_pendiente' (traza del
    fallo de sellado — ADR-010)."""
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    exp_open = make_expediente_record(exp_id=exp_id, exp_status="open")
    exp_closed = make_expediente_record(exp_id=exp_id, exp_status="closed")
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.side_effect = [exp_open, exp_closed, exp_closed]
    mock_conn.fetch.return_value = []

    from app.services.index_service import IndexService

    with (
        patch.object(IndexService, "regenerate", new=AsyncMock()),
        patch.object(IndexService, "firmar_indice", new=AsyncMock(return_value=None)),
    ):
        resp = await client.post(f"/api/v1/expedientes/{exp_id}/close", headers=TENANT_HEADERS)
    assert resp.status_code == 200

    audit_calls = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    # args: (sql, tenant, service, actor, canal, action, object_type, object_ref, payload, prev_hash, hash)
    actions = [a[5] for a in audit_calls]
    assert "archive.indice_firma_pendiente" in actions
    closed_payloads = [a[8] for a in audit_calls if a[5] == "archive.expediente_closed"]
    assert closed_payloads and '"pendiente_firma"' in closed_payloads[0]


@pytest.mark.asyncio
async def test_firmar_indice_marca_firmado(client_with_conn) -> None:
    """El índice vigente se firma vía signature-service y queda 'firmado'."""
    client, mock_conn = client_with_conn  # noqa: F841 (solo para overrides de app)
    from app.clients.signature_client import SignedIndice
    from app.services.index_service import IndexService

    exp_id = uuid.uuid4()
    firma_id = uuid.uuid4()
    actor_id = str(uuid.uuid4())
    svc = IndexService(mock_conn, "demo")
    # get_current → índice vigente; mark_signed → execute
    mock_conn.fetchrow.return_value = {"id": uuid.uuid4(), "estado": "vigente", "xml": "<indice/>", "version": 1}

    signed = SignedIndice(
        firma_id=firma_id,
        minio_key_firmado="file-id/indice.xml",
        nivel_conformidad="xades_b_no_acreditado",
        xades_level="XAdES-B",
        tipo_firma="sello_sistema",
        acreditado=False,
        certificado_fingerprint="ab:cd",
    )

    from app.clients import signature_client

    with patch.object(signature_client, "sign_xml", new=AsyncMock(return_value=signed)):
        result = await svc.firmar_indice(exp_id, tenant_slug="demo", actor_id=actor_id)
    assert result is not None
    assert result.firma_id == firma_id
    # Se ejecutó el UPDATE de mark_signed (estado='firmado').
    assert any("estado='firmado'" in str(c.args[0]) for c in mock_conn.execute.call_args_list)


@pytest.mark.asyncio
async def test_firmar_indice_sin_actor_id_queda_pendiente(client_with_conn) -> None:
    """Sin actor_id (X-User-Id) no se intenta la llamada HTTP: 'pendiente_firma'."""
    client, mock_conn = client_with_conn  # noqa: F841
    from app.services.index_service import IndexService

    exp_id = uuid.uuid4()
    svc = IndexService(mock_conn, "demo")
    mock_conn.fetchrow.return_value = {"id": uuid.uuid4(), "estado": "vigente", "xml": "<indice/>", "version": 1}

    from app.clients import signature_client

    with patch.object(signature_client, "sign_xml", new=AsyncMock()) as mock_sign:
        result = await svc.firmar_indice(exp_id, tenant_slug="demo", actor_id=None)
    assert result is None
    mock_sign.assert_not_called()
    assert any("estado='pendiente_firma'" in str(c.args[0]) for c in mock_conn.execute.call_args_list)


@pytest.mark.asyncio
async def test_firmar_indice_signature_service_inalcanzable_no_bloquea(client_with_conn) -> None:
    """Si signature-service no responde (sign_xml → None), el índice queda
    'pendiente_firma' y el cierre no se bloquea (best-effort, RT-16)."""
    client, mock_conn = client_with_conn  # noqa: F841
    from app.services.index_service import IndexService

    exp_id = uuid.uuid4()
    actor_id = str(uuid.uuid4())
    svc = IndexService(mock_conn, "demo")
    mock_conn.fetchrow.return_value = {"id": uuid.uuid4(), "estado": "vigente", "xml": "<indice/>", "version": 1}

    from app.clients import signature_client

    with patch.object(signature_client, "sign_xml", new=AsyncMock(return_value=None)) as mock_sign:
        result = await svc.firmar_indice(exp_id, tenant_slug="demo", actor_id=actor_id)
    assert result is None
    mock_sign.assert_awaited_once()
    assert any("estado='pendiente_firma'" in str(c.args[0]) for c in mock_conn.execute.call_args_list)


@pytest.mark.asyncio
async def test_close_invalid_state(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.return_value = make_expediente_record(exp_status="closed")
    mock_conn.fetch.return_value = []

    resp = await client.post(f"/api/v1/expedientes/{uuid.uuid4()}/close", headers=TENANT_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "invalid_status_transition"


@pytest.mark.asyncio
async def test_transfer_from_closed(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    closed = make_expediente_record(exp_id=exp_id, exp_status="closed")
    transferred = make_expediente_record(exp_id=exp_id, exp_status="transferred")
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    # _expediente_or_404 → _assert_indice_firmado(get_current) → transition_expediente → reload.
    mock_conn.fetchrow.side_effect = [
        closed,
        {"id": uuid.uuid4(), "estado": "firmado"},
        transferred,
        transferred,
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/transfer", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "transferred"


@pytest.mark.asyncio
async def test_transfer_sin_indice_firmado_409(client_with_conn) -> None:
    """E06 Inc.1 / Acuerdo AGN 001/2024 art. 4.3.2.4: un índice 'pendiente_firma'
    (sellado no obtenido al cierre) bloquea la transferencia, aunque no bloqueó
    el cierre (RT-16, best-effort)."""
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    closed = make_expediente_record(exp_id=exp_id, exp_status="closed")
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.side_effect = [
        closed,
        {"id": uuid.uuid4(), "estado": "pendiente_firma"},
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/transfer", headers=TENANT_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "indice_no_firmado"


@pytest.mark.asyncio
async def test_indice_foliation(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.return_value = make_expediente_record(exp_id=exp_id)
    mock_conn.fetch.return_value = [
        make_radicado_link_record(radicado_id=uuid.uuid4(), tracking_number="2024-A-001"),
        make_radicado_link_record(radicado_id=uuid.uuid4(), tracking_number="2024-A-002"),
    ]

    resp = await client.get(f"/api/v1/expedientes/{exp_id}/foliado", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_folios"] == 2
    assert body["items"][0]["folio"] == 1
    assert body["items"][1]["folio"] == 2
