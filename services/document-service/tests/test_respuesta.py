# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de respuesta rápida (antecedente Entrada↔Salida, paridad legado)."""

import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS


@pytest.mark.asyncio
async def test_crear_respuesta_antecedente_no_existe(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None  # get_by_id(entrada) → None
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/respuesta",
        json={"subject": "Respuesta"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "document_not_found"


@pytest.mark.asyncio
async def test_crear_respuesta_sin_permiso_403(client_with_conn) -> None:
    """D-06: PERM_RESPUESTA_VINCULADA requerido; fetchval None → sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None  # no row in auth_users → no permission
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/respuesta",
        json={"subject": "RE: Solicitud"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_crear_respuesta_antecedente_sobre_clearance_404(client_with_conn) -> None:
    """No-read-up (RF-SEG-08): responder exige poder VER el antecedente.

    No es simetría formal: la Salida hereda el `subject` del antecedente
    (`RE: {subject}`) y su `tracking_number` en `metadata.antecedente`, así que
    sin gate responder a una Entrada reservada exfiltraba su asunto a un
    documento legible. El 404 es el mismo que el de antecedente inexistente.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # PERM_RESPUESTA_VINCULADA concedido
    # clearance PUBLICA del llamante; el get_by_id gated no encuentra la Entrada.
    mock_conn.fetchrow.side_effect = [{"is_root": False, "max_level": 1}, None]

    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/respuesta",
        json={"subject": "RE: intento"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "document_not_found"

    # El clearance REAL viaja al SELECT del antecedente (no un valor abierto).
    sql, *params = mock_conn.fetchrow.call_args[0]
    assert "nivel_seguridad <= $2" in sql
    assert params[1] == 1

    # Y no se llegó a crear ninguna Salida.
    consultas = [c[0][0] for c in mock_conn.fetchrow.call_args_list]
    assert not any("INSERT INTO radicados" in q for q in consultas)


@pytest.mark.asyncio
async def test_listar_respuestas(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = [
        {
            "id": uuid.uuid4(),
            "tracking_number": "2024-DEMO-S-000010",
            "doc_type": "S",
            "subject": "RE: Solicitud",
            "nivel_seguridad": 1,
            "status": "registered",
            "registered_at": datetime.now(tz=UTC),
        },
    ]
    resp = await client.get(f"/api/v1/documents/{uuid.uuid4()}/respuestas", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["doc_type"] == "S"
    assert body[0]["tracking_number"] == "2024-DEMO-S-000010"
