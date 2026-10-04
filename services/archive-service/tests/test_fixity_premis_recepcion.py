# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-07-05: el fixity-check de la recepción es un evento PREMIS en storage-service.

`recibir()` ya verificaba la fixity del acervo, pero el resultado solo vivía en el payload
del asiento: no salía en `GET /expedientes/{id}/preservacion/eventos`. Ahora se registra
(`FIJACION`, idempotente por `dedup_key`) POST-commit y best-effort, y la reconciliación
lo repite si falló.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import settings
from app.services.transferencia_service import TransferenciaService
from tests.conftest import REGISTRAR_FIXITY_PREMIS_REAL, TENANT_HEADERS, USER_ID
from tests.test_transferencia_indice_y_tenencia import _flujo_recibir, _mock_sellar

TENANT = "demo"


def _conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = "UPDATE 1"
    conn.fetch.return_value = []
    conn.fetchval.return_value = None
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _fila(**kw) -> dict:
    base = {
        "transferencia_id": uuid.uuid4(),
        "fixity_premis_detalle": {
            "origen": "recepcion_transferencia",
            "documentos_ok": True,
            "huella_indice_ok": True,
            "num_documentos": 3,
            "documentos_sin_huella": 1,
        },
        "fixity_premis_at": None,
        "fixity_premis_intentos": 0,
        "expediente_id": uuid.uuid4(),
        "decidida_por": str(USER_ID),
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_recibir_guarda_el_detalle_y_registra_el_evento_post_commit(
    client_with_conn, _fixity_premis_doble
) -> None:
    from tests.test_transferencias import _indice_row

    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    fila = _indice_row()
    _flujo_recibir(mock_conn, tid, exp_id, indice_gate=fila, indice_fixity=fila)

    with _mock_sellar():
        resp = await client.post(f"/api/v1/transferencias/{tid}/recibir", headers=TENANT_HEADERS)

    assert resp.status_code == 200, resp.text
    # el detalle viaja en la MISMA INSERT del bookkeeping (dentro de la tx)
    ins = next(
        c for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO transferencia_acta_firma" in c.args[0]
    )
    detalle = json.loads(ins.args[2])
    assert detalle["documentos_ok"] is True and detalle["algoritmo"] == "SHA-256"
    assert "documentos_sin_huella" in detalle
    # hallazgo T12: la verificación parcial se declara, no se lee como fixity total
    assert detalle["verificacion"] == (
        "parcial" if detalle["documentos_sin_huella"] > 0 else "completa"
    )
    # y el registro del evento se dispara una vez, tras el sellado
    _fixity_premis_doble.assert_awaited_once()
    assert _fixity_premis_doble.await_args.args == (tid,)


@pytest.mark.asyncio
async def test_registra_el_evento_con_la_forma_que_storage_espera() -> None:
    conn = _conn()
    svc = TransferenciaService(conn, TENANT)
    fila = _fila()
    conn.fetchrow.return_value = fila
    tid = fila["transferencia_id"]

    with patch(
        "app.clients.storage_client.registrar_evento_internal", new=AsyncMock(return_value=True)
    ) as post:
        resultado = await REGISTRAR_FIXITY_PREMIS_REAL(svc, tid, origen="close")

    assert resultado == "registrado"
    kw = post.await_args.kwargs
    assert kw["object_ref"] == str(fila["expediente_id"])
    assert kw["tipo"] == "FIJACION" and kw["resultado"] == "ok"
    assert kw["dedup_key"] == f"fixity-recepcion:{tid}"
    assert kw["agente"] == str(USER_ID)
    assert kw["detalle"]["evento"] == "fixity check"
    assert kw["detalle"]["documentos_sin_huella"] == 1
    marca = [c for c in conn.execute.call_args_list if "fixity_premis_at = now()" in str(c.args[0])]
    assert marca, "no se marcó el evento como registrado"


@pytest.mark.asyncio
async def test_storage_caido_reprograma_con_backoff_sin_lanzar(monkeypatch) -> None:
    monkeypatch.setattr(settings, "indice_reconciliation_max_intentos", 8)
    conn = _conn()
    svc = TransferenciaService(conn, TENANT)
    fila = _fila(fixity_premis_intentos=2)
    conn.fetchrow.return_value = fila

    with patch(
        "app.clients.storage_client.registrar_evento_internal", new=AsyncMock(return_value=False)
    ):
        resultado = await REGISTRAR_FIXITY_PREMIS_REAL(svc, fila["transferencia_id"], origen="close")

    assert resultado == "pendiente"
    bump = next(c for c in conn.execute.call_args_list if "fixity_premis_intentos + 1" in str(c.args[0]))
    _, _tid, proximo_at, error, agotado = bump.args
    assert proximo_at is not None and error == "storage_evento_no_registrado" and agotado is False


@pytest.mark.asyncio
async def test_al_agotarse_deja_asiento(monkeypatch) -> None:
    monkeypatch.setattr(settings, "indice_reconciliation_max_intentos", 3)
    conn = _conn()
    svc = TransferenciaService(conn, TENANT)
    fila = _fila(fixity_premis_intentos=2)
    conn.fetchrow.return_value = fila

    with patch(
        "app.clients.storage_client.registrar_evento_internal", new=AsyncMock(return_value=False)
    ):
        resultado = await REGISTRAR_FIXITY_PREMIS_REAL(svc, fila["transferencia_id"], origen="reconciliation")

    assert resultado == "agotado"
    acciones = [
        c.args[5] for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert "archive.fixity_premis_agotado" in acciones


@pytest.mark.asyncio
async def test_sin_detalle_guardado_no_se_inventa_el_evento() -> None:
    """Recepción anterior a la migración 056: sin detalle NO se reconstruye un evento."""
    conn = _conn()
    svc = TransferenciaService(conn, TENANT)
    conn.fetchrow.return_value = _fila(fixity_premis_detalle=None)

    with patch("app.clients.storage_client.registrar_evento_internal", new=AsyncMock()) as post:
        resultado = await REGISTRAR_FIXITY_PREMIS_REAL(svc, uuid.uuid4(), origen="reconciliation")

    assert resultado == "omitido"
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_el_metodo_real_nunca_lanza() -> None:
    conn = _conn()
    svc = TransferenciaService(conn, TENANT)
    conn.fetchrow.side_effect = RuntimeError("la base se cayó")

    assert await REGISTRAR_FIXITY_PREMIS_REAL(svc, uuid.uuid4(), origen="close") == "pendiente"


@pytest.mark.asyncio
async def test_la_barrida_reintenta_las_recepciones_con_el_evento_pendiente() -> None:
    from contextlib import asynccontextmanager

    from app.jobs import indice_reconciliation

    mock_pool = MagicMock()
    glob = AsyncMock()
    glob.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=glob)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    conn = _conn()
    conn.fetchval.return_value = True  # advisory lock
    pendiente = {"transferencia_id": uuid.uuid4(), "fixity_premis_intentos": 1}
    conn.fetch.return_value = [pendiente]

    @asynccontextmanager
    async def _acquire(_slug: str):
        yield conn

    reg = AsyncMock(return_value="registrado")
    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", _acquire),
        patch.object(TransferenciaService, "registrar_fixity_premis", reg),
    ):
        resumen = await indice_reconciliation.run_once_fixity_premis()

    assert resumen == {"pendientes_vistos": 1, "registrados": 1, "fallidos": 0, "agotados": 0}
    reg.assert_awaited_once()
    assert reg.await_args.kwargs == {"origen": "reconciliation"}
    # solo las que tienen detalle guardado, sin confirmar, no agotadas y con backoff cumplido
    sql = conn.fetch.await_args.args[0]
    assert "fixity_premis_detalle IS NOT NULL" in sql and "fixity_premis_agotado = FALSE" in sql


@pytest.mark.asyncio
async def test_fixity_con_documentos_sin_huella_se_declara_parcial(
    client_with_conn, _fixity_premis_doble
) -> None:
    """Hallazgo T12: un documento sin huella (nada que verificar) vuelve la verificación
    PARCIAL en el evento PREMIS; con `ok` a secas se leería como fixity del acervo entero."""
    from tests.conftest import make_radicado_link_record
    from tests.test_transferencias import _indice_row

    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid, exp_id, doc_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fila = _indice_row()
    _flujo_recibir(mock_conn, tid, exp_id, indice_gate=fila, indice_fixity=fila)
    primeras = iter(
        [
            [make_radicado_link_record(expediente_id=exp_id, radicado_id=doc_id, content_hash=None)],
            [{"document_id": doc_id, "valor_huella": None}],
        ]
    )
    # las dos primeras lecturas son las de la fixity; el resto (FUID, etc.) devuelve vacío
    mock_conn.fetch.side_effect = lambda *a, **k: next(primeras, [])
    with _mock_sellar():
        resp = await client.post(f"/api/v1/transferencias/{tid}/recibir", headers=TENANT_HEADERS)

    assert resp.status_code == 200, resp.text
    ins = next(
        c for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO transferencia_acta_firma" in c.args[0]
    )
    detalle = json.loads(ins.args[2])
    assert detalle["documentos_sin_huella"] == 1
    assert detalle["verificacion"] == "parcial"
