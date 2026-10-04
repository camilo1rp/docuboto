# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""
AREA-04-10 — ``POST /verify`` y ``GET /firmas/{id}`` aplican el no-read-up.

Cualquier usuario autenticado de la entidad, sin clearance, distinguía 404 de 200
para el ``firma_id`` de un acto reservado. Ahora la firma de un objeto por encima
del clearance del llamante da el MISMO 404 que un UUID inexistente, con el nivel
DENTRO del ``WHERE`` de la consulta (no una comprobación posterior).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import asyncpg
import pytest

from app.repositories.firma_repository import FirmaRepository
from tests.conftest import TENANT_HEADERS, USER
from tests.test_signature import _firma_row

CLEARANCE_1 = {"is_root": False, "max_level": 1}


def _consulta_de_firma(conn: AsyncMock) -> tuple[str, tuple]:
    """La SELECT de la firma (la que lleva ``f.id``), con sus argumentos."""
    for c in conn.fetchrow.await_args_list:
        if "FROM firma f" in c.args[0]:
            return c.args[0], c.args[1:]
    raise AssertionError("no se consultó la firma")


@pytest.mark.asyncio
async def test_verify_no_revela_firma_de_objeto_reservado(client_with_conn) -> None:
    """Clearance 1 y firma sobre un objeto de nivel 3: 404 idéntico al de un UUID
    inexistente. El nivel va en el WHERE (la consulta ya no devuelve la fila), así
    que ni siquiera hay un SELECT que el código tenga que «olvidar»."""
    client, conn = client_with_conn
    firma_id = uuid.uuid4()
    # 1) clearance del llamante; 2) la firma — que el WHERE descarta (objeto nivel 3).
    conn.fetchrow.side_effect = [CLEARANCE_1, None]

    reservado = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(firma_id), "payload_xml": "<x/>"},
        headers=TENANT_HEADERS,
    )

    sql, args = _consulta_de_firma(conn)
    assert "o.nivel_seguridad > $2" in sql  # el gate está en la consulta
    assert args == (firma_id, 1)

    conn.fetchrow.side_effect = [CLEARANCE_1, None]
    inexistente = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(firma_id), "payload_xml": "<x/>"},
        headers=TENANT_HEADERS,
    )
    assert reservado.status_code == inexistente.status_code == 404
    assert reservado.json() == inexistente.json()
    assert reservado.json()["detail"]["error"] == "firma_not_found"


@pytest.mark.asyncio
async def test_verify_consulta_lleva_el_gate_para_radicados_y_expedientes(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.side_effect = [CLEARANCE_1, None]
    await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(uuid.uuid4()), "payload_xml": "<x/>"},
        headers=TENANT_HEADERS,
    )
    sql, _ = _consulta_de_firma(conn)
    assert "FROM radicados o" in sql and "FROM expedientes o" in sql
    assert "o.tracking_number = f.objeto_id" in sql and "o.code = f.objeto_id" in sql
    assert "o.id::text = f.objeto_id" in sql


@pytest.mark.asyncio
async def test_verify_objeto_visible_sigue_verificando(client_with_conn) -> None:
    """Control positivo: dentro del clearance, la firma se verifica como siempre."""
    client, conn = client_with_conn
    row = _firma_row(b"<indice/>", USER)
    conn.fetchrow.side_effect = [CLEARANCE_1, row]
    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(row["id"]), "payload_xml": "<indice/>"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["valida"] is True


@pytest.mark.asyncio
async def test_verify_de_servicio_a_servicio_sin_usuario_no_aplica_el_gate(
    client_with_conn,
) -> None:
    """archive-service verifica el sellado de un índice solo con ``X-Tenant-Slug``:
    no hay clearance que aplicar (y la consulta no lleva el filtro)."""
    client, conn = client_with_conn
    row = _firma_row(b"<indice/>", USER)
    conn.fetchrow.side_effect = [row]
    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(row["id"]), "payload_xml": "<indice/>"},
        headers={"X-Tenant-Slug": "demo"},
    )
    assert resp.status_code == 200
    sql, args = _consulta_de_firma(conn)
    assert "nivel_seguridad" not in sql
    assert args == (row["id"],)


@pytest.mark.asyncio
async def test_get_firma_aplica_el_mismo_gate(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.side_effect = [CLEARANCE_1, None]
    resp = await client.get(
        f"/api/v1/signature/firmas/{uuid.uuid4()}", headers=TENANT_HEADERS
    )
    assert resp.status_code == 404
    sql, args = _consulta_de_firma(conn)
    assert "o.nivel_seguridad > $2" in sql and args[1] == 1


@pytest.mark.asyncio
async def test_gate_tolera_schema_sin_la_tabla_de_expedientes() -> None:
    """Un tenant sin archive-service no tiene ``expedientes``: se descarta SOLO esa
    fuente, no se pierde el gate de las demás ni la verificación."""
    conn = AsyncMock()
    conn.fetchval.return_value = False  # sin auth_users.username
    fila = {"id": uuid.uuid4()}
    conn.fetchrow.side_effect = [asyncpg.UndefinedTableError("expedientes"), fila]
    conn.fetch.return_value = [{"table_name": "radicados"}]

    row = await FirmaRepository(conn).get(fila["id"], max_clearance=1)

    assert row == fila
    segunda = conn.fetchrow.await_args_list[1].args[0]
    assert "FROM radicados o" in segunda and "expedientes" not in segunda
