# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Contrato HTTP de los filtros nuevos de `GET /api/v1/documents`.

Lo que las cláusulas SQL seleccionan se prueba contra Postgres real
(`tests/integration/test_el_vencido_antiguo_es_alcanzable_integration.py`).
Aquí se mide lo otro, que es distinto y también falla solo: que la URL llegue
entera hasta la consulta. Es el tramo donde vivía el defecto del gateway —un
`dict()` sobre un multidict se quedaba con el último `status`— y donde un
parámetro mal declarado se convierte en silencio en «no se filtró nada».

Por eso se inspecciona el **SQL emitido y sus argumentos**, no la respuesta: una
lista de radicados devuelta por un doble no dice nada sobre qué se consultó.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from tests.test_documents import TENANT_HEADERS, make_radicado_record


def _sql_y_args(mock_conn: AsyncMock) -> tuple[str, tuple]:
    """El SELECT de filas: qué se pidió y con qué valores."""
    return mock_conn.fetch.call_args[0][0], mock_conn.fetch.call_args[0][1:]


@pytest.mark.asyncio
async def test_status_repetido_cruza_con_todos_sus_valores(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """`?status=a&status=b` llega como lista, no como su último valor."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 0
    mock_conn.fetch.return_value = []

    resp = await client.get(
        "/api/v1/documents?status=distributed&status=in_progress", headers=TENANT_HEADERS
    )

    assert resp.status_code == 200
    sql, args = _sql_y_args(mock_conn)
    assert "= ANY(" in sql
    assert ["distributed", "in_progress"] in args


@pytest.mark.asyncio
async def test_un_solo_status_sigue_llegando(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Control positivo: la forma que usan hoy las pantallas no se rompió.

    Sin esto, el test de arriba pasaría igual con un endpoint que ignorase
    `status` por completo — no filtrar nunca satisface «no se quedó con el
    último».
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 0
    mock_conn.fetch.return_value = []

    await client.get("/api/v1/documents?status=closed", headers=TENANT_HEADERS)

    _, args = _sql_y_args(mock_conn)
    assert ["closed"] in args


@pytest.mark.asyncio
async def test_el_rango_de_plazo_viaja_como_fecha(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """`due_from`/`due_to` llegan tipados, no como el texto de la URL.

    Importa porque `due_date` es `DATE` en la base: una cadena obligaría a un
    cast implícito y una mal formada se convertiría en un error del motor a
    mitad de consulta en vez de un 422 en la puerta.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 0
    mock_conn.fetch.return_value = []

    resp = await client.get(
        "/api/v1/documents?due_from=2026-01-01&due_to=2026-03-31", headers=TENANT_HEADERS
    )

    assert resp.status_code == 200
    _, args = _sql_y_args(mock_conn)
    assert date(2026, 1, 1) in args
    assert date(2026, 3, 31) in args


@pytest.mark.asyncio
async def test_una_fecha_ilegible_se_rechaza_en_la_puerta(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """`?due_to=ayer` es 422, no una consulta sin filtro.

    El modo de fallo que se evita es el peor de todos: aceptar el parámetro,
    descartarlo y devolver 200 con la lista entera — un informe de vencimientos
    que en realidad no filtró nada, y sin nada que lo delate.
    """
    client, _ = client_with_conn

    resp = await client.get("/api/v1/documents?due_to=ayer", headers=TENANT_HEADERS)

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_el_orden_por_plazo_llega_a_la_consulta(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 0
    mock_conn.fetch.return_value = []

    resp = await client.get(
        "/api/v1/documents?order_by=due_date&order_dir=asc", headers=TENANT_HEADERS
    )

    assert resp.status_code == 200
    sql, _ = _sql_y_args(mock_conn)
    assert "ORDER BY due_date ASC NULLS LAST" in sql


@pytest.mark.asyncio
async def test_un_orden_inventado_se_rechaza(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """El catálogo de órdenes es CERRADO, y esto es lo que lo hace una frontera.

    `ORDER BY` no admite parámetro enlazado: lo que llegue por la URL acabaría
    concatenado en el SQL. Que el nombre de columna sea inadmisible en la puerta
    es lo que impide que llegue a serlo.
    """
    client, _ = client_with_conn

    resp = await client.get("/api/v1/documents?order_by=subject", headers=TENANT_HEADERS)

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_sin_parametros_nuevos_la_consulta_no_cambia(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Control de regresión: el llamante que no sabe de esto ve lo de siempre."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [make_radicado_record()]

    resp = await client.get("/api/v1/documents", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    sql, _ = _sql_y_args(mock_conn)
    assert "ORDER BY registered_at DESC" in sql
    assert "due_date >=" not in sql
    assert "due_date <=" not in sql
