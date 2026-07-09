# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de transferencias documentales (E12)."""

import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS, make_expediente_record

NOW = datetime.now(tz=UTC)


def _transfer_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "tipo": "primaria",
        "estado": "preparada",
        "expediente_id": uuid.uuid4(),
        "ubicacion_origen_id": None,
        "ubicacion_destino_id": None,
        "creada_por": "ana",
        "observacion": None,
        "created_at": NOW,
        "enviada_at": None,
        "recibida_at": None,
        "decidida_por": None,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_create_requires_closed_expediente(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: permission ok; service raises 409
    mock_conn.fetchrow.return_value = make_expediente_record(exp_status="open")
    mock_conn.fetch.return_value = []

    resp = await client.post(
        "/api/v1/transferencias",
        json={"tipo": "primaria", "expediente_id": str(uuid.uuid4())},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "expediente_not_closed"


@pytest.mark.asyncio
async def test_create_transferencia(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    exp_id = uuid.uuid4()
    # get_expediente (closed) + fetch radicados, insert transferencia
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        _transfer_row(expediente_id=exp_id),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        "/api/v1/transferencias",
        json={"tipo": "primaria", "expediente_id": str(exp_id)},
        headers={**TENANT_HEADERS, "X-Username": "ana"},
    )
    assert resp.status_code == 201
    assert resp.json()["estado"] == "preparada"


@pytest.mark.asyncio
async def test_enviar_then_recibir_freezes_expediente(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    # enviar: get(preparada) → update(enviada)
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="preparada"),
        _transfer_row(id=tid, expediente_id=exp_id, estado="enviada", enviada_at=NOW),
    ]
    env = await client.post(f"/api/v1/transferencias/{tid}/enviar", headers=TENANT_HEADERS)
    assert env.status_code == 200
    assert env.json()["estado"] == "enviada"

    # recibir: get(enviada) → get_current(índice firmado) → update(recibida) →
    # transition_expediente(transferred)
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="enviada"),
        {"id": uuid.uuid4(), "estado": "firmado"},
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida", recibida_at=NOW),
        make_expediente_record(exp_id=exp_id, exp_status="transferred"),
    ]
    mock_conn.fetch.return_value = []
    rec = await client.post(
        f"/api/v1/transferencias/{tid}/recibir",
        headers={**TENANT_HEADERS, "X-Username": "jefe"},
    )
    assert rec.status_code == 200
    assert rec.json()["estado"] == "recibida"


@pytest.mark.asyncio
async def test_recibir_sin_indice_firmado_409(client_with_conn) -> None:
    """E06 Inc.1 / Acuerdo AGN 001/2024 art. 4.3.2.4: la transferencia NO puede
    congelar (`transferred`) un expediente cuyo índice quedó 'pendiente_firma'."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="enviada"),
        {"id": uuid.uuid4(), "estado": "pendiente_firma"},
    ]
    resp = await client.post(
        f"/api/v1/transferencias/{tid}/recibir",
        headers={**TENANT_HEADERS, "X-Username": "jefe"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "indice_no_firmado"


@pytest.mark.asyncio
async def test_recibir_invalid_from_preparada(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: permission ok; service raises 409
    tid = uuid.uuid4()
    mock_conn.fetchrow.return_value = _transfer_row(id=tid, estado="preparada")

    resp = await client.post(f"/api/v1/transferencias/{tid}/recibir", headers=TENANT_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "invalid_status_transition"


@pytest.mark.asyncio
async def test_transferencia_fuid(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    # Secuencia real (RF-SEG-08 V2, no-read-up del FUID single):
    #   1) repo.get(transferencia)
    #   2) caller_clearance (TransferenciaService.fuid) -> None -> PUBLICA(1)
    #   3) get_expediente_by_id -> nivel_seguridad=1 <= PUBLICA -> visible
    #   4) caller_clearance de nuevo (dentro de FisicoService.generate_fuid)
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        None,
    ]
    # fetch: 1) _get_radicados (irrelevante al FUID, forma distinta a la fila
    # del FUID) ; 2) fuid_inventory (las filas reales del FUID).
    mock_conn.fetch.side_effect = [
        [],
        [
            {
                "expediente_id": exp_id,
                "expediente_code": "EXP-2024-0001",
                "asunto": "Contrato",
                "fecha_inicial": NOW,
                "fecha_final": None,
                "serie_code": "100",
                "serie_name": "Contratos",
                "signatura": "DEP01-C0124",
                "unidad_tipo": "caja",
                "soporte": "papel",
                "num_folios": 200,
                "folio_inicio": 1,
                "folio_fin": 200,
            },
        ],
    ]
    resp = await client.get(f"/api/v1/transferencias/{tid}/fuid", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_transferencia_fuid_no_read_up_404(client_with_conn) -> None:
    """RF-SEG-08 V2: el FUID de la transferencia es CONTENIDO del expediente
    (asunto/serie/signatura — Ley 1712/2014 arts. 18-19; Ley 594/2000 art.
    27), no un acto de custodia. Un llamante sin clearance suficiente sobre
    un expediente CLASIFICADA(3) recibe 404 `expediente_not_found`,
    indistinguible de un expediente inexistente (no revela ni existencia ni
    estado). Contraste: el MISMO expediente, con clearance suficiente, sí
    genera el FUID (200) — este test rompe si alguien quita el no-read-up
    del FUID single."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()

    # No-lector: caller_clearance -> PUBLICA(1); get_expediente_by_id no
    # matchea `nivel_seguridad <= max_clearance` sobre el expediente real
    # (CLASIFICADA=3) -> None (la BD real filtra por fila; el mock simula
    # ese resultado, mismo patrón que test_archive.py::
    # test_roles_autorizados_clearance_scoped_returns_404).
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id),
        None,
        None,
    ]
    resp = await client.get(f"/api/v1/transferencias/{tid}/fuid", headers=TENANT_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "expediente_not_found"

    # Mismo expediente/transferencia, caller root (CLASIFICADA=3): sí puede.
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id),
        {"is_root": True, "max_level": None},
        make_expediente_record(exp_id=exp_id, nivel_seguridad=3),
        {"is_root": True, "max_level": None},
    ]
    mock_conn.fetch.side_effect = [
        [],
        [
            {
                "expediente_id": exp_id,
                "expediente_code": "EXP-2024-0001",
                "asunto": "Contrato",
                "fecha_inicial": NOW,
                "fecha_final": None,
                "serie_code": "100",
                "serie_name": "Contratos",
                "signatura": "DEP01-C0124",
                "unidad_tipo": "caja",
                "soporte": "papel",
                "num_folios": 200,
                "folio_inicio": 1,
                "folio_fin": 200,
            },
        ],
    ]
    resp2 = await client.get(f"/api/v1/transferencias/{tid}/fuid", headers=TENANT_HEADERS)
    assert resp2.status_code == 200
    assert resp2.json()["total"] == 1


@pytest.mark.asyncio
async def test_transferencia_fuid_not_found_before_clearance(client_with_conn) -> None:
    """El 404 `transferencia_not_found` sigue resolviéndose ANTES que el
    no-read-up (RF-SEG-08 V2): una transferencia inexistente nunca llega a
    resolver clearance ni a tocar el expediente."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.return_value = None  # repo.get(transferencia_id) -> no existe
    resp = await client.get(f"/api/v1/transferencias/{uuid.uuid4()}/fuid", headers=TENANT_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "transferencia_not_found"


# ---------------------------------------------------------------------------
# D-08: comprobaciones de autorización (USUA_PERM_EXPEDIENTE)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_transferencia_sin_permiso_403(client_with_conn) -> None:
    """check_permission usa fetchval; None → usuario no encontrado → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None  # sin fila en auth_users → sin permiso
    resp = await client.post(
        "/api/v1/transferencias",
        json={"tipo": "primaria", "expediente_id": str(uuid.uuid4())},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_list_transferencias_sin_permiso_403(client_with_conn) -> None:
    """GET también requiere USUA_PERM_EXPEDIENTE."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.get("/api/v1/transferencias", headers=TENANT_HEADERS)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_enviar_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        f"/api/v1/transferencias/{uuid.uuid4()}/enviar",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_recibir_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        f"/api/v1/transferencias/{uuid.uuid4()}/recibir",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_transferencia_fuid_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.get(
        f"/api/v1/transferencias/{uuid.uuid4()}/fuid",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_create_transferencia_con_permiso_201(client_with_conn) -> None:
    """fetchval truthy → permission ok; fetchrow → expediente closed → INSERT."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_EXPEDIENTE granted
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        _transfer_row(expediente_id=exp_id),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        "/api/v1/transferencias",
        json={"tipo": "primaria", "expediente_id": str(exp_id)},
        headers={**TENANT_HEADERS, "X-Username": "ana"},
    )
    assert resp.status_code == 201
    assert resp.json()["estado"] == "preparada"
