# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`document.api.fetch_anexos` (E10 debt, cableado del AIP OAIS al cierre del
expediente): inventario TOTAL batch de anexos por `radicado_id`, SIN el gate de
clearance que sí aplica `GET /documents/{id}/anexos` (RF-SEG-08): el empaquetado
del AIP es un acto de SISTEMA. Era una ruta interna con token; ahora es una
llamada a función (ADR-047 §1). El aislamiento real por tenant se prueba en
`tests/document/integration/test_internal_anexos_tenant_isolation.py`.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import asyncpg
import pytest

from document import api as document_api
from document.repositories.document_repository import DocumentRepository
from tests.document.conftest import TENANT_SLUG, make_anexo_record

# ---------------------------------------------------------------------------
# Inventario batch — forma de la respuesta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_returns_anexos_grouped_by_radicado_id(api_conn) -> None:
    radicado_a = uuid.uuid4()
    radicado_b = uuid.uuid4()  # sin anexos -> no debe aparecer como clave
    anexo_a1 = make_anexo_record(radicado_id=radicado_a)
    anexo_a2 = make_anexo_record(radicado_id=radicado_a)
    api_conn.fetch.return_value = [anexo_a1, anexo_a2]

    body = await document_api.fetch_anexos(
        tenant_slug=TENANT_SLUG, radicado_ids=[radicado_a, radicado_b]
    )

    assert str(radicado_b) not in body
    assert len(body[str(radicado_a)]) == 2
    returned_file_ids = {item["file_id"] for item in body[str(radicado_a)]}
    assert returned_file_ids == {str(anexo_a1["file_id"]), str(anexo_a2["file_id"])}
    for item in body[str(radicado_a)]:
        assert set(item.keys()) == {"file_id", "filename"}


@pytest.mark.asyncio
async def test_radicado_without_anexos_is_empty_dict_not_error(api_conn) -> None:
    """Ningún anexo para ningún radicado del lote -> `{}`, nunca un error: el
    llamador lo trata como «sin anexos digitales»."""
    api_conn.fetch.return_value = []

    assert await document_api.fetch_anexos(
        tenant_slug=TENANT_SLUG, radicado_ids=[uuid.uuid4()]
    ) == {}


@pytest.mark.asyncio
async def test_empty_radicado_ids_short_circuits(api_conn) -> None:
    assert await document_api.fetch_anexos(tenant_slug=TENANT_SLUG, radicado_ids=[]) == {}
    api_conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_un_fallo_de_la_bd_no_es_un_inventario_vacio(api_conn) -> None:
    """RF-FIR-15: un AIP «completo» con anexos faltantes sería peor que no
    empaquetarlo. El fallo sube como `DocumentUnavailableError`."""
    api_conn.fetch.side_effect = asyncpg.PostgresError("caída")

    with pytest.raises(document_api.DocumentUnavailableError):
        await document_api.fetch_anexos(tenant_slug=TENANT_SLUG, radicado_ids=[uuid.uuid4()])


# ---------------------------------------------------------------------------
# Repositorio: la query filtra SOLO por radicado_id (sin clearance/JOIN)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repository_filters_by_radicado_ids_only_no_clearance_join() -> None:
    """Deliberadamente distinto de `_get_anexos`/`list_anexos_for_document`
    (no-read-up, RF-SEG-08): SIN JOIN a `radicados` ni filtro de
    `nivel_seguridad` — inventario TOTAL de sistema, ver el docstring de
    `list_anexos_for_radicados_internal`."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []
    repo = DocumentRepository(mock_conn, TENANT_SLUG)
    ids = [uuid.uuid4(), uuid.uuid4()]

    result = await repo.list_anexos_for_radicados_internal(ids)

    assert result == {}
    mock_conn.fetch.assert_awaited_once()
    sql = mock_conn.fetch.await_args.args[0]
    assert "WHERE radicado_id = ANY($1" in sql
    assert "JOIN" not in sql.upper()
    assert mock_conn.fetch.await_args.args[1] == ids


@pytest.mark.asyncio
async def test_repository_empty_input_short_circuits_without_query() -> None:
    mock_conn = AsyncMock()
    repo = DocumentRepository(mock_conn, TENANT_SLUG)

    result = await repo.list_anexos_for_radicados_internal([])

    assert result == {}
    mock_conn.fetch.assert_not_called()
