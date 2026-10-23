# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La puerta pública no asocia por número de documento (C17, ADR-038).

Cableado de la ventanilla, con dobles. Que la ficha quede bien en la base lo fija
`integration/test_ficha_provisional_integration.py`. Aquí:

* con documento, `DocumentService.create` recibe `remitente_provisional` (el documento
  declarado, sin consulta previa) y NINGÚN actor ya resuelto;
* el camino público no llama jamás a `get_por_documento` ni a `crear_o_recuperar`
  (mutación: reponer la asociación por número tumba este módulo);
* la respuesta tiene las mismas claves con o sin documento;
* sin documento no hay ficha (denuncia anónima).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.repositories.tercero_repository import TerceroRepository
from app.services.tercero_service import TerceroService
from tests.test_ventanilla import (
    BASE,
    _cuerpo,
    _fetchrow_por_consulta,
    _fetchval_por_consulta,
    _multipart,
    _resultado_radicado,
)


@pytest.fixture
def espias():
    """`get_por_documento` y `crear_o_recuperar` revientan si el camino público los toca."""
    boom = AsyncMock(side_effect=AssertionError("la puerta pública NO asocia por número"))
    with (
        patch.object(TerceroRepository, "get_por_documento", boom),
        patch.object(TerceroService, "crear_o_recuperar", boom),
    ):
        yield boom


async def _radicar(client, conn, **cuerpo):
    conn.fetchval.side_effect = _fetchval_por_consulta("Petición", "cod")
    conn.fetchrow.side_effect = _fetchrow_por_consulta()
    with patch(
        "app.services.document_service.DocumentService.create",
        AsyncMock(return_value=_resultado_radicado()),
    ) as crear:
        resp = await client.post(BASE, **_multipart(_cuerpo(**cuerpo)))
    return resp, crear


@pytest.mark.asyncio
async def test_con_documento_viaja_el_documento_declarado_y_no_hay_actor(
    client_with_conn, espias
) -> None:
    client, conn = client_with_conn
    resp, crear = await _radicar(client, conn, tipo_identificacion="CC", identificacion=" 1020304050 ")

    assert resp.status_code == 201, resp.text
    assert crear.await_args.args[0].actores == []
    prov = crear.await_args.kwargs["remitente_provisional"]
    assert (prov["documento_declarado_tipo"], prov["documento_declarado_numero"]) == (
        "CC",
        "1020304050",
    )
    assert prov["tipo"] == "ciudadano" and prov["email"] == "ana@example.org"
    assert "numero_documento" not in prov and "tipo_documento" not in prov
    espias.assert_not_awaited()


@pytest.mark.asyncio
async def test_una_entidad_remitente_queda_como_entidad_publica(client_with_conn, espias) -> None:
    client, conn = client_with_conn
    _, crear = await _radicar(
        client, conn, entidad="Contraloría", tipo_identificacion="NIT", identificacion="800123"
    )
    assert crear.await_args.kwargs["remitente_provisional"]["tipo"] == "entidad_publica"


@pytest.mark.asyncio
async def test_sin_documento_no_hay_ficha(client_with_conn, espias) -> None:
    client, conn = client_with_conn
    resp, crear = await _radicar(
        client, conn, identificacion=None, email=None, telefono=None, direccion=None
    )
    assert resp.status_code == 201
    assert crear.await_args.kwargs["remitente_provisional"] is None
    espias.assert_not_awaited()


@pytest.mark.asyncio
async def test_la_respuesta_es_la_misma_con_o_sin_documento(client_with_conn, espias) -> None:
    """Nada en lo que ve quien radica delata que el documento existiera."""
    client, conn = client_with_conn
    con, _ = await _radicar(client, conn, tipo_identificacion="CC", identificacion="123")
    sin, _ = await _radicar(client, conn, identificacion=None, email=None)
    assert set(con.json()) == set(sin.json())
    assert con.json()["mensaje"] == sin.json()["mensaje"]


@pytest.mark.asyncio
async def test_create_es_interno_el_cliente_no_puede_pedir_provisionales() -> None:
    """`remitente_provisional` NO es un campo de `DocumentCreate`."""
    from app.schemas.document import DocumentCreate

    assert "remitente_provisional" not in DocumentCreate.model_fields
