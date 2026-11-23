# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de preservación digital: plan + eventos (E10)."""

import json
import uuid
from datetime import UTC, datetime

import pytest

from tests.storage.conftest import TENANT_HEADERS

NOW = datetime.now(tz=UTC)


def _plan_row(version: int = 1) -> dict:
    return {
        "id": uuid.uuid4(),
        "version": version,
        "formatos_destino": ["PDF/A-2b", "TIFF"],
        "formatos_origen": ["application/msword"],
        "num_copias": 2,
        "periodicidad_fixity_dias": 180,
        "politica_migracion": "anual",
        "contingencia": None,
        "creado_por": "ana",
        "created_at": NOW,
    }


@pytest.mark.asyncio
async def test_set_plan_versions(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    # 1.º fetchval = gate require_permission (concedido); 2.º = MAX(version) → 0.
    mock_conn.fetchval.side_effect = [True, 0]
    mock_conn.fetchrow.return_value = _plan_row(version=1)

    resp = await client.put(
        "/api/v1/preservacion/plan",
        json={
            "formatos_destino": ["PDF/A-2b", "TIFF"],
            "num_copias": 2,
            "periodicidad_fixity_dias": 180,
            "politica_migracion": "anual",
        },
        headers={**TENANT_HEADERS, "X-Username": "ana"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["version"] == 1
    assert body["num_copias"] == 2


@pytest.mark.asyncio
async def test_la_politica_de_migracion_se_declara_EN_EL_PLAN(client_with_conn) -> None:
    """RF-PRE-04: qué formatos están en riesgo lo dice la entidad, en su plan.

    Antes esto solo se podía decir con una variable de entorno del despliegue
    —la misma para todas las entidades de la instalación, intocable para el
    archivista y sin versión ni autor—, lo que contradecía la regla que el
    propio código declaraba. Aquí se comprueba que el valor llega hasta el
    INSERT, normalizado (minúsculas, sin repetidos): que la pantalla lo muestre
    no prueba que se haya guardado.
    """
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [True, 0]
    mock_conn.fetchrow.return_value = _plan_row(version=1)

    resp = await client.put(
        "/api/v1/preservacion/plan",
        json={
            "formatos_destino": ["PDF/A-2b"],
            "formatos_origen": [" Application/MSWord ", "application/msword"],
        },
        headers={**TENANT_HEADERS, "X-Username": "ana"},
    )
    assert resp.status_code == 201
    assert resp.json()["formatos_origen"] == ["application/msword"]
    # El 3.er parámetro del INSERT es `formatos_origen` (tras version y destino).
    assert mock_conn.fetchrow.await_args.args[3] == ["application/msword"]


@pytest.mark.asyncio
async def test_un_MIME_mal_escrito_se_rechaza_en_vez_de_guardarse(client_with_conn) -> None:
    """Un "msword" a secas no casa con ningún archivo: el barrido no
    convertiría nada y la política aparecería como declarada en la pantalla.
    Lo peligroso aquí no es convertir de más, es creer que se convirtió."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [True, 0]

    resp = await client.put(
        "/api/v1/preservacion/plan",
        json={"formatos_destino": [], "formatos_origen": ["msword"]},
        headers={**TENANT_HEADERS, "X-Username": "ana"},
    )
    assert resp.status_code == 422
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_get_plan_not_found(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = True  # check_permission → concedido
    mock_conn.fetchrow.return_value = None
    resp = await client.get("/api/v1/preservacion/plan", headers=TENANT_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_plan_forbidden_without_permission(client_with_conn) -> None:
    """GET /plan expone `politica_migracion`/`contingencia` — gateado igual
    que `GET /aip/{expediente_id}` (hallazgo de auditoría Increment B)."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = False  # check_permission → denegado
    resp = await client.get("/api/v1/preservacion/plan", headers=TENANT_HEADERS)
    assert resp.status_code == 403
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_record_evento(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = True  # gate require_permission → concedido
    exp_ref = str(uuid.uuid4())
    h = "a" * 64
    mock_conn.fetchrow.return_value = {
        "id": uuid.uuid4(),
        "object_ref": exp_ref,
        "tipo": "FIJACION",
        "resultado": "ok",
        "hash_before": h,
        "hash_after": h,
        "detalle": json.dumps({"algoritmo": "SHA-256"}),
        "agente": "system",
        "ts": NOW,
    }
    resp = await client.post(
        "/api/v1/preservacion/eventos",
        json={
            "object_ref": exp_ref,
            "tipo": "FIJACION",
            "resultado": "ok",
            "hash_before": h,
            "hash_after": h,
            "detalle": {"algoritmo": "SHA-256"},
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["tipo"] == "FIJACION"
    assert body["detalle"]["algoritmo"] == "SHA-256"  # JSONB parseado


@pytest.mark.asyncio
async def test_record_evento_forbidden_without_permission(client_with_conn) -> None:
    """POST /eventos es una ESCRITURA a la traza de preservación: sin permiso
    no debe poder inyectar eventos PREMIS falsos (hallazgo de auditoría R1,
    endurecimiento de gate del router de preservación)."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = False  # check_permission → denegado
    resp = await client.post(
        "/api/v1/preservacion/eventos",
        json={"object_ref": str(uuid.uuid4()), "tipo": "WORM", "resultado": "ok"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    mock_conn.fetchrow.assert_not_called()


# El AIP real (BagIt + PREMIS v3, E10 Increment B) tiene su propia suite:
# ver tests/storage/test_aip_real.py. La lectura de eventos de otros dominios: test_api_eventos.py.
# — el stub previo (texto sintético, sin RBAC ni
# bytes reales) fue reemplazado por completo.
