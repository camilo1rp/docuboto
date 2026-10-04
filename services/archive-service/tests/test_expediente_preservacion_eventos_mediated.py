# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""GET /api/v1/expedientes/{expediente_id}/preservacion/eventos — lectura
mediada de los eventos PREMIS de preservación (continuación de E10 Increment
C, cierre del no-read-up de `GET /preservacion/eventos` en storage-service,
RF-SEG-08).

Call sequence (ArchiveService.get_eventos_preservacion):
  fetchval — D-08 gate (require_permission)
  fetchrow — resolve_caller_clearance (caller_clearance)
  fetchrow — get_expediente_by_id (visibilidad, filtrada por clearance)
  fetchrow — AipRepository.get_aip_object_id (AREA-07-15: el AIP es un `object_ref` más)
  [document_client.fetch_anexos_internal — solo si hay radicados vigentes]
  [storage_client.list_eventos — HTTP mediado, mockeado aparte]
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.clients.storage_client import StorageEventosError
from tests.conftest import EXPEDIENTE_ID, TENANT_HEADERS, make_expediente_record

_NOW = datetime.now(tz=UTC)


def _evento_payload(*, object_ref: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "object_ref": object_ref,
        "tipo": "FIJACION",
        "resultado": "ok",
        "hash_before": "a" * 64,
        "hash_after": "a" * 64,
        "detalle": {"algoritmo": "SHA-256"},
        "agente": "system",
        "ts": _NOW.isoformat(),
    }


@pytest.mark.asyncio
async def test_get_eventos_sufficient_clearance_returns_eventos(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Clearance CLASIFICADA (root) sobre un expediente CLASIFICADA: se media
    la llamada a storage-service con el UUID del expediente en texto."""
    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=3)
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.side_effect = [
        {"is_root": True, "max_level": None},  # resolve_caller_clearance
        exp,  # get_expediente_by_id (visible: 3 <= 3)
        None,  # get_aip_object_id: sin AIP todavía
    ]
    mock_conn.fetch.return_value = []
    payload = [_evento_payload(object_ref=str(EXPEDIENTE_ID))]

    with patch(
        "app.clients.storage_client.list_eventos", new=AsyncMock(return_value=payload)
    ) as mock_list_eventos:
        response = await client.get(
            f"/api/v1/expedientes/{EXPEDIENTE_ID}/preservacion/eventos",
            headers=TENANT_HEADERS,
        )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["object_ref"] == str(EXPEDIENTE_ID)
    assert body[0]["detalle"]["algoritmo"] == "SHA-256"
    mock_list_eventos.assert_awaited_once_with(
        tenant_slug="demo", object_refs=[str(EXPEDIENTE_ID)]
    )


@pytest.mark.asyncio
async def test_get_eventos_empty_list_is_200_not_404(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Expediente visible sin actividad de preservación todavía: [] es una
    respuesta legítima (200), NO un 404 — a diferencia de `get_aip`, no hay
    aquí un tercer caso "visible pero ausente" que fundir con el no-read-up."""
    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=1)
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [None, exp, None]
    mock_conn.fetch.return_value = []

    with patch(
        "app.clients.storage_client.list_eventos", new=AsyncMock(return_value=[])
    ) as mock_list_eventos:
        response = await client.get(
            f"/api/v1/expedientes/{EXPEDIENTE_ID}/preservacion/eventos",
            headers=TENANT_HEADERS,
        )

    assert response.status_code == 200
    assert response.json() == []
    mock_list_eventos.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_eventos_deja_asiento_de_acceso(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    # La lectura de los eventos (actividad de preservación clasificada) deja
    # pista de acceso (RF-BUS-10) cuando hay canal auditable + actor real.
    from app.services import archive_service as _svc

    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=3)
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [{"is_root": True, "max_level": None}, exp, None]
    mock_conn.fetch.return_value = []
    payload = [_evento_payload(object_ref=str(EXPEDIENTE_ID))]
    append = AsyncMock()

    with (
        patch("app.clients.storage_client.list_eventos", new=AsyncMock(return_value=payload)),
        patch.object(_svc.audit, "append", append),
    ):
        response = await client.get(
            f"/api/v1/expedientes/{EXPEDIENTE_ID}/preservacion/eventos",
            headers={**TENANT_HEADERS, "X-Canal": "ui"},
        )

    assert response.status_code == 200
    append.assert_awaited_once()
    kw = append.await_args.kwargs
    assert kw["action"] == "archive.preservacion_eventos_consultados"
    assert kw["object_type"] == "expediente"
    assert kw["canal"] == "ui"


@pytest.mark.asyncio
async def test_get_eventos_over_clearance_returns_404_neutral(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """RF-SEG-08 no-read-up: llamante PUBLICA (1) sobre un expediente
    CLASIFICADA (3) — 404 neutro, storage-service NUNCA se llama."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08 gate granted
    mock_conn.fetchrow.side_effect = [None, None]

    with patch(
        "app.clients.storage_client.list_eventos", new=AsyncMock()
    ) as mock_list_eventos:
        response = await client.get(
            f"/api/v1/expedientes/{EXPEDIENTE_ID}/preservacion/eventos",
            headers=TENANT_HEADERS,
        )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "expediente_not_found"
    mock_list_eventos.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_eventos_expediente_does_not_exist_returns_404_neutral(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Expediente realmente inexistente — MISMO cuerpo 404 que el caso de
    sobre-clearance (indistinguibles a propósito, no-read-up)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08 gate granted
    mock_conn.fetchrow.side_effect = [{"is_root": True, "max_level": None}, None]

    with patch(
        "app.clients.storage_client.list_eventos", new=AsyncMock()
    ) as mock_list_eventos:
        response = await client.get(
            f"/api/v1/expedientes/{uuid.uuid4()}/preservacion/eventos",
            headers=TENANT_HEADERS,
        )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "expediente_not_found"
    mock_list_eventos.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_eventos_storage_unavailable_returns_503(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Un fallo de storage-service (red/timeout/HTTP inesperado) es ortogonal
    a la autorización: 503, nunca un 404 que lo confundiría con "no
    autorizado"."""
    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=1)
    mock_conn.fetchval.return_value = True  # D-08 gate granted
    mock_conn.fetchrow.side_effect = [None, exp, None]
    mock_conn.fetch.return_value = []

    with patch(
        "app.clients.storage_client.list_eventos",
        new=AsyncMock(side_effect=StorageEventosError("storage-service inalcanzable")),
    ):
        response = await client.get(
            f"/api/v1/expedientes/{EXPEDIENTE_ID}/preservacion/eventos",
            headers=TENANT_HEADERS,
        )

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "storage_unavailable"


@pytest.mark.asyncio
async def test_get_eventos_requires_permission_gate(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """D-08: sin USUA_PERM_EXPEDIENTE, 403 antes de tocar caller_clearance o
    storage-service."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None  # D-08 gate denied

    with patch(
        "app.clients.storage_client.list_eventos", new=AsyncMock()
    ) as mock_list_eventos:
        response = await client.get(
            f"/api/v1/expedientes/{EXPEDIENTE_ID}/preservacion/eventos",
            headers=TENANT_HEADERS,
        )

    assert response.status_code == 403
    mock_list_eventos.assert_not_awaited()


# ---------------------------------------------------------------------------
# AREA-07-15 — los eventos de ficheros y AIP también son del expediente
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_eventos_incluye_el_aip_y_los_file_id_de_los_anexos(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """`list_eventos` recibe el expediente, el id del AIP y los `file_id` de los anexos
    VIGENTES; antes solo el expediente y la pestaña Preservación omitía el resto."""
    from tests.conftest import make_radicado_link_record

    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=1)
    rad_vigente, rad_excluido = uuid.uuid4(), uuid.uuid4()
    aip_id, file_a, file_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [None, exp, {"aip_worm_object_id": aip_id}]
    mock_conn.fetch.return_value = [
        make_radicado_link_record(radicado_id=rad_vigente, estado="vinculado"),
        make_radicado_link_record(radicado_id=rad_excluido, estado="excluido"),
    ]
    anexos = {
        str(rad_vigente): [
            {"file_id": str(file_a), "filename": "a.pdf"},
            {"file_id": str(file_b), "filename": "b.pdf"},
        ]
    }

    with (
        patch("app.clients.document_client.fetch_anexos_internal", new=AsyncMock(return_value=anexos)) as doc,
        patch("app.clients.storage_client.list_eventos", new=AsyncMock(return_value=[])) as lista,
    ):
        response = await client.get(
            f"/api/v1/expedientes/{EXPEDIENTE_ID}/preservacion/eventos",
            headers=TENANT_HEADERS,
        )

    assert response.status_code == 200
    refs = lista.await_args.kwargs["object_refs"]
    assert refs == [str(EXPEDIENTE_ID), str(aip_id), str(file_a), str(file_b)]
    # solo los radicados vigentes, y con el clearance del llamante (sin fuga de existencia)
    assert doc.await_args.kwargs["radicado_ids"] == [rad_vigente]
    assert doc.await_args.kwargs["max_clearance"] == 1


@pytest.mark.asyncio
async def test_get_eventos_con_document_service_caido_es_503_no_una_lista_parcial(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Una lista de eventos sin los de los ficheros presentada como completa es peor que
    un error: fail-closed."""
    from app.clients.document_client import DocumentClientError
    from tests.conftest import make_radicado_link_record

    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=1)
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [None, exp, None]
    mock_conn.fetch.return_value = [make_radicado_link_record(estado="vinculado")]

    with (
        patch(
            "app.clients.document_client.fetch_anexos_internal",
            new=AsyncMock(side_effect=DocumentClientError("caído")),
        ),
        patch("app.clients.storage_client.list_eventos", new=AsyncMock(return_value=[])) as lista,
    ):
        response = await client.get(
            f"/api/v1/expedientes/{EXPEDIENTE_ID}/preservacion/eventos",
            headers=TENANT_HEADERS,
        )

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "document_service_unavailable"
    lista.assert_not_awaited()
