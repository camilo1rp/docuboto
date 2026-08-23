# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cola del aprobador de anulaciones (`GET /api/v1/anulaciones`).

No existía: la pantalla del aprobador solo podía consultar el estado *de un
radicado concreto*, así que obligaba a buscar radicado por radicado y declaraba
la limitación en un banner permanente. Una cola de trabajo que obliga a adivinar
su propio contenido no es una cola.

Lo que estos tests fijan, en orden de importancia:

1. El gate es el MISMO que decidir (`PERM_PANU_CODI`). Ofrecer una cola cuyos
   actos luego se rechazan es el defecto que el barrido negativo fue a buscar.
2. El **no-read-up alcanza al recuento**, no solo a las filas. Es el punto
   delicado: contar sin filtrar diría «hay 7» mostrando 5, y esa diferencia es
   exactamente la existencia de dos solicitudes sobre radicados clasificados.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from tests.conftest import TENANT_HEADERS

RUTA = "/api/v1/anulaciones"


def _clearance_row(max_level: int = 3, is_root: bool = False) -> dict:
    """`resolve_caller_clearance` lee por `fetchrow`, no por `fetchval`.

    Sin esta fila el clearance cae a 1 (Pública, fail-closed) y el test pasaría
    sin haber ejercido el nivel que dice ejercer.
    """
    return {"is_root": is_root, "max_level": max_level}


def _fila(*, tracking: str, nivel_visible: bool = True) -> dict:
    return {
        "id": uuid.uuid4(),
        "radicado_id": uuid.uuid4(),
        "estado": "solicitada",
        "causal": "error_digitacion",
        "motivo": "Se radicó por duplicado",
        "solicitado_por": uuid.uuid4(),
        "solicitado_at": datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
        "tracking_number": tracking,
        "subject": "Solicitud de certificación laboral",
        "doc_type": "S",
        "radicado_status": "radicado",
    }


@pytest.mark.asyncio
async def test_sin_permiso_de_aprobacion_devuelve_403(client_with_conn) -> None:
    """`fetchval=None` es el fail-closed real del gate (ver CLAUDE.md del servicio)."""
    client, conn = client_with_conn
    conn.fetchval.return_value = None

    resp = await client.get(RUTA, headers=TENANT_HEADERS)

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_lista_pendientes_con_recuento_en_la_cabecera(client_with_conn) -> None:
    client, conn = client_with_conn
    filas = [_fila(tracking="2026-DEMO-S-000012"), _fila(tracking="2026-DEMO-S-000013")]
    conn.fetch.return_value = filas
    conn.fetchrow.return_value = _clearance_row()
    # Solo DOS `fetchval`: el gate (1 = concedido) y el COUNT de la cola.
    conn.fetchval.side_effect = [1, 2]

    resp = await client.get(RUTA, headers=TENANT_HEADERS)

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert [c["tracking_number"] for c in cuerpo] == [
        "2026-DEMO-S-000012",
        "2026-DEMO-S-000013",
    ]
    # La cola dice de QUÉ habla: sin asunto y número habría que abrir cada ficha.
    assert cuerpo[0]["subject"] == "Solicitud de certificación laboral"
    assert cuerpo[0]["doc_type"] == "S"
    assert resp.headers["X-Total-Count"] == "2"


@pytest.mark.asyncio
async def test_el_clearance_del_llamante_va_a_las_DOS_consultas(client_with_conn) -> None:
    """El recuento tiene que llevar el MISMO filtro que las filas.

    Si el `COUNT(*)` no lo llevara, `X-Total-Count` delataría cuántas
    solicitudes hay sobre radicados que el llamante no puede ver — que es
    justo lo que el no-read-up impide (RF-SEG-08).
    """
    client, conn = client_with_conn
    conn.fetch.return_value = []
    conn.fetchrow.return_value = _clearance_row(max_level=2)
    conn.fetchval.side_effect = [1, 0]  # gate, count

    resp = await client.get(RUTA, headers=TENANT_HEADERS)
    assert resp.status_code == 200

    # La consulta de filas
    sql_filas, *args_filas = conn.fetch.await_args.args
    assert "nivel_seguridad <= $1" in sql_filas
    assert args_filas[0] == 2  # el clearance REAL del llamante, no el default

    # …y la del recuento, con el mismo predicado y el mismo argumento.
    llamada_count = [
        c for c in conn.fetchval.await_args_list if "COUNT(*)" in str(c.args[0] if c.args else "")
    ]
    assert llamada_count, "el recuento no se ejecutó"
    sql_count, *args_count = llamada_count[0].args
    assert "nivel_seguridad <= $1" in sql_count
    assert args_count[0] == 2


@pytest.mark.asyncio
async def test_solo_solicitudes_sin_decidir_y_la_mas_antigua_primero(client_with_conn) -> None:
    """Una anulación pendiente bloquea el ciclo de vida del documento (art. 28):
    la más antigua es la más urgente, no la más reciente."""
    client, conn = client_with_conn
    conn.fetch.return_value = []
    conn.fetchrow.return_value = _clearance_row()
    conn.fetchval.side_effect = [1, 0]

    await client.get(RUTA, headers=TENANT_HEADERS)

    sql = conn.fetch.await_args.args[0]
    assert "a.estado = 'solicitada'" in sql
    assert "ORDER BY a.solicitado_at ASC" in sql


@pytest.mark.asyncio
async def test_la_paginacion_llega_a_la_consulta(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetch.return_value = []
    conn.fetchrow.return_value = _clearance_row()
    conn.fetchval.side_effect = [1, 0]

    await client.get(f"{RUTA}?page=3&size=10", headers=TENANT_HEADERS)

    _, _, offset, limit = conn.fetch.await_args.args
    assert (offset, limit) == (20, 10)


@pytest.mark.asyncio
async def test_una_pagina_fuera_de_rango_no_es_un_error(client_with_conn) -> None:
    """Vacío legítimo: `page` alta devuelve 200 con lista vacía y el total real,
    no un 404 — el aprobador debe poder ver que la cola tiene 2 aunque pida la
    página 9."""
    client, conn = client_with_conn
    conn.fetch.return_value = []
    conn.fetchrow.return_value = _clearance_row()
    conn.fetchval.side_effect = [1, 2]

    resp = await client.get(f"{RUTA}?page=9", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    assert resp.json() == []
    assert resp.headers["X-Total-Count"] == "2"


@pytest.mark.asyncio
async def test_no_audita_la_lectura_de_la_cola(client_with_conn, monkeypatch) -> None:
    """La cola es una lectura de trabajo, no un acceso a un documento.

    Las lecturas de radicados sí dejan traza (RF-BUS-10), pero esta no lee
    contenido de un documento concreto: es el tablero del rol. Auditarla
    metería una fila por refresco de pantalla en la cadena de hashes, que es
    justo el ruido que ya se retiró una vez del `audit_log`.
    """
    from app.services import anulacion_service

    espia = AsyncMock()
    if hasattr(anulacion_service, "audit"):
        monkeypatch.setattr(anulacion_service.audit, "append", espia, raising=False)

    client, conn = client_with_conn
    conn.fetch.return_value = []
    conn.fetchrow.return_value = _clearance_row()
    conn.fetchval.side_effect = [1, 0]

    resp = await client.get(RUTA, headers=TENANT_HEADERS)

    assert resp.status_code == 200
    espia.assert_not_awaited()
