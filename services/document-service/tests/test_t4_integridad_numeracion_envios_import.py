# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""T4 — integridad de la numeración, envíos e import (AREA-09-01/03/09/11/13/14/15).

Cada prueba va emparejada con una MUTACIÓN verificada a mano al entregar la
tanda (se anota en el docstring): revertir la corrección debe hacerla caer.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.schemas.document import DocumentCreate
from app.services import batch_service as batch_mod
from app.services.document_service import DocumentService
from app.services.import_service import ImportService, PackageValidationError
from tests.conftest import TENANT_HEADERS
from tests.test_import import _pkg, _run, _svc
from tests.test_postal import _ship_row

_USER = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# AREA-09-01 — el consecutivo se toma DENTRO de la transacción del radicado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_el_consecutivo_se_toma_dentro_de_la_transaccion() -> None:
    """Mutación: sacar `next_sequence` fuera del bloque `transaction()` → cae."""
    eventos: list[str] = []

    class _Tx:
        async def __aenter__(self) -> None:
            eventos.append("tx_enter")

        async def __aexit__(self, *a: object) -> None:
            eventos.append("tx_exit")

    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_Tx())
    svc = DocumentService(conn, "demo")
    svc._repo = AsyncMock()

    async def _next(*a: object, **k: object) -> int:
        eventos.append("next_sequence")
        return 7

    async def _tenant_code() -> str:
        eventos.append("tenant_code")
        return "DEMO"

    svc._repo.next_sequence.side_effect = _next
    svc._repo.get_tenant_code.side_effect = _tenant_code

    async def _create(**k: object) -> None:
        eventos.append("create")
        raise RuntimeError("falla tras consumir el número")

    svc._repo.create.side_effect = _create

    with pytest.raises(RuntimeError):
        await svc.create(DocumentCreate(doc_type="E", subject="Solicitud de prueba"), None)

    assert eventos == ["tx_enter", "tenant_code", "next_sequence", "create", "tx_exit"]


# ---------------------------------------------------------------------------
# AREA-09-03 y 09-15 — el lote respeta la plantilla de metadatos y el año local
# ---------------------------------------------------------------------------


def _item(doc_class: str | None) -> dict:
    return {
        "id": uuid.uuid4(),
        "item_index": 0,
        "payload": json.dumps(
            {
                "doc_type": "E",
                "subject": "Lote",
                "doc_class": doc_class,
                "dest_dept": None,
                "sender_name": None,
                "sender_entity": None,
                "pages": None,
                "observations": None,
            }
        ),
    }


def _conn_lote(item: dict, plantilla: dict | None) -> tuple[AsyncMock, list[str]]:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.fetch.return_value = [item]
    estados: list[str] = []

    async def _fetchval(query: str, *a: object, **k: object):
        if "pg_try_advisory_lock" in query or "pg_advisory_unlock" in query:
            return True
        if "FROM batch_job_items WHERE id" in query:  # reclamo del ítem (FOR UPDATE)
            return "pending"
        if "RETURNING COALESCE(reprocesado_por, created_by)" in query:
            return uuid.uuid4()
        if "SELECT code FROM tenants" in query:
            return "DEMO"
        if "INSERT INTO tracking_sequences" in query:
            estados.append(f"seq_year={a[0]}")
            return 1
        if "SELECT hash" in query:
            return None
        raise AssertionError(query)

    async def _fetchrow(query: str, *a: object, **k: object):
        if "FROM metadata_templates" in query:
            return plantilla
        if "INSERT INTO radicados" in query:
            estados.append(f"insert_tpl={a[-1]}")
            return {"id": uuid.uuid4(), "nivel_seguridad": 1}
        if "SELECT total_items" in query:
            return {"total_items": 1, "processed_items": 1, "failed_items": 0}
        raise AssertionError(query)

    conn.fetchval.side_effect = _fetchval
    conn.fetchrow.side_effect = _fetchrow
    return conn, estados


@pytest.mark.asyncio
async def test_el_lote_valida_la_plantilla_de_metadatos() -> None:
    """Mutación: borrar la llamada a `validate_metadata` en el lote → el ítem
    entra (`success`) y este test cae."""
    tpl_id = uuid.uuid4()
    plantilla = {
        "id": tpl_id,
        "json_schema": {"type": "object", "required": ["expediente_ref"]},
    }
    conn, estados = _conn_lote(_item("Tutela"), plantilla)
    await batch_mod.BatchService(conn, "demo").process_batch_documents(uuid.uuid4())

    fallidos = [
        c.args
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "UPDATE batch_job_items" in c.args[0]
        and "status = 'failed'" in c.args[0]
    ]
    assert len(fallidos) == 1
    assert "expediente_ref" in fallidos[0][1]  # el motivo es el de metadatos
    assert not any(e.startswith("insert_tpl") for e in estados)  # no se radicó


@pytest.mark.asyncio
async def test_el_lote_sin_plantilla_radica_y_con_plantilla_vacia_guarda_su_version() -> None:
    conn, estados = _conn_lote(_item("Tutela"), None)
    await batch_mod.BatchService(conn, "demo").process_batch_documents(uuid.uuid4())
    assert "insert_tpl=None" in estados

    tpl_id = uuid.uuid4()
    conn, estados = _conn_lote(
        _item("Tutela"), {"id": tpl_id, "json_schema": {"type": "object"}}
    )
    await batch_mod.BatchService(conn, "demo").process_batch_documents(uuid.uuid4())
    assert f"insert_tpl={tpl_id}" in estados


@pytest.mark.asyncio
async def test_el_lote_numera_con_el_ano_de_la_zona_de_radicacion(monkeypatch) -> None:
    """31-dic 23:30 en Bogotá es ya 2027 en UTC. Mutación: volver a `tz=UTC` →
    el número sale de 2027 y el test cae."""
    bogota = ZoneInfo("America/Bogota")
    instante = datetime(2026, 12, 31, 23, 30, tzinfo=bogota)

    class _Reloj(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return instante.astimezone(tz) if tz else instante

    monkeypatch.setattr(batch_mod, "datetime", _Reloj)
    monkeypatch.setattr(batch_mod, "_zona_de_radicacion", lambda: bogota)
    conn, estados = _conn_lote(_item(None), None)
    await batch_mod.BatchService(conn, "demo").process_batch_documents(uuid.uuid4())
    assert "seq_year=2026" in estados


# ---------------------------------------------------------------------------
# AREA-09-09 — dos envíos del mismo radicado no chocan en la guía
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dos_envios_del_mismo_radicado_por_el_mismo_operador_tienen_guias_distintas(
    client_with_conn,
) -> None:
    """Mutación: volver a `sha(operador:radicado_id)` → misma guía y el test cae."""
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    guias: list[str] = []

    async def _fetchrow(query: str, *a: object, **k: object):
        if "INSERT INTO postal_shipment" in query:
            guias.append(a[2])
            return _ship_row(radicado_id=rid, guia=a[2])
        return {"is_root": True}

    mock_conn.fetchrow.side_effect = _fetchrow
    mock_conn.fetch.return_value = []
    for dest in ("Ana", "Luis"):
        resp = await client.post(
            f"/api/v1/documents/{rid}/envios",
            json={"operador": "4-72", "destinatario": dest},
            headers=TENANT_HEADERS,
        )
        assert resp.status_code == 201
    assert len(guias) == 2 and guias[0] != guias[1]
    assert all(g.startswith("C472-") for g in guias)


# ---------------------------------------------------------------------------
# AREA-09-11 — transición manual: asiento con actor, estado previo en el WHERE,
# nivel 3
# ---------------------------------------------------------------------------


def _audits(conn, action: str) -> list:
    return [
        c.args
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == action
    ]


@pytest.mark.asyncio
async def test_la_transicion_manual_deja_asiento_con_el_actor(client_with_conn) -> None:
    """Mutación: quitar `audit.append` de `update_estado` → cae."""
    client, mock_conn = client_with_conn
    sid = uuid.uuid4()
    mock_conn.fetchval.return_value = True  # permiso concedido (y sin hash previo)
    mock_conn.fetchrow.side_effect = [
        {"is_root": True},
        _ship_row(id=sid, estado="en_transito"),
        _ship_row(id=sid, estado="entregado"),
    ]
    mock_conn.fetch.return_value = []
    resp = await client.post(
        f"/api/v1/envios/{sid}/estado",
        json={"estado": "entregado"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200
    asientos = _audits(mock_conn, "document.envio_estado")
    assert len(asientos) == 1
    assert asientos[0][3] == _USER  # actor
    assert json.loads(asientos[0][8])["a"] == "entregado"


@pytest.mark.asyncio
async def test_el_update_compara_estado_previo_y_clearance(client_with_conn) -> None:
    """Mutación: quitar `AND estado = $4` o el `nivel_seguridad <=` del WHERE → cae."""
    client, mock_conn = client_with_conn
    sid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        {"is_root": True},
        _ship_row(id=sid, estado="registrado"),
        _ship_row(id=sid, estado="en_transito"),
    ]
    mock_conn.fetch.return_value = []
    await client.post(
        f"/api/v1/envios/{sid}/estado",
        json={"estado": "en_transito"},
        headers=TENANT_HEADERS,
    )
    sql = next(
        str(c.args[0])
        for c in mock_conn.fetchrow.await_args_list
        if "UPDATE postal_shipment" in str(c.args[0])
    )
    assert "estado = $4" in sql
    assert "nivel_seguridad <=" in sql


@pytest.mark.asyncio
async def test_un_callback_que_gana_la_carrera_hace_que_el_manual_reciba_409(
    client_with_conn,
) -> None:
    """El UPDATE no devuelve fila (el callback dejó `devuelto` entre la lectura y
    el UPDATE) → 409 y NINGÚN asiento de transición manual."""
    client, mock_conn = client_with_conn
    sid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        {"is_root": True},
        _ship_row(id=sid, estado="en_transito"),
        None,  # UPDATE ... WHERE estado = 'en_transito' → 0 filas
    ]
    mock_conn.fetch.return_value = []
    resp = await client.post(
        f"/api/v1/envios/{sid}/estado",
        json={"estado": "entregado"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 409
    assert _audits(mock_conn, "document.envio_estado") == []


@pytest.mark.asyncio
async def test_crear_envio_deja_asiento(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = _ship_row(radicado_id=rid)
    mock_conn.fetch.return_value = []
    resp = await client.post(
        f"/api/v1/documents/{rid}/envios",
        json={"operador": "4-72"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 201
    asientos = _audits(mock_conn, "document.envio_registrado")
    assert len(asientos) == 1 and asientos[0][3] == _USER


@pytest.mark.asyncio
async def test_las_dos_escrituras_postales_exigen_nivel_3(client_with_conn) -> None:
    """Con crud 1 el SQL de RBAC recibe min_crud=3 (el mock no evalúa el SQL: se
    verifica el parámetro). Mutación: quitar `min_crud=3` → el parámetro es 1."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None  # no alcanza el nivel
    for url, body in (
        (f"/api/v1/documents/{uuid.uuid4()}/envios", {"operador": "4-72"}),
        (f"/api/v1/envios/{uuid.uuid4()}/estado", {"estado": "en_transito"}),
    ):
        mock_conn.fetchval.reset_mock()
        resp = await client.post(url, json=body, headers={**TENANT_HEADERS, "X-User-Id": _USER})
        assert resp.status_code == 403
        assert mock_conn.fetchval.await_args_list[0].args[-1] == 3


# ---------------------------------------------------------------------------
# AREA-09-14 — import: número de otra entidad, techo de secuencia y año
# ---------------------------------------------------------------------------


def _svc_demo() -> ImportService:
    svc = _svc()
    svc._repo.get_tenant_code.return_value = "DEMO"
    return svc


def _reconciliaciones(svc: ImportService) -> list[tuple]:
    return [
        c.args
        for c in svc._conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO tracking_sequences" in c.args[0]
    ]


@pytest.mark.asyncio
async def test_un_numero_de_otra_entidad_no_mueve_el_contador() -> None:
    """Mutación: quitar la comparación con el código de entidad → cae."""
    svc = _svc_demo()
    await _run(svc, _pkg([{"tracking": "2026-OTRA-E-000999"}]))
    assert _reconciliaciones(svc) == []


@pytest.mark.asyncio
async def test_un_numero_propio_si_adelanta_el_contador() -> None:
    svc = _svc_demo()
    await _run(svc, _pkg([{"tracking": "2026-DEMO-E-000050"}]))
    (llamada,) = _reconciliaciones(svc)
    assert llamada[1:] == (2026, "E", 50)


@pytest.mark.asyncio
@pytest.mark.parametrize("tracking", ["2026-DEMO-E-2147483647", "2026-OTRA-E-1000000"])
async def test_una_secuencia_sobre_el_techo_es_422(tracking: str) -> None:
    """Mutación: quitar el tope de secuencia → no lanza y el test cae."""
    svc = _svc_demo()
    with pytest.raises(PackageValidationError, match="secuencia"):
        await _run(svc, _pkg([{"tracking": tracking}]))
    svc._repo.insert_radicado.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("anio", ["1899", "9999"])
async def test_un_ano_fuera_de_rango_es_422(anio: str) -> None:
    svc = _svc_demo()
    with pytest.raises(PackageValidationError, match="año"):
        await _run(svc, _pkg([{"tracking": f"{anio}-DEMO-E-000001"}]))


# ---------------------------------------------------------------------------
# AREA-09-13 — import/export no congelan el event loop; un cupo; nivel 3
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validar_el_paquete_no_bloquea_el_event_loop() -> None:
    """Mientras `_validate` duerme 0.5 s, el loop sigue atendiendo (latido < 100 ms).
    Mutación: llamar `_validate` directo (sin `to_thread`) → el latido espera 0.5 s."""
    svc = _svc_demo()
    pkg = _pkg([{"tracking": "2026-DEMO-E-000001"}])

    original = svc._validate

    def _lenta(zf):  # type: ignore[no-untyped-def]
        time.sleep(0.5)
        return original(zf)

    svc._validate = _lenta  # type: ignore[method-assign]
    huecos: list[float] = []
    terminado = False

    async def _latido() -> None:
        # Un reloj que debería marcar cada 10 ms: si el loop se congela, el hueco
        # entre dos marcas crece hasta lo que dure el bloqueo.
        previo = time.perf_counter()
        while not terminado:
            await asyncio.sleep(0.01)
            ahora = time.perf_counter()
            huecos.append(ahora - previo)
            previo = ahora

    async def _importar() -> None:
        nonlocal terminado
        try:
            await _run(svc, pkg)
        finally:
            terminado = True

    await asyncio.gather(_importar(), _latido())
    assert huecos and max(huecos) < 0.1


@pytest.mark.asyncio
async def test_un_segundo_import_concurrente_recibe_429(client_with_conn) -> None:
    """Mutación: quitar `una_operacion_pesada()` → el segundo no recibe 429."""
    from app.core.operacion_pesada import una_operacion_pesada

    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = {"is_root": True}
    async with una_operacion_pesada():
        resp = await client.post(
            "/api/v1/import",
            content=b"x",
            headers={**TENANT_HEADERS, "Content-Type": "application/zip"},
        )
    assert resp.status_code == 429
    assert resp.json()["detail"]["error"] == "operacion_ocupada"
    assert resp.headers["Retry-After"]

    async with una_operacion_pesada():
        resp = await client.post(
            "/api/v1/export", json={}, headers={**TENANT_HEADERS}
        )
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_el_cuerpo_se_corta_al_superar_el_tope(client_with_conn, monkeypatch) -> None:
    from app.routers import import_ as router

    monkeypatch.setattr(router, "_MAX_UPLOAD", 10)
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = {"is_root": True}
    resp = await client.post(
        "/api/v1/import",
        content=b"x" * 50,
        headers={**TENANT_HEADERS, "Content-Type": "application/zip"},
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_el_tope_de_bytes_del_export_es_422() -> None:
    from app.services import export_service as exp

    conn = AsyncMock()
    svc = exp.ExportService(conn, "demo")
    svc._repo = AsyncMock()
    svc._repo.count_incluidos.return_value = 1
    svc._repo.count_excluidos.return_value = 0
    svc._repo.list_radicados.return_value = [{"id": uuid.uuid4(), "tracking_number": "2026-DEMO-E-000001"}]
    svc._repo.list_anexos.return_value = [
        {"filename": "a.pdf", "mime_type": "application/pdf", "file_size": 3, "file_id": uuid.uuid4()}
    ]
    svc._repo.history.return_value = []

    async def _entity(row):  # type: ignore[no-untyped-def]
        return {"anexos": [], "_anexos_meta": [dict(a) for a in await svc._repo.list_anexos(row["id"])]}

    svc._entity = _entity  # type: ignore[method-assign]
    svc._fetch_bytes = AsyncMock(return_value=b"abc")  # type: ignore[method-assign]
    with patch.object(exp, "MAX_EXPORT_BYTES", 2), pytest.raises(exp.ExportBytesTooLargeError):
        await svc.build_export(max_clearance=3, comentario=None, year=None, doc_type=None)
