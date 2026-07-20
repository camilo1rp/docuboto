# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Delta de remediación: `GET /expedientes/indices/pendientes-renovacion`
(visibilidad operacional de artefactos WORM de series CT en riesgo/agotados
de renovación, E10 debt) -- gemela de
`GET /expedientes/indices/pendientes-firma` (`test_indice_reconciliation.py`,
PARTE 5) para el eje de RENOVACIÓN.

Pure-unit (pool/conexiones mockeados): delegación del servicio a AMBOS
repositorios (`IndexRepository`/`TransferenciaRepository`), combinación +
orden (agotados primero, luego vencimiento más próximo) + paginación en
MEMORIA, y el gate RBAC (D-08, `USUA_PERM_EXPEDIENTE`) del router. El
INVARIANTE no-read-up (que el `WHERE nivel_seguridad <= $max_clearance` vive
de verdad en el SQL de cada repositorio, no solo en el mock) se prueba aparte
contra Postgres real en
`tests/integration/test_worm_renovacion_visibilidad.py` -- un mock de
repositorio nunca ejecuta un `WHERE`."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.disposition import CT_DISPOSITION_CODES
from app.repositories.index_repository import IndexRepository
from app.repositories.transferencia_repository import TransferenciaRepository
from app.services.index_service import IndexService
from tests.conftest import TENANT_HEADERS

TENANT = "demo"
_CT_CODES = list(CT_DISPOSITION_CODES)


def _mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _visibilidad_row(**overrides) -> dict:
    base = {
        "expediente_id": uuid.uuid4(),
        "expediente_code": "EXP-CT-0001",
        "worm_retain_until": datetime.now(tz=UTC) + timedelta(days=30),
        "worm_renovacion_intentos": 1,
        "worm_renovacion_agotado": False,
        "worm_renovacion_ultimo_error": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# IndexService.list_pendientes_renovacion — delegación + no-read-up (resuelve
# clearance UNA vez, la propaga a AMBOS repositorios).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pendientes_renovacion_resolves_clearance_once_and_delegates_to_both_repos() -> None:
    conn = _mock_conn()
    svc = IndexService(conn, TENANT)
    user_id = uuid.uuid4()

    with (
        patch.object(svc._archive, "caller_clearance", new=AsyncMock(return_value=2)) as mock_clearance,
        patch.object(
            IndexRepository, "list_worm_renovacion_visibilidad", new=AsyncMock(return_value=[])
        ) as mock_indice,
        patch.object(
            TransferenciaRepository,
            "list_worm_renovacion_visibilidad",
            new=AsyncMock(return_value=[]),
        ) as mock_acta,
    ):
        items, total = await svc.list_pendientes_renovacion(page=1, size=20, user_id=user_id)

    assert items == []
    assert total == 0
    mock_clearance.assert_awaited_once_with(user_id)
    # AMBOS repositorios reciben el MISMO clearance (no re-resuelto por rama)
    # y la MISMA ventana/lista de códigos CT.
    mock_indice.assert_awaited_once_with(max_clearance=2, ct_codes=_CT_CODES, ventana_dias=400)
    mock_acta.assert_awaited_once_with(max_clearance=2, ct_codes=_CT_CODES, ventana_dias=400)


@pytest.mark.asyncio
async def test_list_pendientes_renovacion_tags_tipo_indice_y_acta_transferencia() -> None:
    conn = _mock_conn()
    svc = IndexService(conn, TENANT)
    indice_row = _visibilidad_row(expediente_code="EXP-IND-0001")
    acta_row = _visibilidad_row(expediente_code="EXP-ACT-0001")

    with (
        patch.object(svc._archive, "caller_clearance", new=AsyncMock(return_value=3)),
        patch.object(
            IndexRepository,
            "list_worm_renovacion_visibilidad",
            new=AsyncMock(return_value=[indice_row]),
        ),
        patch.object(
            TransferenciaRepository,
            "list_worm_renovacion_visibilidad",
            new=AsyncMock(return_value=[acta_row]),
        ),
    ):
        items, total = await svc.list_pendientes_renovacion(page=1, size=20, user_id=uuid.uuid4())

    assert total == 2
    by_code = {it.expediente_code: it.tipo for it in items}
    assert by_code["EXP-IND-0001"] == "indice"
    assert by_code["EXP-ACT-0001"] == "acta_transferencia"


# ---------------------------------------------------------------------------
# Orden: agotados primero, luego vencimiento más próximo -- SIN importar de
# qué repositorio (índice/acta) ni en qué orden llegó cada fila.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pendientes_renovacion_orders_agotados_first_then_soonest() -> None:
    conn = _mock_conn()
    svc = IndexService(conn, TENANT)
    now = datetime.now(tz=UTC)

    lejos = _visibilidad_row(
        expediente_code="EXP-LEJOS", worm_retain_until=now + timedelta(days=300), worm_renovacion_agotado=False
    )
    proximo = _visibilidad_row(
        expediente_code="EXP-PROXIMO", worm_retain_until=now + timedelta(days=5), worm_renovacion_agotado=False
    )
    agotado_acta = _visibilidad_row(
        expediente_code="EXP-AGOTADO-ACTA",
        worm_retain_until=now + timedelta(days=200),  # lejos, pero agotado siempre gana
        worm_renovacion_agotado=True,
    )

    # Deliberadamente en un orden "desordenado" desde cada repositorio.
    with (
        patch.object(svc._archive, "caller_clearance", new=AsyncMock(return_value=3)),
        patch.object(
            IndexRepository,
            "list_worm_renovacion_visibilidad",
            new=AsyncMock(return_value=[lejos, proximo]),
        ),
        patch.object(
            TransferenciaRepository,
            "list_worm_renovacion_visibilidad",
            new=AsyncMock(return_value=[agotado_acta]),
        ),
    ):
        items, total = await svc.list_pendientes_renovacion(page=1, size=20, user_id=uuid.uuid4())

    assert total == 3
    assert [it.expediente_code for it in items] == ["EXP-AGOTADO-ACTA", "EXP-PROXIMO", "EXP-LEJOS"]


@pytest.mark.asyncio
async def test_list_pendientes_renovacion_agotado_with_null_retain_until_does_not_crash_sort() -> None:
    """`worm_retain_until` NULL solo debería darse en teoría junto a
    `agotado=TRUE` legado -- `datetime.max` evita que ese caso reviente el
    `sort` o se cuele al frente por delante de un agotado con fecha real."""
    conn = _mock_conn()
    svc = IndexService(conn, TENANT)
    now = datetime.now(tz=UTC)

    agotado_sin_fecha = _visibilidad_row(
        expediente_code="EXP-AGOTADO-NULL", worm_retain_until=None, worm_renovacion_agotado=True
    )
    agotado_con_fecha = _visibilidad_row(
        expediente_code="EXP-AGOTADO-FECHA",
        worm_retain_until=now + timedelta(days=1),
        worm_renovacion_agotado=True,
    )

    with (
        patch.object(svc._archive, "caller_clearance", new=AsyncMock(return_value=3)),
        patch.object(
            IndexRepository,
            "list_worm_renovacion_visibilidad",
            new=AsyncMock(return_value=[agotado_sin_fecha, agotado_con_fecha]),
        ),
        patch.object(
            TransferenciaRepository,
            "list_worm_renovacion_visibilidad",
            new=AsyncMock(return_value=[]),
        ),
    ):
        items, total = await svc.list_pendientes_renovacion(page=1, size=20, user_id=uuid.uuid4())

    assert total == 2
    # El agotado CON fecha real va antes del agotado SIN fecha (NULL tratado
    # como "el vencimiento más lejano posible", nunca al frente).
    assert [it.expediente_code for it in items] == ["EXP-AGOTADO-FECHA", "EXP-AGOTADO-NULL"]


# ---------------------------------------------------------------------------
# Paginación en memoria: page/size + total = longitud COMBINADA antes de
# recortar (no el tamaño de la página devuelta).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pendientes_renovacion_paginates_in_memory_after_merge() -> None:
    conn = _mock_conn()
    svc = IndexService(conn, TENANT)
    now = datetime.now(tz=UTC)
    indice_rows = [
        _visibilidad_row(expediente_code=f"EXP-IDX-{i}", worm_retain_until=now + timedelta(days=i))
        for i in range(3)
    ]
    acta_rows = [
        _visibilidad_row(expediente_code=f"EXP-ACT-{i}", worm_retain_until=now + timedelta(days=10 + i))
        for i in range(2)
    ]

    with (
        patch.object(svc._archive, "caller_clearance", new=AsyncMock(return_value=3)),
        patch.object(
            IndexRepository,
            "list_worm_renovacion_visibilidad",
            new=AsyncMock(return_value=indice_rows),
        ),
        patch.object(
            TransferenciaRepository,
            "list_worm_renovacion_visibilidad",
            new=AsyncMock(return_value=acta_rows),
        ),
    ):
        page1, total1 = await svc.list_pendientes_renovacion(page=1, size=2, user_id=uuid.uuid4())
        page3, total3 = await svc.list_pendientes_renovacion(page=3, size=2, user_id=uuid.uuid4())

    assert total1 == 5
    assert total3 == 5
    assert len(page1) == 2
    assert len(page3) == 1  # 5 items, size=2 -> última página con el resto


# ---------------------------------------------------------------------------
# Router: gate RBAC (D-08), X-Total-Count, shape de respuesta.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_indices_pendientes_renovacion_endpoint_sets_total_count_header(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.return_value = {"is_root": False, "max_level": 1}
    mock_conn.fetch.return_value = []

    with (
        patch.object(
            IndexRepository, "list_worm_renovacion_visibilidad", new=AsyncMock(return_value=[])
        ),
        patch.object(
            TransferenciaRepository,
            "list_worm_renovacion_visibilidad",
            new=AsyncMock(return_value=[]),
        ),
    ):
        resp = await client.get(
            "/api/v1/expedientes/indices/pendientes-renovacion", headers=TENANT_HEADERS
        )

    assert resp.status_code == 200
    assert resp.json() == []
    assert resp.headers["X-Total-Count"] == "0"


@pytest.mark.asyncio
async def test_get_indices_pendientes_renovacion_endpoint_returns_expected_shape(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = {"is_root": False, "max_level": 3}
    row = _visibilidad_row(expediente_id=exp_id, expediente_code="EXP-CT-9999", worm_renovacion_agotado=True)

    with (
        patch.object(
            IndexRepository, "list_worm_renovacion_visibilidad", new=AsyncMock(return_value=[row])
        ),
        patch.object(
            TransferenciaRepository,
            "list_worm_renovacion_visibilidad",
            new=AsyncMock(return_value=[]),
        ),
    ):
        resp = await client.get(
            "/api/v1/expedientes/indices/pendientes-renovacion", headers=TENANT_HEADERS
        )

    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "1"
    body = resp.json()
    assert len(body) == 1
    item = body[0]
    assert item["expediente_id"] == str(exp_id)
    assert item["expediente_code"] == "EXP-CT-9999"
    assert item["tipo"] == "indice"
    assert item["worm_renovacion_agotado"] is True


@pytest.mark.asyncio
async def test_pendientes_renovacion_sin_user_id_es_rechazado_por_el_gate_rbac(
    client_with_conn,
) -> None:
    """Sin X-User-Id, `require_permission` (D-08) responde 403 fail-closed
    ANTES de resolver clearance/consultar los repositorios -- mismo
    comportamiento que `pendientes-firma`
    (`test_indice_reconciliation.py`)."""
    client, mock_conn = client_with_conn

    resp = await client.get(
        "/api/v1/expedientes/indices/pendientes-renovacion",
        headers={"X-Tenant-Slug": TENANT},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_pendientes_renovacion_permiso_insuficiente_es_rechazado_por_el_gate_rbac(
    client_with_conn,
) -> None:
    """Con X-User-Id pero SIN `USUA_PERM_EXPEDIENTE`, el gate D-08 responde
    403 antes de que el servicio toque `caller_clearance` ni los
    repositorios de renovación."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # D-08: permiso denegado

    with (
        patch.object(
            IndexRepository, "list_worm_renovacion_visibilidad", new=AsyncMock(return_value=[])
        ) as mock_indice,
        patch.object(
            TransferenciaRepository,
            "list_worm_renovacion_visibilidad",
            new=AsyncMock(return_value=[]),
        ) as mock_acta,
    ):
        resp = await client.get(
            "/api/v1/expedientes/indices/pendientes-renovacion", headers=TENANT_HEADERS
        )

    assert resp.status_code == 403
    mock_indice.assert_not_called()
    mock_acta.assert_not_called()
