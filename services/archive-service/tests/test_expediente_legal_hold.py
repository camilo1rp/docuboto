# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Retenciones legales POR MOTIVO (AREA-08-07, C-5 / C7, ADR-043).

  GET  /api/v1/expedientes/{id}/legal-holds
  POST /api/v1/expedientes/{id}/legal-holds
  POST /api/v1/expedientes/{id}/legal-holds/{hold_id}/levantar

Secuencia de `mock_conn` (cada ruta):
  fetchval — gate RBAC (USUA_PERM_ADMIN; crud>=1 lectura, >=3 escritura)
  fetchrow — resolve_caller_clearance
  fetchrow — get_expediente_by_id (clearance en el WHERE; None => 404 neutro)
  [storage_client.* — mockeado aparte]

Mutaciones que deben caer (descritas junto a cada test):
  * levantar sin buscar el hold ENTRE LOS DEL EXPEDIENTE -> test_hold_de_otro_expediente...
  * quitar la comparación local `valoracion_pendiente` -> test_hold_de_sistema...
  * escribir el asiento ANTES de llamar a storage -> test_fallo_de_storage_no_deja_asiento...
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.clients.storage_client import StorageLegalHoldError
from tests.conftest import EXPEDIENTE_ID, TENANT_HEADERS, make_expediente_record

HOLD_ID = uuid.UUID("5d0f6a52-0d0b-4f3e-9e2c-2b1f9b0a1111")
_BASE = f"/api/v1/expedientes/{EXPEDIENTE_ID}/legal-holds"
_FIJAR = {
    "motivo_tipo": "judicial",
    "fundamento": "Orden del juzgado 12 por proceso en curso",
    "acta_ref": "ACTA-2026-044",
}
_LEVANTAR = {
    "motivo_tipo": "judicial",
    "fundamento": "Fallo ejecutoriado a favor de la entidad",
    "acta_ref": "ACTA-2026-070",
}


def _hold(motivo: str = "judicial", *, levantado: bool = False, hold_id: uuid.UUID = HOLD_ID) -> dict:
    return {
        "hold_id": str(hold_id),
        "expediente_id": str(EXPEDIENTE_ID),
        "motivo_tipo": motivo,
        "fundamento": "Orden del juzgado 12",
        "acta_ref": "ACTA-1",
        "fijado_por": str(uuid.uuid4()) if motivo != "valoracion_pendiente" else None,
        "fijado_at": datetime.now(tz=UTC).isoformat(),
        "levantado_at": datetime.now(tz=UTC).isoformat() if levantado else None,
        "levantado_por": None,
        "levantado_fundamento": "x" * 12 if levantado else None,
        "levantado_acta_ref": "A" if levantado else None,
    }


def _asientos(mock_conn: AsyncMock) -> list[str]:
    return [
        " ".join(str(a) for a in c[0])
        for c in mock_conn.execute.call_args_list
        if "audit_log" in str(c[0][0])
    ]


def _con_exp(mock_conn: AsyncMock, *, visible: bool = True) -> None:
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=1) if visible else None
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [{"is_root": False, "max_level": 3}, exp]


# --- fijar ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fijar_orquesta_storage_y_asienta_en_archive(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    _con_exp(mock_conn)
    res = {"hold_id": str(HOLD_ID), "creado": True, "objetos_afectados": 0}
    with patch(
        "app.clients.storage_client.fijar_legal_hold", new=AsyncMock(return_value=res)
    ) as fijar:
        resp = await client.post(_BASE, json=_FIJAR, headers=TENANT_HEADERS)

    assert resp.status_code == 201, resp.text
    # Control positivo del cierre de H2: 0 objetos afectados es un 201 (el hold existe igual).
    assert resp.json() == {"hold_id": str(HOLD_ID), "objetos_afectados": 0}
    kw = fijar.await_args.kwargs
    assert kw["expediente_id"] == str(EXPEDIENTE_ID)
    assert kw["motivo_tipo"] == "judicial" and kw["acta_ref"] == "ACTA-2026-044"
    assert kw["actor_id"] is not None  # identidad humana firmada
    asientos = _asientos(mock_conn)
    assert any("expediente_legal_hold_fijado" in a for a in asientos)


@pytest.mark.asyncio
async def test_fijar_valoracion_pendiente_es_422_hold_de_sistema(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Ningún usuario fija el hold de sistema. Mutación: aceptarlo -> storage recibiría la llamada."""
    client, mock_conn = client_with_conn
    _con_exp(mock_conn)
    with patch("app.clients.storage_client.fijar_legal_hold", new=AsyncMock()) as fijar:
        resp = await client.post(
            _BASE, json={**_FIJAR, "motivo_tipo": "valoracion_pendiente"}, headers=TENANT_HEADERS
        )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "hold_de_sistema"
    fijar.assert_not_called()


@pytest.mark.asyncio
async def test_expediente_invisible_es_404_neutro_y_no_toca_storage(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    _con_exp(mock_conn, visible=False)
    with patch("app.clients.storage_client.fijar_legal_hold", new=AsyncMock()) as fijar:
        resp = await client.post(_BASE, json=_FIJAR, headers=TENANT_HEADERS)
    assert resp.status_code == 404
    fijar.assert_not_called()
    assert not _asientos(mock_conn)


@pytest.mark.asyncio
async def test_sin_permiso_admin_es_403_y_no_toca_storage(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False
    with patch("app.clients.storage_client.fijar_legal_hold", new=AsyncMock()) as fijar:
        resp = await client.post(_BASE, json=_FIJAR, headers=TENANT_HEADERS)
    assert resp.status_code == 403
    fijar.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cuerpo",
    [
        {**_FIJAR, "fundamento": "corto"},
        {k: v for k, v in _FIJAR.items() if k != "acta_ref"},
        {k: v for k, v in _FIJAR.items() if k != "motivo_tipo"},
        {**_FIJAR, "motivo_tipo": "capricho"},
        {**_FIJAR, "activo": True},  # el booleano legado ya no existe
    ],
)
async def test_motivo_fundamento_y_acta_obligatorios_y_campos_extra_prohibidos(
    client_with_conn: tuple[AsyncClient, AsyncMock], cuerpo: dict
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    resp = await client.post(_BASE, json=cuerpo, headers=TENANT_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(("status_storage", "esperado"), [(404, 404), (503, 503), (500, 503)])
async def test_fallo_de_storage_no_deja_asiento_de_archive(
    client_with_conn: tuple[AsyncClient, AsyncMock], status_storage: int, esperado: int
) -> None:
    client, mock_conn = client_with_conn
    _con_exp(mock_conn)
    with patch(
        "app.clients.storage_client.fijar_legal_hold",
        new=AsyncMock(side_effect=StorageLegalHoldError(status_storage, "x")),
    ):
        resp = await client.post(_BASE, json=_FIJAR, headers=TENANT_HEADERS)
    assert resp.status_code == esperado
    assert not any("legal_hold" in a for a in _asientos(mock_conn))


@pytest.mark.asyncio
async def test_el_gate_es_admin_con_escritura_y_no_el_permiso_de_operar_expedientes(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Congelar la disposición es un acto de custodia: USUA_PERM_ADMIN, crud >= 3 (la lectura,
    crud >= 1)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False
    await client.post(_BASE, json=_FIJAR, headers=TENANT_HEADERS)
    consulta = next(c for c in mock_conn.fetchval.call_args_list if "auth_permissions" in c.args[0])
    assert consulta.args[2:] == ("USUA_PERM_ADMIN", 3)

    mock_conn.fetchval.reset_mock()
    mock_conn.fetchval.return_value = False
    await client.get(_BASE, headers=TENANT_HEADERS)
    consulta = next(c for c in mock_conn.fetchval.call_args_list if "auth_permissions" in c.args[0])
    assert consulta.args[2:] == ("USUA_PERM_ADMIN", 1)


# --- listar --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listar_devuelve_activos_y_levantados_con_su_motivo(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    _con_exp(mock_conn)
    holds = [_hold("judicial"), _hold("valoracion_pendiente", hold_id=uuid.uuid4()),
             _hold("fiscal", levantado=True, hold_id=uuid.uuid4())]
    with patch("app.clients.storage_client.listar_legal_holds", new=AsyncMock(return_value=holds)):
        resp = await client.get(_BASE, headers=TENANT_HEADERS)
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert [h["motivo_tipo"] for h in cuerpo] == ["judicial", "valoracion_pendiente", "fiscal"]
    assert cuerpo[1]["fijado_por"] is None  # el de sistema no tiene persona
    assert cuerpo[2]["levantado_at"] is not None


@pytest.mark.asyncio
async def test_listar_expediente_invisible_es_404_y_no_consulta_storage(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    _con_exp(mock_conn, visible=False)
    with patch("app.clients.storage_client.listar_legal_holds", new=AsyncMock()) as listar:
        resp = await client.get(_BASE, headers=TENANT_HEADERS)
    assert resp.status_code == 404
    listar.assert_not_called()


# --- levantar ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_levantar_confirma_el_motivo_y_asienta(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    _con_exp(mock_conn)
    res = {"hold_id": str(HOLD_ID), "objetos_liberados_s3": 2, "holds_restantes": 0}
    with (
        patch("app.clients.storage_client.listar_legal_holds", new=AsyncMock(return_value=[_hold()])),
        patch(
            "app.clients.storage_client.levantar_legal_hold", new=AsyncMock(return_value=res)
        ) as levantar,
    ):
        resp = await client.post(
            f"{_BASE}/{HOLD_ID}/levantar", json=_LEVANTAR, headers=TENANT_HEADERS
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"hold_id": str(HOLD_ID), "objetos_liberados_s3": 2, "holds_restantes": 0}
    assert levantar.await_args.kwargs["motivo_tipo_esperado"] == "judicial"
    assert levantar.await_args.kwargs["hold_id"] == HOLD_ID
    assert any("expediente_legal_hold_levantado" in a for a in _asientos(mock_conn))


@pytest.mark.asyncio
async def test_levantar_con_otro_hold_activo_no_libera_s3(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """H1: levantar UN hold con otro vivo (p. ej. el judicial) devuelve `holds_restantes > 0` y
    `objetos_liberados_s3 == 0` (lo decide storage; archive lo transmite sin 'corregirlo')."""
    client, mock_conn = client_with_conn
    _con_exp(mock_conn)
    res = {"hold_id": str(HOLD_ID), "objetos_liberados_s3": 0, "holds_restantes": 1}
    with (
        patch("app.clients.storage_client.listar_legal_holds", new=AsyncMock(return_value=[_hold()])),
        patch("app.clients.storage_client.levantar_legal_hold", new=AsyncMock(return_value=res)),
    ):
        resp = await client.post(
            f"{_BASE}/{HOLD_ID}/levantar", json=_LEVANTAR, headers=TENANT_HEADERS
        )
    assert resp.json()["holds_restantes"] == 1 and resp.json()["objetos_liberados_s3"] == 0


@pytest.mark.asyncio
async def test_hold_de_otro_expediente_es_404_y_no_llega_a_storage(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """El hold se busca ENTRE LOS DEL EXPEDIENTE. Mutación: pasar el hold_id directo a storage
    (sin buscarlo) -> `levantar` se llamaría con un hold ajeno."""
    client, mock_conn = client_with_conn
    _con_exp(mock_conn)
    ajeno = _hold(hold_id=uuid.uuid4())
    with (
        patch("app.clients.storage_client.listar_legal_holds", new=AsyncMock(return_value=[ajeno])),
        patch("app.clients.storage_client.levantar_legal_hold", new=AsyncMock()) as levantar,
    ):
        resp = await client.post(
            f"{_BASE}/{HOLD_ID}/levantar", json=_LEVANTAR, headers=TENANT_HEADERS
        )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "hold_not_found"
    levantar.assert_not_called()


@pytest.mark.asyncio
async def test_hold_de_sistema_no_se_levanta_a_mano(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    _con_exp(mock_conn)
    with (
        patch(
            "app.clients.storage_client.listar_legal_holds",
            new=AsyncMock(return_value=[_hold("valoracion_pendiente")]),
        ),
        patch("app.clients.storage_client.levantar_legal_hold", new=AsyncMock()) as levantar,
    ):
        resp = await client.post(
            f"{_BASE}/{HOLD_ID}/levantar",
            json={**_LEVANTAR, "motivo_tipo": "valoracion_pendiente"},
            headers=TENANT_HEADERS,
        )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "hold_de_sistema"
    levantar.assert_not_called()


@pytest.mark.asyncio
async def test_hold_ya_levantado_es_409(client_with_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, mock_conn = client_with_conn
    _con_exp(mock_conn)
    with (
        patch(
            "app.clients.storage_client.listar_legal_holds",
            new=AsyncMock(return_value=[_hold(levantado=True)]),
        ),
        patch("app.clients.storage_client.levantar_legal_hold", new=AsyncMock()) as levantar,
    ):
        resp = await client.post(
            f"{_BASE}/{HOLD_ID}/levantar", json=_LEVANTAR, headers=TENANT_HEADERS
        )
    assert resp.status_code == 409 and resp.json()["detail"]["error"] == "hold_ya_levantado"
    levantar.assert_not_called()


@pytest.mark.asyncio
async def test_motivo_que_no_coincide_lo_rechaza_storage_y_archive_lo_traduce(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """La confirmación se reenvía a storage (que la compara y es la autoridad). Mutación: quitar
    `motivo_tipo_esperado` del reenvío -> el test de `kwargs` cae."""
    client, mock_conn = client_with_conn
    _con_exp(mock_conn)
    with (
        patch("app.clients.storage_client.listar_legal_holds", new=AsyncMock(return_value=[_hold()])),
        patch(
            "app.clients.storage_client.levantar_legal_hold",
            new=AsyncMock(side_effect=StorageLegalHoldError(409, "x", "motivo_no_coincide")),
        ) as levantar,
    ):
        resp = await client.post(
            f"{_BASE}/{HOLD_ID}/levantar",
            json={**_LEVANTAR, "motivo_tipo": "fiscal"},
            headers=TENANT_HEADERS,
        )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "motivo_no_coincide"
    assert levantar.await_args.kwargs["motivo_tipo_esperado"] == "fiscal"
    assert not any("legal_hold_levantado" in a for a in _asientos(mock_conn))


@pytest.mark.asyncio
async def test_el_endpoint_viejo_no_existe(client_with_conn: tuple[AsyncClient, AsyncMock]) -> None:
    """`POST /expedientes/{id}/legal-hold` (booleano) se retiró sin compatibilidad."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    resp = await client.post(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/legal-hold",
        json={"activo": True, "motivo": "Orden del juzgado 12", "acta_ref": "A"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code in (404, 405)


def test_levantar_ddhh_exige_el_acto_de_la_autoridad_explicado() -> None:
    """DDHH / memoria histórica (Ley 1448/2011 art. 144 [confirmar]): no la levanta el paso del
    tiempo ni una valoración; el fundamento del levantamiento exige holgura."""
    import pytest
    from pydantic import ValidationError

    from app.schemas.retencion_legal import LegalHoldFijarRequest, LegalHoldLevantarRequest

    LegalHoldFijarRequest(
        motivo_tipo="ddhh_memoria_historica", fundamento="Orden de preservación de archivos", acta_ref="A-1"
    )
    with pytest.raises(ValidationError):
        LegalHoldLevantarRequest(motivo_tipo="ddhh_memoria_historica", fundamento="x" * 20, acta_ref="A-2")
    LegalHoldLevantarRequest(motivo_tipo="ddhh_memoria_historica", fundamento="x" * 50, acta_ref="A-2")
    LegalHoldLevantarRequest(motivo_tipo="judicial", fundamento="x" * 10, acta_ref="A-2")
