# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Índice de información clasificada y reservada (E08, Ley 1712/2014 art. 20)."""

import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS

NOW = datetime.now(tz=UTC)
_USER = str(uuid.uuid4())


def _indice_row(tracking, doc_type, nivel, *, fundamento=None, causal=None, plazo=None):
    return {
        "tracking_number": tracking,
        "doc_type": doc_type,
        "nivel_seguridad": nivel,
        "clasificacion_fundamento": fundamento,
        "clasificacion_causal": causal,
        "clasificacion_plazo_meses": plazo,
        "clasificado_at": NOW,
        "registered_at": NOW,
        "origin_dept": "Jurídica",
        "dest_dept": "Dirección General",
        "registered_by": None,
    }


@pytest.mark.asyncio
async def test_indice_sin_permiso_403(client_with_conn) -> None:
    """PERM_RECLASIFICAR requerido; sin permiso → 403."""
    client, conn = client_with_conn
    conn.fetchval.return_value = None
    resp = await client.get(
        "/api/v1/reports/indice-reservado",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_indice_con_permiso_mapea_niveles(client_with_conn) -> None:
    """nivel 2→reservada/art.19, nivel 3→clasificada/art.18; sin fuga de contenido."""
    client, conn = client_with_conn
    conn.fetchval.side_effect = [True, None]  # permiso, audit prev_hash
    conn.fetch.return_value = [
        _indice_row("2026-DEMO-E-000001", "E", 3, fundamento="Art. 18 lit. b Ley 1712"),
        _indice_row("2026-DEMO-S-000002", "S", 2, causal="Defensa y seguridad nacional"),
    ]
    resp = await client.get(
        "/api/v1/reports/indice-reservado",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    items = body["items"]
    # Content-free: ninguna fila expone el asunto/contenido del documento reservado.
    for it in items:
        assert "subject" not in it and "asunto" not in it

    n3 = next(i for i in items if i["nivel_seguridad"] == 3)
    assert n3["clasificacion"] == "clasificada"
    assert "art. 18" in n3["excepcion_ley_1712"]
    assert n3["fundamento_juridico"] == "Art. 18 lit. b Ley 1712"
    assert n3["numero_radicado"] == "2026-DEMO-E-000001"

    n2 = next(i for i in items if i["nivel_seguridad"] == 2)
    assert n2["clasificacion"] == "reservada"
    assert "art. 19" in n2["excepcion_ley_1712"]
    assert n2["causal_reserva"] == "Defensa y seguridad nacional"


@pytest.mark.asyncio
async def test_indice_vacio(client_with_conn) -> None:
    """Sin radicados clasificados → total 0, lista vacía."""
    client, conn = client_with_conn
    conn.fetchval.side_effect = [True, None]
    conn.fetch.return_value = []
    resp = await client.get(
        "/api/v1/reports/indice-reservado",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200
    assert resp.json() == {"total": 0, "items": []}


@pytest.mark.asyncio
async def test_indice_csv(client_with_conn) -> None:
    """El CSV lleva el encabezado esperado y una fila por ítem; sin columna de asunto."""
    client, conn = client_with_conn
    conn.fetchval.side_effect = [True, None]
    conn.fetch.return_value = [
        _indice_row("2026-DEMO-E-000001", "E", 3, fundamento="Art. 18"),
    ]
    resp = await client.get(
        "/api/v1/reports/indice-reservado.csv",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    text = resp.text
    header = text.splitlines()[0]
    assert "numero_radicado" in header and "excepcion_ley_1712" in header
    assert "asunto" not in header and "subject" not in header
    assert "2026-DEMO-E-000001" in text


@pytest.mark.asyncio
async def test_indice_csv_neutraliza_inyeccion_formula(client_with_conn) -> None:
    """Un fundamento que empieza por '=' se antepone con apóstrofo (anti CSV injection)."""
    client, conn = client_with_conn
    conn.fetchval.side_effect = [True, None]
    conn.fetch.return_value = [
        _indice_row("2026-DEMO-E-000001", "E", 2, causal="=SUM(A1:A9)"),
    ]
    resp = await client.get(
        "/api/v1/reports/indice-reservado.csv",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200
    assert "'=SUM(A1:A9)" in resp.text  # el apóstrofo fuerza lectura como texto
    assert ",=SUM" not in resp.text     # nunca la fórmula desnuda


@pytest.mark.asyncio
async def test_indice_csv_sin_permiso_403(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchval.return_value = None
    resp = await client.get(
        "/api/v1/reports/indice-reservado.csv",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Motivación obligatoria (Ley 1712 art. 19/28): clasificar a nivel≥2 SIN
# fundamento → 422 → el índice del art. 20 nunca queda con fundamentos NULL.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reclassify_a_nivel2_sin_fundamento_422(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchval.return_value = True  # permiso concedido → el 422 es del schema
    resp = await client.patch(
        f"/api/v1/documents/{uuid.uuid4()}/security-level",
        json={"nivel_seguridad": 2, "motivo": "sin fundamento"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_crear_radicado_clasificado_sin_fundamento_422(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchval.return_value = True
    resp = await client.post(
        "/api/v1/documents",
        json={
            "doc_type": "E",
            "doc_class": "oficio",
            "subject": "asunto",
            "nivel_seguridad": 3,  # clasificada, sin clasificacion_fundamento
        },
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 422
