# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Acto de publicación de oficio del EXPEDIENTE (Ley 1712/2014 art. 12).

Por qué existe este módulo: la corrección del art. 12 se aplicó en su día a los
radicados y no aquí, así que el OAI EAD multinivel siguió difundiendo de oficio
todo expediente **no clasificado** — que es el valor por defecto de la columna.
Lo señalaron por separado los dos auditores (seguridad y conformidad) de la
revisión de 2026-08-13. La asimetría era implícita: quien leyera el recorte de
document-service supondría que este servicio hace lo mismo.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from tests.conftest import TENANT_HEADERS

EXP_ID = uuid.uuid4()


def _clearance_row(max_level: int = 3) -> dict:
    return {"is_root": False, "max_level": max_level}


def test_el_recorte_de_expedientes_exige_el_acto_de_publicacion() -> None:
    """El chokepoint del OAI ya no deduce «público» de la ausencia de reserva."""
    from app.repositories.oai_repository import _PUBLIC

    assert "publicable" in _PUBLIC, "el recorte público no exige el acto de publicación"
    assert "nivel_seguridad = 1" in _PUBLIC, "la reserva sigue mandando"


def test_el_recorte_no_despublica_los_expedientes_cerrados() -> None:
    """No se copia la condición de estado de los radicados.

    Un radicado anulado está retirado de la circulación; un expediente `closed` o
    `transferred` no lo está — son estados normales de su ciclo de vida. Copiar
    `status <> ...` por simetría habría despublicado en silencio todo el archivo
    cerrado, que es la mayor parte de un fondo.
    """
    from app.repositories.oai_repository import _PUBLIC

    assert "status" not in _PUBLIC


@pytest.mark.asyncio
async def test_publicar_deja_asiento_con_el_codigo_resuelto_por_el_servidor(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # gate RBAC
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {"code": "EXP-2026-000042", "publicable": True, "publicado_at": None},
    ]

    resp = await client.post(
        f"/api/v1/expedientes/{EXP_ID}/publicacion",
        json={"publicar": True, "fundamento": "Numeral 4.3 del esquema de publicación"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200
    assert resp.json()["publicable"] is True

    # La acción y el `object_ref` viajan como PARÁMETROS del INSERT, no dentro
    # del SQL: mirar solo la consulta daría un test que pasa sin comprobar nada.
    inserts = [c for c in mock_conn.execute.call_args_list if "audit_log" in str(c[0][0])]
    assert inserts, "el acto no dejó asiento"
    args = [str(a) for a in inserts[0][0]]
    assert any("publicacion_activada" in a for a in args), "acción incorrecta en el asiento"
    # El código del expediente sale del RETURNING, nunca del cliente.
    assert "EXP-2026-000042" in args


@pytest.mark.asyncio
async def test_publicar_exige_nivel_publico_en_el_propio_where(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """El gate va en el `WHERE`, no en una comprobación previa: sin ventana TOCTOU."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_clearance_row(), None]

    resp = await client.post(
        f"/api/v1/expedientes/{EXP_ID}/publicacion",
        json={"publicar": True, "fundamento": "x"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "expediente_not_found"
    consultas = [str(c[0][0]) for c in mock_conn.fetchrow.call_args_list]
    update = next(q for q in consultas if "UPDATE expedientes" in q and "publicable" in q)
    assert "nivel_seguridad = 1" in update


@pytest.mark.asyncio
async def test_retirar_no_exige_nivel_publico(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Bloquear el retiro dejaba sin salida al expediente reservado ya publicado."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {"code": "EXP-2026-000042", "publicable": False, "publicado_at": None},
    ]

    await client.post(
        f"/api/v1/expedientes/{EXP_ID}/publicacion",
        json={"publicar": False, "fundamento": "Retiro por reserva sobrevenida"},
        headers=TENANT_HEADERS,
    )

    consultas = [str(c[0][0]) for c in mock_conn.fetchrow.call_args_list]
    update = next(q for q in consultas if "UPDATE expedientes" in q and "publicable" in q)
    assert "nivel_seguridad <= $3" in update, "el clearance sigue siendo obligatorio"
    assert "nivel_seguridad = 1" not in update, "el retiro no debe exigir nivel público"


@pytest.mark.asyncio
async def test_el_fundamento_es_obligatorio_en_ambos_sentidos(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    for publicar in (True, False):
        resp = await client.post(
            f"/api/v1/expedientes/{EXP_ID}/publicacion",
            json={"publicar": publicar, "fundamento": ""},
            headers=TENANT_HEADERS,
        )
        assert resp.status_code == 422, f"sin fundamento debería rechazar (publicar={publicar})"


@pytest.mark.asyncio
async def test_el_acto_exige_la_competencia_sobre_el_regimen_de_acceso(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Mismo permiso y nivel que el gemelo de document-service."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {"code": "EXP-1", "publicable": True, "publicado_at": None},
    ]

    await client.post(
        f"/api/v1/expedientes/{EXP_ID}/publicacion",
        json={"publicar": True, "fundamento": "x"},
        headers=TENANT_HEADERS,
    )

    rbac = [c for c in mock_conn.fetchval.call_args_list if "auth_users" in str(c[0][0])]
    assert rbac, "no se consultó el RBAC"
    assert rbac[0][0][2] == "PERM_RECLASIFICAR"
    assert rbac[0][0][3] == 3
