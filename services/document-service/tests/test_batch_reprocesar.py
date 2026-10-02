# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Router de `POST /api/v1/batch/documents/{job_id}/reprocesar` y contrato del GET (AREA-09-15).

La lógica de reproceso/numeración se prueba con Postgres real
(`tests/integration/test_lote_reanudable_integration.py`); aquí, lo que se ve desde el
router: el gate (mismo permiso que crear el lote), la traducción de errores de negocio
y que el reproceso LANZA el procesamiento en una tarea propia.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.routers import batch as batch_router
from app.services.batch_service import BatchService, LoteError
from tests.conftest import TENANT_HEADERS

JOB = uuid.uuid4()
RUTA = f"/api/v1/batch/documents/{JOB}/reprocesar"


@pytest.mark.asyncio
async def test_reprocesar_sin_permiso_403(client_with_conn, monkeypatch) -> None:
    """Mutación: quitar el `require_permission` → el 403 pasa a 202."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None  # gate RBAC fail-closed
    repro = AsyncMock()
    monkeypatch.setattr(BatchService, "reprocesar", repro)
    resp = await client.post(RUTA, headers=TENANT_HEADERS)
    assert resp.status_code == 403
    repro.assert_not_awaited()


@pytest.mark.asyncio
async def test_reprocesar_usa_el_mismo_permiso_que_crear_el_lote(client_with_conn) -> None:
    """El gate es PERM_RADI: lo comprueba el parámetro del SQL de RBAC."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    await client.post(RUTA, headers=TENANT_HEADERS)
    await client.post("/api/v1/batch/documents", json={"documents": [{"doc_type": "E", "subject": "x"}]},
                      headers=TENANT_HEADERS)
    # Ambas rutas consultan el RBAC con el MISMO permiso (PERM_RADI, nivel por defecto).
    gates = [c.args for c in mock_conn.fetchval.await_args_list if "is_root" in c.args[0]]
    assert len(gates) == 2 and gates[0][2:] == gates[1][2:] and gates[0][2] == "PERM_RADI"


@pytest.mark.asyncio
async def test_reprocesar_sin_tenant_400(client_with_conn) -> None:
    client, _ = client_with_conn
    headers = {k: v for k, v in TENANT_HEADERS.items() if k != "X-Tenant-Slug"}
    assert (await client.post(RUTA, headers=headers)).status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "codigo,status", [("lote_no_encontrado", 404), ("lote_en_proceso", 409), ("nada_que_reprocesar", 409)]
)
async def test_los_errores_de_negocio_salen_con_su_codigo(client_with_conn, monkeypatch, codigo, status) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    monkeypatch.setattr(BatchService, "reprocesar", AsyncMock(side_effect=LoteError(codigo, "x", status)))
    lanzado = AsyncMock()
    monkeypatch.setattr(batch_router, "_procesar_lote", lanzado)
    resp = await client.post(RUTA, headers=TENANT_HEADERS)
    assert resp.status_code == status
    assert resp.json()["detail"]["error"] == codigo
    lanzado.assert_not_called()


@pytest.mark.asyncio
async def test_reprocesar_202_y_lanza_el_procesamiento_con_el_actor(client_with_conn, monkeypatch) -> None:
    """Mutación: quitar `create_task(_procesar_lote(...))` → el lote queda `pending`
    esperando al barrido y la pantalla no avanza."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    repro = AsyncMock(return_value=3)
    monkeypatch.setattr(BatchService, "reprocesar", repro)
    vistos: list[tuple] = []

    async def _fake(slug, job_id):
        vistos.append((slug, job_id))

    monkeypatch.setattr(batch_router, "_procesar_lote", _fake)
    resp = await client.post(RUTA, headers=TENANT_HEADERS)
    assert resp.status_code == 202
    assert resp.json() == {"job_id": str(JOB), "status": "pending", "items_a_procesar": 3}
    # El actor del reproceso es el X-User-Id validado, nunca un campo del cuerpo.
    assert repro.await_args.args == (JOB, uuid.UUID(TENANT_HEADERS["X-User-Id"]))
    import asyncio

    await asyncio.sleep(0)
    assert vistos == [(TENANT_HEADERS["X-Tenant-Slug"], JOB)]


@pytest.mark.asyncio
async def test_el_estado_expone_el_detalle_de_errores_por_item(client_with_conn) -> None:
    """Contrato para la UI: `errores`, `pending_items`, `reprocesable` y, por ítem,
    `subject`, `error_tipo`, `intentos`."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    ahora = datetime.now(tz=UTC)
    mock_conn.fetchrow.return_value = {
        "id": JOB, "job_type": "documents", "status": "completado_con_errores",
        "total_items": 3, "processed_items": 1, "failed_items": 1, "created_at": ahora,
        "started_at": ahora, "completed_at": ahora, "error_message": None,
        "reanudaciones": 1, "reprocesado_at": None,
    }
    mock_conn.fetch.return_value = [
        {"item_index": 0, "status": "success", "result_id": uuid.uuid4(), "error_message": None,
         "error_tipo": None, "intentos": 1, "subject": "A", "nivel_seguridad": 1},
        {"item_index": 1, "status": "failed", "result_id": None, "error_message": "falta expediente_ref",
         "error_tipo": "MetadataValidationError", "intentos": 2, "subject": "B", "nivel_seguridad": None},
        {"item_index": 2, "status": "pending", "result_id": None, "error_message": None,
         "error_tipo": None, "intentos": 0, "subject": "C", "nivel_seguridad": None},
    ]
    resp = await client.get(f"/api/v1/batch/documents/{JOB}/status", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    b = resp.json()
    assert b["status"] == "completado_con_errores"
    assert b["pending_items"] == 1 and b["reprocesable"] is True and b["reanudaciones"] == 1
    assert [e["item_index"] for e in b["errores"]] == [1]
    assert b["errores"][0]["error_tipo"] == "MetadataValidationError"
    assert b["errores"][0]["subject"] == "B" and b["errores"][0]["intentos"] == 2
    assert len(b["items"]) == 3


@pytest.mark.asyncio
async def test_un_lote_en_curso_no_se_ofrece_como_reprocesable(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    ahora = datetime.now(tz=UTC)
    mock_conn.fetchrow.return_value = {
        "id": JOB, "job_type": "documents", "status": "processing", "total_items": 1,
        "processed_items": 0, "failed_items": 0, "created_at": ahora, "started_at": ahora,
        "completed_at": None, "error_message": None, "reanudaciones": 0, "reprocesado_at": None,
    }
    mock_conn.fetch.return_value = [
        {"item_index": 0, "status": "pending", "result_id": None, "error_message": None,
         "error_tipo": None, "intentos": 0, "subject": "A", "nivel_seguridad": None}]
    b = (await client.get(f"/api/v1/batch/documents/{JOB}/status", headers=TENANT_HEADERS)).json()
    assert b["reprocesable"] is False, "en curso: lo retoma el barrido, no se ofrece el botón"
