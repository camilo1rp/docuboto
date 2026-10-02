# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""POST /api/v1/expedientes/{id}/legal-hold (AREA-08-07).

Único camino de usuario hacia el freeze de litigio de storage. Secuencia:
  fetchval — gate RBAC (USUA_PERM_ADMIN, crud>=3)
  fetchrow — resolve_caller_clearance
  fetchrow — get_expediente_by_id (clearance en el WHERE; None => 404 neutro)
  [storage_client.fijar_legal_hold_internal — mockeado aparte]
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.clients.storage_client import StorageLegalHoldError
from tests.conftest import EXPEDIENTE_ID, TENANT_HEADERS, make_expediente_record

_BODY = {
    "activo": True,
    "motivo": "Orden del juzgado 12 por proceso en curso",
    "acta_ref": "ACTA-2026-044",
}
_URL = f"/api/v1/expedientes/{EXPEDIENTE_ID}/legal-hold"
_RES = {
    "expediente_id": str(EXPEDIENTE_ID),
    "activo": True,
    "objetos_afectados": 2,
    "objetos_sin_cambio": 0,
}


def _asientos(mock_conn: AsyncMock) -> list[str]:
    return [
        " ".join(str(a) for a in c[0])
        for c in mock_conn.execute.call_args_list
        if "audit_log" in str(c[0][0])
    ]


@pytest.mark.asyncio
async def test_declarar_hold_orquesta_storage_y_asienta_en_archive(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=1)
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [{"is_root": False, "max_level": 3}, exp]

    with patch(
        "app.clients.storage_client.fijar_legal_hold_internal",
        new=AsyncMock(return_value=_RES),
    ) as fijar:
        resp = await client.post(_URL, json=_BODY, headers=TENANT_HEADERS)

    assert resp.status_code == 200, resp.text
    assert resp.json()["objetos_afectados"] == 2
    kw = fijar.await_args.kwargs
    assert kw["expediente_id"] == str(EXPEDIENTE_ID)
    assert kw["activo"] is True and kw["acta_ref"] == "ACTA-2026-044"
    asientos = _asientos(mock_conn)
    assert any("expediente_legal_hold_fijado" in a for a in asientos)


@pytest.mark.asyncio
async def test_expediente_invisible_es_404_neutro_y_no_toca_storage(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [{"is_root": False, "max_level": 1}, None]

    with patch(
        "app.clients.storage_client.fijar_legal_hold_internal", new=AsyncMock()
    ) as fijar:
        resp = await client.post(_URL, json=_BODY, headers=TENANT_HEADERS)

    assert resp.status_code == 404
    fijar.assert_not_called()
    assert not _asientos(mock_conn)


@pytest.mark.asyncio
async def test_sin_permiso_admin_es_403_y_no_toca_storage(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False

    with patch(
        "app.clients.storage_client.fijar_legal_hold_internal", new=AsyncMock()
    ) as fijar:
        resp = await client.post(_URL, json=_BODY, headers=TENANT_HEADERS)

    assert resp.status_code == 403
    fijar.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cuerpo",
    [
        {**_BODY, "motivo": "corto"},
        {k: v for k, v in _BODY.items() if k != "acta_ref"},
        {**_BODY, "legal_hold": True},
    ],
)
async def test_motivo_y_acta_obligatorios_y_campos_extra_prohibidos(
    client_with_conn: tuple[AsyncClient, AsyncMock], cuerpo: dict
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    resp = await client.post(_URL, json=cuerpo, headers=TENANT_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_storage", "esperado"), [(404, 404), (503, 503)]
)
async def test_fallo_de_storage_no_deja_asiento_de_archive(
    client_with_conn: tuple[AsyncClient, AsyncMock], status_storage: int, esperado: int
) -> None:
    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=1)
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [{"is_root": False, "max_level": 3}, exp]

    with patch(
        "app.clients.storage_client.fijar_legal_hold_internal",
        new=AsyncMock(side_effect=StorageLegalHoldError(status_storage, "x")),
    ):
        resp = await client.post(_URL, json=_BODY, headers=TENANT_HEADERS)

    assert resp.status_code == esperado
    assert not any("legal_hold" in a for a in _asientos(mock_conn))


@pytest.mark.asyncio
async def test_el_gate_es_admin_con_escritura_y_no_el_permiso_de_operar_expedientes(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """El permiso de operar expedientes lo tiene cualquier tramitador; congelar
    la disposicion es un acto de custodia: USUA_PERM_ADMIN, crud >= 3."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False
    await client.post(_URL, json=_BODY, headers=TENANT_HEADERS)
    consulta = next(c for c in mock_conn.fetchval.call_args_list if "auth_permissions" in c.args[0])
    assert consulta.args[2:] == ("USUA_PERM_ADMIN", 3)
