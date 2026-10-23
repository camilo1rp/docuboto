# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""C-4/C10 (ADR-040): publicar de oficio depende de la marca PROPIA del tipo documental.

Contrato HTTP con la conexión mockeada. Lo que un mock no puede decir —que el `EXISTS` filtra de
verdad por tipo activo/publicable, que un radicado sin tipo no se publica, que el retiro no
depende del tipo— está en `tests/integration/test_t13_cumplimiento_integration.py`."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from tests.conftest import TENANT_HEADERS

_USER = str(uuid.uuid4())
_CUERPO = {"publicar": True, "fundamento": "Esquema de publicación 2026, numeral 4.2"}


def _clearance_row() -> dict:
    return {"is_root": False, "max_level": 3}


def _sql_del_update(mock_conn) -> str:
    return next(
        str(c[0][0])
        for c in mock_conn.fetchrow.call_args_list
        if "UPDATE radicados" in str(c[0][0]) and "publicable" in str(c[0][0])
    )


def _gate_concedido_y_diagnostico(mock_conn, *, solo_falla_el_tipo: bool) -> None:
    """RBAC concede (`auth_users`); la comprobación diagnóstica (`FROM radicados`) responde
    lo que se pida."""

    async def _fetchval(sql, *args, **kw):
        if "FROM radicados" in sql:
            return solo_falla_el_tipo
        return True

    mock_conn.fetchval.side_effect = _fetchval


@pytest.mark.asyncio
async def test_si_lo_unico_que_falta_es_el_tipo_responde_409_y_no_404(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    _gate_concedido_y_diagnostico(mock_conn, solo_falla_el_tipo=True)
    mock_conn.fetchrow.side_effect = [_clearance_row(), None]  # el UPDATE no afectó filas

    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/publicacion",
        json=_CUERPO,
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "tipo_documental_no_publicable"
    # el acto no se asentó: no ocurrió
    ejecutadas = " ".join(str(c) for c in mock_conn.execute.call_args_list)
    assert "publicacion_activada" not in ejecutadas


@pytest.mark.asyncio
async def test_cualquier_otro_motivo_conserva_el_404_neutro(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Reservado, anulado, de ventanilla, inexistente o que supera el clearance: el diagnóstico
    repite ese gate y responde que no es solo el tipo → 404 `document_not_found`."""
    client, mock_conn = client_with_conn
    _gate_concedido_y_diagnostico(mock_conn, solo_falla_el_tipo=False)
    mock_conn.fetchrow.side_effect = [_clearance_row(), None]

    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/publicacion",
        json=_CUERPO,
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "document_not_found"


@pytest.mark.asyncio
async def test_el_diagnostico_repite_el_gate_de_clearance_nivel_anulado_y_canal(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Si el diagnóstico olvidara una de estas condiciones, el 409 sería un oráculo: confirmaría
    que un radicado reservado/anulado/ajeno EXISTE. Se fija su SQL, condición por condición."""
    client, mock_conn = client_with_conn
    _gate_concedido_y_diagnostico(mock_conn, solo_falla_el_tipo=False)
    mock_conn.fetchrow.side_effect = [_clearance_row(), None]

    await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/publicacion",
        json=_CUERPO,
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    diag = next(
        str(c[0][0])
        for c in mock_conn.fetchval.call_args_list
        if "FROM radicados" in str(c[0][0])
    )
    assert "nivel_seguridad <= $2" in diag, "clearance del actor"
    assert "nivel_seguridad = 1" in diag, "solo lo público"
    assert "'anulado'" in diag, "no anulado"
    assert "ventanilla_publica" in diag, "canal"
    assert "tipos_documentales" not in diag, "la condición del tipo es justo la que NO se repite"


@pytest.mark.asyncio
async def test_retirar_no_pregunta_por_el_tipo(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Fail-safe: ni el UPDATE del retiro lleva la condición del tipo ni, si no afecta filas, se
    emite un 409 de tipo (un retiro nunca falla por la configuración del tipo)."""
    client, mock_conn = client_with_conn
    _gate_concedido_y_diagnostico(mock_conn, solo_falla_el_tipo=True)
    mock_conn.fetchrow.side_effect = [_clearance_row(), None]

    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/publicacion",
        json={"publicar": False, "fundamento": "Retiro por decisión del comité"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert "tipos_documentales" not in _sql_del_update(mock_conn)
    assert resp.status_code == 404  # sin el diagnóstico de tipo: no era un problema de tipo


@pytest.mark.asyncio
async def test_el_update_exige_un_tipo_activo_y_publicable_con_exists(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {"tracking_number": "T", "publicable": True, "publicado_at": None},
    ]

    await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/publicacion",
        json=_CUERPO,
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    update = _sql_del_update(mock_conn)
    assert " AND EXISTS (SELECT 1 FROM tipos_documentales t" in update
    assert "NOT EXISTS" not in update
    assert "t.code = radicados.doc_class" in update
    assert "t.activo" in update and "t.publicable" in update
    assert "admite_ventanilla_publica" not in update, "la ventanilla ya no excluye la publicación"
    assert "COALESCE(metadata->>'canal', '') <> 'ventanilla_publica'" in update, "el canal se conserva"
