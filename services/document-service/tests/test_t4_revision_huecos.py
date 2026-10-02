# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Revisión de la tanda T4: huecos que las pruebas de la implementación dejaban vivos.

Cada prueba cierra una mutación que sobrevivía a `test_t4_integridad_numeracion_envios_import`:

* import/export a nivel 3 (el censo compartido lo vigila, pero no el servicio solo);
* el armado del ZIP del export en un hilo (`to_thread` solo estaba probado en import);
* el callback del operador pasa SU estado previo al UPDATE y pierde limpio la carrera;
* el corte del cuerpo por streaming (sin `Content-Length`, que es lo que lo exige);
* el cupo de operación pesada se libera tras un fallo (si no, el 1.er error deja el
  servicio en 429 para siempre);
* los bordes inclusivos de año y secuencia (un `<` por `<=` rechazaría radicados legales);
* el contrato saliente de `upload_bytes` (todos los tests de import lo sustituyen).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.clients import storage_client
from app.core.operacion_pesada import OperacionPesadaOcupadaError, una_operacion_pesada
from tests.conftest import TENANT_HEADERS
from tests.test_import import _pkg, _run, _svc
from tests.test_postal_callback import (  # noqa: F401
    SECRET,
    _body,
    _hdr,
    _ship_row,
    _url,
    callback_ctx,
)

_USER = str(uuid.uuid4())


# --- import/export exigen nivel 3 -------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("method,url,kw", [
    ("post", "/api/v1/import", {"content": b"x", "headers": {"Content-Type": "application/zip"}}),
    ("post", "/api/v1/export", {"json": {}}),
])
async def test_import_y_export_exigen_nivel_3(client_with_conn, method, url, kw) -> None:
    """Mutación: quitar `min_crud=3` del router → el parámetro del SQL de RBAC es 1."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None  # no alcanza el nivel
    headers = {**TENANT_HEADERS, "X-User-Id": _USER, **kw.pop("headers", {})}
    resp = await getattr(client, method)(url, headers=headers, **kw)
    assert resp.status_code == 403
    assert mock_conn.fetchval.await_args_list[0].args[-1] == 3


# --- export: el armado del ZIP no bloquea el loop ----------------------------


@pytest.mark.asyncio
async def test_armar_el_zip_del_export_no_bloquea_el_event_loop() -> None:
    """Mutación: llamar `_escribir_binario` directo (sin `to_thread`) → el latido
    espera lo que dure el binario más lento y el test cae."""
    from app.services import export_service as exp

    svc = exp.ExportService(AsyncMock(), "demo")
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
    original = exp._escribir_binario

    def _lenta(zf, arcname, data):  # type: ignore[no-untyped-def]
        time.sleep(0.4)
        return original(zf, arcname, data)

    huecos: list[float] = []
    terminado = False

    async def _latido() -> None:
        previo = time.perf_counter()
        while not terminado:
            await asyncio.sleep(0.01)
            ahora = time.perf_counter()
            huecos.append(ahora - previo)
            previo = ahora

    async def _exportar() -> None:
        nonlocal terminado
        try:
            with patch.object(exp, "_escribir_binario", _lenta):
                try:
                    await svc.build_export(max_clearance=3, comentario=None, year=None, doc_type=None)
                except Exception:  # el armado completo no es lo que se mide
                    pass
        finally:
            terminado = True

    await asyncio.gather(_exportar(), _latido())
    assert huecos and max(huecos) < 0.1


# --- callback: carrera con la transición manual -------------------------------


@pytest.mark.asyncio
async def test_el_callback_pasa_su_estado_previo_y_es_acto_de_sistema(callback_ctx) -> None:  # noqa: F811
    """Mutación: estado previo distinto del leído, o `max_clearance` con valor → cae.
    El mock no evalúa el SQL: se verifican los PARÁMETROS del UPDATE ($4 y $5)."""
    client, conn, _ = callback_ctx
    body = _body(estado_operador="EN_TRANSITO", event_id="evt-par")
    conn.fetchval.side_effect = [SECRET, uuid.uuid4(), None]
    conn.fetchrow.side_effect = [_ship_row(estado="registrado"), _ship_row(estado="en_transito")]
    resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 200 and resp.json()["status"] == "applied"
    upd = next(c.args for c in conn.fetchrow.await_args_list if "UPDATE postal_shipment" in str(c.args[0]))
    assert upd[4] == "registrado"
    assert upd[5] is None  # sin tope de clearance: lo autentica el HMAC


@pytest.mark.asyncio
async def test_el_callback_que_pierde_la_carrera_no_pisa_ni_marca_aplicada(callback_ctx) -> None:  # noqa: F811
    """El despacho manual dejó `entregado` entre la lectura y el UPDATE del callback:
    el UPDATE devuelve 0 filas. Debe quedar `recorded_no_change` con el estado REAL,
    sin `aplicada = TRUE`, sin evento a Redis y sin asiento de transición aplicada."""
    client, conn, redis = callback_ctx
    body = _body(estado_operador="DEVUELTO", event_id="evt-carrera", motivo="x")
    conn.fetchval.side_effect = [SECRET, uuid.uuid4(), None]
    conn.fetchrow.side_effect = [
        _ship_row(estado="en_transito"),  # lectura previa
        None,                             # UPDATE ... WHERE estado='en_transito' → 0 filas
        _ship_row(estado="entregado"),    # relectura
    ]
    resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 200
    assert resp.json()["status"] == "recorded_no_change"
    assert resp.json()["estado"] == "entregado"
    sqls = [str(c.args[0]) for c in conn.execute.await_args_list]
    assert not any("aplicada = TRUE" in s for s in sqls)
    assert any("no_aplicada" in str(c.args) for c in conn.execute.await_args_list)
    redis.xadd.assert_not_awaited()


# --- import: corte por streaming y cupo ---------------------------------------


@pytest.mark.asyncio
async def test_el_cuerpo_sin_content_length_se_corta_por_streaming(client_with_conn, monkeypatch) -> None:
    """Chunked: no hay `Content-Length` que mire la cabecera. Mutación: quitar el
    corte dentro del bucle (`if total > _MAX_UPLOAD`) → se bufferiza todo y no hay 413."""
    from app.routers import import_ as router

    monkeypatch.setattr(router, "_MAX_UPLOAD", 10)
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = {"is_root": True}
    leidos = 0

    async def _trozos():
        nonlocal leidos
        for _ in range(50):
            leidos += 1
            yield b"x" * 4

    resp = await client.post(
        "/api/v1/import", content=_trozos(), headers={**TENANT_HEADERS, "Content-Type": "application/zip"}
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_el_cupo_se_libera_tras_un_fallo_dentro() -> None:
    """Mutación: tomar el cupo sin `async with` (sin liberar en la excepción) → el
    segundo intento recibe `OperacionPesadaOcupadaError`."""
    with pytest.raises(RuntimeError):
        async with una_operacion_pesada():
            raise RuntimeError("falla dentro")
    async with una_operacion_pesada():
        pass  # no lanza: el cupo volvió


@pytest.mark.asyncio
async def test_un_import_fallido_no_deja_el_servicio_en_429(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = {"is_root": True}
    h = {**TENANT_HEADERS, "Content-Type": "application/zip"}
    primero = await client.post("/api/v1/import", content=b"no es un zip", headers=h)
    assert primero.status_code == 422
    segundo = await client.post("/api/v1/import", content=b"tampoco", headers=h)
    assert segundo.status_code == 422, "el cupo quedó tomado tras el primer fallo"


@pytest.mark.asyncio
async def test_el_cupo_es_exclusivo() -> None:
    async with una_operacion_pesada():
        with pytest.raises(OperacionPesadaOcupadaError):
            async with una_operacion_pesada():
                pass


# --- import: bordes inclusivos -------------------------------------------------


def _svc_demo():
    svc = _svc()
    svc._repo.get_tenant_code.return_value = "DEMO"
    return svc


@pytest.mark.asyncio
@pytest.mark.parametrize("anio", ["1900", str(datetime.now(tz=UTC).year)])
async def test_los_bordes_del_ano_se_admiten(anio: str) -> None:
    """Mutación: `<` por `<=` en el rango de año → un radicado legal se rechaza."""
    svc = _svc_demo()
    report = await _run(svc, _pkg([{"tracking": f"{anio}-DEMO-E-000001"}]))
    assert report["importados"] == 1


@pytest.mark.asyncio
async def test_la_secuencia_999998_propia_se_admite_y_reconcilia() -> None:
    svc = _svc_demo()
    report = await _run(svc, _pkg([{"tracking": "2026-DEMO-E-999998"}]))
    assert report["importados"] == 1
    llamadas = [
        c.args for c in svc._conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO tracking_sequences" in c.args[0]
    ]
    assert llamadas and llamadas[0][3] == 999998


@pytest.mark.asyncio
async def test_un_paquete_con_un_numero_invalido_no_importa_ninguno() -> None:
    """Todo-o-nada: un número fuera de techo junto a uno válido → 422 y NINGÚN insert."""
    from app.services.import_service import PackageValidationError

    svc = _svc_demo()
    with pytest.raises(PackageValidationError):
        await _run(svc, _pkg([{"tracking": "2026-DEMO-E-000001"}, {"tracking": "2026-DEMO-E-2147483647"}]))
    svc._repo.insert_radicado.assert_not_awaited()


# --- contrato saliente de upload_bytes (import) -------------------------------


@pytest.mark.asyncio
async def test_upload_bytes_cumple_el_contrato_de_storage(monkeypatch) -> None:
    """El import sube cada binario a storage `/upload`; todas las pruebas de import
    sustituyen `upload_bytes`, así que sin esta nada vigila lo que viaja: ruta,
    tenant, identidad del actor FIRMADA (storage la exige) y campo multipart `file`."""
    capturadas: list[httpx.Request] = []
    real = httpx.AsyncClient

    def _factory(*a, **k):
        def _h(req: httpx.Request) -> httpx.Response:
            capturadas.append(req)
            return httpx.Response(201, json={"id": str(uuid.uuid4()), "sha256": "ab" * 32})

        k["transport"] = httpx.MockTransport(_h)
        return real(*a, **k)

    monkeypatch.setattr(storage_client.httpx, "AsyncClient", _factory)
    monkeypatch.setattr(storage_client.settings, "storage_service_url", "http://storage")
    monkeypatch.setenv("IDENTITY_SIGNING_SECRET", "s3cr3to-de-prueba")
    actor = uuid.uuid4()
    await storage_client.upload_bytes(
        tenant_slug="demo", filename="a.pdf", mime_type="application/pdf", data=b"%PDF", uploaded_by=actor
    )
    (req,) = capturadas
    assert req.method == "POST" and req.url.path == "/api/v1/storage/upload"
    assert req.headers["X-Tenant-Slug"] == "demo"
    assert req.headers["X-User-Id"] == str(actor)
    assert req.headers["X-Identity-Signature"], "sin la firma storage descarta la identidad"
    assert b'name="file"' in req.content and b"%PDF" in req.content


# --- hallazgos de la revisión de T4 --------------------------------------------


@pytest.mark.asyncio
async def test_un_numero_propio_en_999999_se_rechaza_y_uno_ajeno_se_admite() -> None:
    """Mutación: quitar la guarda `MAX_SECUENCIA_PROPIA` → el propio entra y el
    siguiente nativo saldría `-E-1000000`. El ajeno no toca el contador."""
    from app.services.import_service import PackageValidationError

    svc = _svc_demo()
    with pytest.raises(PackageValidationError, match="agota el año"):
        await _run(svc, _pkg([{"tracking": "2026-DEMO-E-999999"}]))
    svc._repo.insert_radicado.assert_not_awaited()
    ajeno = await _run(_svc_demo(), _pkg([{"tracking": "2026-OTRA-E-999999"}]))
    assert ajeno["importados"] == 1


@pytest.mark.asyncio
async def test_un_cuerpo_goteado_agota_el_plazo_y_libera_el_cupo(client_with_conn, monkeypatch) -> None:
    """Mutación: quitar `asyncio.timeout` → el cliente que gotea retiene el cupo y
    esta petición no termina (y la siguiente recibiría 429)."""
    from app.routers import import_ as router

    monkeypatch.setattr(router, "_PLAZO_LECTURA_S", 0.2)
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = {"is_root": True}

    async def _gotea():
        for _ in range(100):
            yield b"x"
            await asyncio.sleep(0.1)

    h = {**TENANT_HEADERS, "Content-Type": "application/zip"}
    resp = await client.post("/api/v1/import", content=_gotea(), headers=h)
    assert resp.status_code == 408
    assert resp.json()["detail"]["error"] == "cuerpo_demasiado_lento"
    otra = await client.post("/api/v1/import", content=b"no es un zip", headers=h)
    assert otra.status_code == 422, "el cupo quedó retenido"


@pytest.mark.asyncio
async def test_la_reversion_total_asienta_los_binarios_huerfanos() -> None:
    """Mutación: quitar el `except` de `import_package` → no hay asiento
    `document.import_binarios_sin_radicado` y los bytes subidos quedan sin traza."""
    svc = _svc_demo()
    svc._repo.insert_radicado.side_effect = RuntimeError("falla de BD")
    pkg = _pkg([{"tracking": "2026-DEMO-E-000001", "anexos": [("a.pdf", b"PDF")]}])
    audit = AsyncMock(return_value="h")
    with patch(
        "app.services.import_service.storage_client.upload_bytes",
        new=AsyncMock(return_value=storage_client.ArchivoSubido(file_id=uuid.uuid4(), sha256="x")),
    ), patch("app.services.import_service.audit.append", new=audit), patch(
        "app.services.import_service.ImportHuerfanosService.purgar", new=AsyncMock()
    ):
        with pytest.raises(RuntimeError):
            await svc.import_package(zip_bytes=pkg, max_clearance=3, actor=None)
    acciones = [c.kwargs["action"] for c in audit.await_args_list]
    assert acciones == ["document.import_binarios_sin_radicado"]
    binarios = audit.await_args_list[0].kwargs["payload"]["binarios"]
    assert len(binarios) == 1 and len(binarios[0]["sha256"]) == 64


@pytest.mark.asyncio
async def test_un_item_omitido_por_carrera_asienta_sus_binarios() -> None:
    import asyncpg

    svc = _svc_demo()
    svc._repo.insert_radicado.side_effect = asyncpg.UniqueViolationError("dup")
    pkg = _pkg([{"tracking": "2026-DEMO-E-000001", "anexos": [("a.pdf", b"PDF")]}])
    audit = AsyncMock(return_value="h")
    with patch(
        "app.services.import_service.storage_client.upload_bytes",
        new=AsyncMock(return_value=storage_client.ArchivoSubido(file_id=uuid.uuid4(), sha256="x")),
    ), patch("app.services.import_service.audit.append", new=audit), patch(
        "app.services.import_service.ImportHuerfanosService.purgar", new=AsyncMock()
    ):
        rep = await svc.import_package(zip_bytes=pkg, max_clearance=3, actor=None)
    assert rep["importados"] == 0
    acciones = [c.kwargs["action"] for c in audit.await_args_list]
    assert acciones == ["document.import_binarios_sin_radicado", "radicado.import"]
