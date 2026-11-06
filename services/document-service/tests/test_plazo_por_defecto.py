# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Una Entrada sin días toma el plazo del tipo. Quien indica días gana.

El importador no consulta ese plazo: conserva el vencimiento del paquete.
"""

from __future__ import annotations

import inspect
from datetime import date
from pathlib import Path

import pytest

from document.services.email_sondeo import SondeoDeCorreo
from tests.conftest import TENANT_HEADERS, make_radicado_record
from tests.test_documents import _CREATE_PAYLOAD

_RAIZ = Path(__file__).resolve().parents[1]


def _fetchval(dias: int | None):
    async def _fv(query, *args, **kwargs):
        if isinstance(query, str) and "default_response_days" in query:
            return dias
        return 1

    return _fv


@pytest.mark.asyncio
async def test_la_entrada_sin_dias_toma_el_plazo_del_tipo(client_with_deps) -> None:
    client, mock_conn, fake = client_with_deps
    fake.due_date = date(2024, 2, 1)
    mock_conn.fetchval.side_effect = _fetchval(10)
    mock_conn.fetchrow.side_effect = [
        None,
        {"code": "DEMO"},
        make_radicado_record(due_date=date(2024, 2, 1)),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post("/api/v1/documents", json=_CREATE_PAYLOAD, headers=TENANT_HEADERS)

    assert resp.status_code == 201, resp.text
    assert resp.json()["due_date"] == "2024-02-01"
    assert fake.calls[0][2] == 10


@pytest.mark.asyncio
async def test_los_dias_indicados_ganan_al_plazo_del_tipo(client_with_deps) -> None:
    client, mock_conn, fake = client_with_deps
    fake.due_date = date(2024, 1, 22)
    mock_conn.fetchval.side_effect = _fetchval(99)
    mock_conn.fetchrow.side_effect = [
        None,
        {"code": "DEMO"},
        make_radicado_record(due_date=date(2024, 1, 22)),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        "/api/v1/documents",
        json={**_CREATE_PAYLOAD, "response_days": 15},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    assert fake.calls[0][2] == 15
    consultas = [str(c.args[0]) for c in mock_conn.fetchval.await_args_list if c.args]
    assert not any("default_response_days" in q for q in consultas)


@pytest.mark.asyncio
async def test_un_interno_no_hereda_el_plazo_del_tipo(client_with_deps) -> None:
    client, mock_conn, fake = client_with_deps
    mock_conn.fetchval.side_effect = _fetchval(10)
    mock_conn.fetchrow.side_effect = [
        None,
        {"code": "DEMO"},
        make_radicado_record(doc_type="I"),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        "/api/v1/documents",
        json={**_CREATE_PAYLOAD, "doc_type": "I"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    assert fake.calls == []
    assert resp.json()["due_date"] is None


def test_el_sondeo_automatico_lleva_el_calendario() -> None:
    src = inspect.getsource(SondeoDeCorreo._radicar_pendientes)
    assert "self._tenant_client" in src
    assert "DocumentService(conn, self._tenant, None" not in src


def test_el_importador_no_inventa_plazo() -> None:
    texto = (_RAIZ / "document" / "services" / "import_service.py").read_text(encoding="utf-8")
    repo = _RAIZ / "document" / "repositories" / "import_repository.py"
    texto += repo.read_text(encoding="utf-8")
    assert "plazo_por_defecto" not in texto
    assert "default_response_days" not in texto
