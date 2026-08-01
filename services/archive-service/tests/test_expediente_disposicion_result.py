# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Regresión M1: `close_expediente`/`transfer_expediente` NO deben re-leer vía
`ArchiveService.get_expediente` (ese método resuelve el clearance del
llamante y, al no propagarse `user_id` desde el router, degradaba a
CLEARANCE PUBLICA — un expediente RESERVADA/CLASIFICADA cerrado/transferido
con éxito volvía 404 espurio en la respuesta pese a que la mutación ya se
había comprometido, `get_db_conn` es autocommit).

Ambos endpoints ahora construyen su propio `ExpedienteDisposicionResult`
(schema estrecho) a partir de la fila que YA tenían (`exp` / el resultado de
`transition_expediente`), sin volver a tocar `get_expediente`."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import TENANT_HEADERS, make_expediente_record

_NARROW_KEYS = {"id", "code", "status", "disposition", "closed_at", "transferred_at"}
_FORBIDDEN_KEYS = {"metadata", "description", "radicados", "opened_by"}


def _patch_index_service():
    from app.services.index_service import IndexService

    return (
        patch.object(IndexService, "regenerate", new=AsyncMock()),
        patch.object(IndexService, "firmar_indice_close", new=AsyncMock(return_value=None)),
    )


@pytest.mark.asyncio
async def test_close_classified_expediente_returns_200_not_404(client_with_conn) -> None:
    """Regresión M1: un expediente CLASIFICADA (nivel_seguridad=3) que
    transiciona a 'closed' responde 200, NO 404. Si alguien reintroduce
    `return await self.get_expediente(...)` al final de `close_expediente`,
    ese método vuelve a resolver el clearance del llamante y este test debe
    fallar (lo detectamos aquí espiando que `get_expediente` NUNCA se
    invoque durante el cierre)."""
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    exp_open = make_expediente_record(exp_id=exp_id, exp_status="open", nivel_seguridad=3)
    exp_closed = make_expediente_record(exp_id=exp_id, exp_status="closed", nivel_seguridad=3)
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    # _expediente_or_404 → transition_expediente. Sin TRD serie: no hay
    # lectura de trd, y sin la re-lectura del bug (get_expediente) no debe
    # haber una tercera fetchrow.
    mock_conn.fetchrow.side_effect = [exp_open, exp_closed]
    mock_conn.fetch.return_value = []

    from app.services.archive_service import ArchiveService

    patch_regen, patch_firmar = _patch_index_service()
    with (
        patch_regen,
        patch_firmar,
        patch.object(ArchiveService, "get_expediente", new=AsyncMock()) as mock_get,
    ):
        resp = await client.post(f"/api/v1/expedientes/{exp_id}/close", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"
    mock_get.assert_not_called()


@pytest.mark.asyncio
async def test_transfer_classified_expediente_returns_200_not_404(client_with_conn) -> None:
    """Igual que el anterior pero para `transfer_expediente` (closed →
    transferred) sobre un expediente CLASIFICADA."""
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    closed = make_expediente_record(exp_id=exp_id, exp_status="closed", nivel_seguridad=3)
    transferred = make_expediente_record(exp_id=exp_id, exp_status="transferred", nivel_seguridad=3)
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    # _expediente_or_404 → _assert_indice_firmado(get_current) → transition_expediente.
    mock_conn.fetchrow.side_effect = [
        closed,
        {"id": uuid.uuid4(), "estado": "firmado"},
        transferred,
    ]
    mock_conn.fetch.return_value = []

    from app.services.archive_service import ArchiveService

    with patch.object(ArchiveService, "get_expediente", new=AsyncMock()) as mock_get:
        resp = await client.post(f"/api/v1/expedientes/{exp_id}/transfer", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    assert resp.json()["status"] == "transferred"
    mock_get.assert_not_called()


@pytest.mark.asyncio
async def test_close_response_omits_classified_content_fields(client_with_conn) -> None:
    """La respuesta de close es el schema estrecho `ExpedienteDisposicionResult`:
    NO trae `metadata`, `description`, `radicados` ni `opened_by` — si alguien
    vuelve a usar `ExpedienteResponse` como response_model, estas claves
    reaparecen y el test falla."""
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    exp_open = make_expediente_record(
        exp_id=exp_id,
        exp_status="open",
        nivel_seguridad=3,
        metadata={"secreto": "contenido clasificado"},
        description="Descripción sensible",
    )
    exp_closed = make_expediente_record(
        exp_id=exp_id,
        exp_status="closed",
        nivel_seguridad=3,
        metadata={"secreto": "contenido clasificado"},
        description="Descripción sensible",
    )
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [exp_open, exp_closed]
    mock_conn.fetch.return_value = []

    patch_regen, patch_firmar = _patch_index_service()
    with patch_regen, patch_firmar:
        resp = await client.post(f"/api/v1/expedientes/{exp_id}/close", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == _NARROW_KEYS
    assert not _FORBIDDEN_KEYS & set(body.keys())


@pytest.mark.asyncio
async def test_transfer_response_omits_classified_content_fields(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    closed = make_expediente_record(
        exp_id=exp_id,
        exp_status="closed",
        nivel_seguridad=3,
        metadata={"secreto": "contenido clasificado"},
        description="Descripción sensible",
    )
    transferred = make_expediente_record(
        exp_id=exp_id,
        exp_status="transferred",
        nivel_seguridad=3,
        metadata={"secreto": "contenido clasificado"},
        description="Descripción sensible",
    )
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        closed,
        {"id": uuid.uuid4(), "estado": "firmado"},
        transferred,
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/transfer", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == _NARROW_KEYS
    assert not _FORBIDDEN_KEYS & set(body.keys())


@pytest.mark.asyncio
async def test_close_response_reflects_transitioned_row_not_pretransition_exp(
    client_with_conn,
) -> None:
    """La respuesta de close viene de la fila que devuelve
    `transition_expediente` (RETURNING *, ya con status='closed'), NO del
    `exp` cargado antes de la transición (status='open'). Usa un
    `closed_at` distintivo en la fila transicionada para probar que la
    respuesta lee de ahí y no del `exp` pre-transición (que trae
    `closed_at=None`)."""
    from datetime import UTC, datetime

    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    closed_at_sentinel = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
    exp_open = make_expediente_record(exp_id=exp_id, exp_status="open", closed_at=None)
    exp_closed = make_expediente_record(
        exp_id=exp_id, exp_status="closed", closed_at=closed_at_sentinel
    )
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [exp_open, exp_closed]
    mock_conn.fetch.return_value = []

    patch_regen, patch_firmar = _patch_index_service()
    with patch_regen, patch_firmar:
        resp = await client.post(f"/api/v1/expedientes/{exp_id}/close", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "closed"
    assert body["closed_at"] == closed_at_sentinel.isoformat().replace("+00:00", "Z")


@pytest.mark.asyncio
async def test_close_disposition_present_with_trd_serie(client_with_conn) -> None:
    """Con `trd_serie_id` configurada, el close materializa y devuelve el
    `disposition` calculado por la TRD (ver también
    `test_close_applies_trd_disposition` en test_expediente_lifecycle.py —
    aquí se aisla específicamente el contrato del schema estrecho)."""
    from tests.conftest import make_trd_serie_record

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
    mock_conn.fetchval.return_value = True
    # ADR-025 (Increment 1): `close_expediente` ahora re-resuelve la version
    # VIGENTE por codigo antes de calcular -- 3 lecturas de `trd_series`
    # (pinned / vigente / la que hace `compute_retention` sobre `vigente.id`)
    # en vez de 1. Misma fila (mock) las tres veces: no cambia el contrato
    # que este test aisla (el `disposition` calculado).
    mock_conn.fetchrow.side_effect = [exp_open, trd, trd, trd, exp_closed]
    mock_conn.fetch.return_value = []

    patch_regen, patch_firmar = _patch_index_service()
    with patch_regen, patch_firmar:
        resp = await client.post(f"/api/v1/expedientes/{exp_id}/close", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["disposition"] is not None
    assert body["disposition"]["disposition_code"] == "CT"


@pytest.mark.asyncio
async def test_close_disposition_none_without_trd_serie(client_with_conn) -> None:
    """Sin `trd_serie_id`, el close no materializa disposición: el campo
    viaja como `None` en el schema estrecho (no se omite ni se convierte en
    otro tipo)."""
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    exp_open = make_expediente_record(exp_id=exp_id, trd_serie_id=None, exp_status="open")
    exp_closed = make_expediente_record(
        exp_id=exp_id, trd_serie_id=None, exp_status="closed", disposition=None
    )
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [exp_open, exp_closed]
    mock_conn.fetch.return_value = []

    patch_regen, patch_firmar = _patch_index_service()
    with patch_regen, patch_firmar:
        resp = await client.post(f"/api/v1/expedientes/{exp_id}/close", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["disposition"] is None
