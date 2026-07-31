# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Acota por dependencia `GET /workflows/inbox` y `GET /workflows/pending`.

Antes, el único filtro de estos dos listados era el clearance del llamante —
sin `PERM_TRAMITAR`/`USUA_PERM_ADMIN`, cualquier usuario autenticado del
tenant veía la carga de trabajo activa de TODA la entidad (número de
radicado, dependencia origen/destino, asignado, plazos). Ese listado sin
acotar era además la primitiva de enumeración que hacía explotable el
`step_id` ajeno cerrado en `PATCH /{step_id}/complete` (ver CLAUDE.md).

Alcance por defecto (sin `dept`/`assigned_to`): lo propio — `assigned_to =
actor` O `to_dept` de una dependencia a la que el actor pertenece por URD
activa (`_own_scope_condition`, join inline `urd_usuaroldep`/`dependencias`
en el MISMO schema de tenant, sin llamada HTTP a otro servicio). Ampliar el
alcance (`dept`/`assigned_to` explícitos) exige `PERM_TRAMITAR` o
`USUA_PERM_ADMIN` — si no se tiene, se RECHAZA (403), no se ignora en
silencio.

Mismo estilo que `test_bandeja_rf_seg_08.py`: se inspecciona el SQL/params
REALES que llegan a `mock_conn.fetchval`/`fetch` (conexión mockeada, sin
Postgres real) — es la técnica establecida en este servicio para verificar
que un predicado de autorización está en el `WHERE`, no en Python.
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import TENANT_HEADERS, TENANT_SLUG, USER_ID, make_step_record

OTHER_USER_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# (a) Alcance por defecto: control positivo — el tablero sigue vivo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_default_scope_filters_own_condition(client_with_conn) -> None:
    """Sin `dept`/`assigned_to`, el predicado propio (`_own_scope_condition`)
    llega al `WHERE` de COUNT y de SELECT, con el actor como parámetro — el
    tablero por defecto sigue devolviendo resultados (200), no queda en blanco."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [make_step_record(assigned_to=USER_ID)]

    response = await client.get("/api/v1/workflows/pending", headers=TENANT_HEADERS)

    assert response.status_code == 200
    assert len(response.json()) == 1

    count_sql, *count_params = mock_conn.fetchval.call_args[0]
    assert "urd_usuaroldep" in count_sql
    assert "assigned_to = " in count_sql
    assert USER_ID in count_params

    select_sql, *select_params = mock_conn.fetch.call_args[0]
    assert "urd_usuaroldep" in select_sql
    assert USER_ID in select_params


@pytest.mark.asyncio
async def test_inbox_default_scope_filters_own_condition(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [make_step_record(assigned_to=USER_ID)]

    response = await client.get("/api/v1/workflows/inbox", headers=TENANT_HEADERS)

    assert response.status_code == 200
    assert len(response.json()) == 1

    select_sql, *select_params = mock_conn.fetch.call_args[0]
    assert "urd_usuaroldep" in select_sql
    assert USER_ID in select_params


@pytest.mark.asyncio
async def test_pending_default_scope_without_identity_is_fail_closed(client_with_conn) -> None:
    """Sin `X-User-Id`, el parámetro del predicado propio es NULL — ninguna fila
    puede casar `assigned_to = NULL` ni la subquery de dependencias (que
    tampoco encuentra `user_id = NULL`) — fail-closed sin necesitar una rama
    explícita, no rompe la petición (sigue siendo 200)."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 0
    mock_conn.fetch.return_value = []
    headers = {"X-Tenant-Slug": TENANT_SLUG}  # sin X-User-Id

    response = await client.get("/api/v1/workflows/pending", headers=headers)

    assert response.status_code == 200
    assert response.json() == []
    count_sql, *count_params = mock_conn.fetchval.call_args[0]
    assert "urd_usuaroldep" in count_sql
    assert None in count_params  # own_scope_user_id ligado como NULL


# ---------------------------------------------------------------------------
# (b) Ampliar alcance (`dept`/`assigned_to`) exige PERM_TRAMITAR/USUA_PERM_ADMIN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_dept_widening_denied_without_permission(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 0  # check_permission → False (ambos permisos)

    response = await client.get(
        "/api/v1/workflows/pending", params={"dept": "Otra Dependencia"}, headers=TENANT_HEADERS
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"
    # Denegado ANTES de tocar flow_steps: ni COUNT ni SELECT de la bandeja corrieron.
    mock_conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_assigned_to_widening_denied_without_permission(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 0

    response = await client.get(
        "/api/v1/workflows/pending",
        params={"assigned_to": str(OTHER_USER_ID)},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_inbox_dept_widening_denied_without_permission(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 0

    response = await client.get(
        "/api/v1/workflows/inbox", params={"dept": "Otra Dependencia"}, headers=TENANT_HEADERS
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"
    mock_conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_dept_widening_allowed_with_perm_tramitar(client_with_conn) -> None:
    """Con PERM_TRAMITAR (o USUA_PERM_ADMIN), `dept` amplía el alcance como
    antes: SIN el predicado propio de por medio (los dos ejes son mutuamente
    excluyentes por request)."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 1  # check_permission → True, y luego COUNT
    mock_conn.fetch.return_value = [make_step_record(to_dept="Otra Dependencia")]

    response = await client.get(
        "/api/v1/workflows/pending", params={"dept": "Otra Dependencia"}, headers=TENANT_HEADERS
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
    select_sql, *select_params = mock_conn.fetch.call_args[0]
    assert "to_dept = " in select_sql
    assert "urd_usuaroldep" not in select_sql
    assert "Otra Dependencia" in select_params


@pytest.mark.asyncio
async def test_inbox_assigned_to_widening_allowed_with_permission(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [make_step_record(assigned_to=OTHER_USER_ID)]

    response = await client.get(
        "/api/v1/workflows/inbox",
        params={"assigned_to": str(OTHER_USER_ID)},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    select_sql, *select_params = mock_conn.fetch.call_args[0]
    assert "urd_usuaroldep" not in select_sql
    assert OTHER_USER_ID in select_params


# ---------------------------------------------------------------------------
# (c) X-Total-Count coincide con el mismo WHERE que el SELECT (invariante dura)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_default_scope_total_count_matches_filtered_where(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 1  # COUNT ya acotado por el predicado propio
    mock_conn.fetch.return_value = [make_step_record(assigned_to=USER_ID)]

    response = await client.get("/api/v1/workflows/pending", headers=TENANT_HEADERS)

    assert response.status_code == 200
    assert response.headers["x-total-count"] == "1"
    count_sql, *count_params = mock_conn.fetchval.call_args[0]
    select_sql, *select_params = mock_conn.fetch.call_args[0]
    # Mismo WHERE (mismo texto de predicado propio) en COUNT y en SELECT.
    assert "urd_usuaroldep" in count_sql and "urd_usuaroldep" in select_sql
    assert USER_ID in count_params and USER_ID in select_params
