# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cableado y contratos de la tanda T3 (AREA-11-X1, AREA-01-N2, AREA-05-03-S).

Las pruebas de integración de T3 llaman al repositorio o al servicio directamente:
prueban el SQL, no que el ROUTER entregue el clearance del llamante. Un router que
pasara `max_clearance=3` fijo las dejaría a todas en verde. Aquí se espía el punto
de unión (el repositorio) con un clearance DISTINTIVO (2) y se exige que llegue
tal cual. También se fija el contrato saliente hacia storage (`X-Internal-Token`)
y la traza de lectura de `/terceros/{id}/radicados`, que nadie probaba.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from app.clients import storage_client
from app.repositories.borrador_repository import BorradorRepository
from app.repositories.tercero_repository import TerceroRepository
from app.schemas.borrador import BorradorResponse
from tests.conftest import TENANT_HEADERS
from tests.test_borradores import _borrador_row

_USER = str(uuid.uuid4())
_H = {**TENANT_HEADERS, "X-User-Id": _USER}
_CLEARANCE_2 = {"is_root": False, "max_level": 2}


def _conn(mock_conn) -> None:
    mock_conn.fetchval.return_value = True  # check_permission
    mock_conn.fetchrow.return_value = _CLEARANCE_2  # resolve_caller_clearance


# ── El router entrega el clearance del llamante al repositorio ──────────────


@pytest.mark.asyncio
async def test_get_borrador_pasa_el_clearance_real(client_with_conn, monkeypatch) -> None:
    client, mock_conn = client_with_conn
    _conn(mock_conn)
    visto: list = []

    async def _get(self, borrador_id, max_clearance):
        visto.append(max_clearance)
        return None

    monkeypatch.setattr(BorradorRepository, "get_borrador", _get)
    resp = await client.get(f"/api/v1/borradores/{uuid.uuid4()}", headers=_H)
    assert resp.status_code == 404
    assert visto == [2]


@pytest.mark.asyncio
async def test_listar_borradores_pasa_el_clearance_real(client_with_conn, monkeypatch) -> None:
    client, mock_conn = client_with_conn
    _conn(mock_conn)
    visto: list = []

    async def _list(self, estado, max_clearance):
        visto.append(max_clearance)
        return []

    monkeypatch.setattr(BorradorRepository, "list_borradores", _list)
    resp = await client.get("/api/v1/borradores", headers=_H)
    assert resp.status_code == 200
    assert visto == [2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metodo, ruta",
    [
        ("PATCH", "{id}"),
        ("POST", "{id}/aprobar"),
        ("POST", "{id}/devolver"),
    ],
)
async def test_mutaciones_de_borrador_pasan_el_clearance_real(
    client_with_conn, monkeypatch, metodo, ruta
) -> None:
    client, mock_conn = client_with_conn
    _conn(mock_conn)
    visto: list = []

    async def _get(self, borrador_id, max_clearance):
        visto.append(max_clearance)
        return None  # 404 neutro: el gate decide antes que cualquier efecto

    monkeypatch.setattr(BorradorRepository, "get_borrador", _get)
    url = "/api/v1/borradores/" + ruta.format(id=uuid.uuid4())
    kw = {"json": {"subject": "x"}} if metodo == "PATCH" else {}
    resp = await client.request(metodo, url, headers=_H, **kw)
    assert resp.status_code == 404
    assert visto == [2], f"{metodo} {ruta}: el clearance no llegó al repositorio"


@pytest.mark.asyncio
async def test_patch_borrador_escribe_con_el_clearance_real(
    client_with_conn, monkeypatch
) -> None:
    client, mock_conn = client_with_conn
    _conn(mock_conn)
    visto: list = []
    fila = _borrador_row(nivel_seguridad=2, cuerpo_sha256="a" * 64, version=1)

    async def _get(self, borrador_id, max_clearance):
        return fila

    async def _upd(self, borrador_id, fields, max_clearance, **_kw):
        visto.append(max_clearance)
        return None

    monkeypatch.setattr(BorradorRepository, "get_borrador", _get)
    monkeypatch.setattr(BorradorRepository, "update_borrador", _upd)
    resp = await client.patch(
        f"/api/v1/borradores/{uuid.uuid4()}", json={"subject": "x"}, headers=_H
    )
    assert resp.status_code == 404
    assert visto == [2]


@pytest.mark.asyncio
async def test_radicados_de_tercero_pasa_el_clearance_real(
    client_with_conn, monkeypatch
) -> None:
    client, mock_conn = client_with_conn
    _conn(mock_conn)
    visto: list = []

    async def _rad(self, tercero_id, *, limit, max_clearance):
        visto.append(max_clearance)
        return []

    monkeypatch.setattr(TerceroRepository, "radicados_de", _rad)
    resp = await client.get(f"/api/v1/terceros/{uuid.uuid4()}/radicados", headers=_H)
    assert resp.status_code == 200
    assert visto == [2]


@pytest.mark.asyncio
async def test_radicados_de_tercero_sin_identidad_es_clearance_publico(
    client_with_conn, monkeypatch
) -> None:
    """Sin `X-User-Id` el clearance cae a PUBLICA (1), nunca a «sin restricción»."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    visto: list = []

    async def _rad(self, tercero_id, *, limit, max_clearance):
        visto.append(max_clearance)
        return []

    monkeypatch.setattr(TerceroRepository, "radicados_de", _rad)
    await client.get(f"/api/v1/terceros/{uuid.uuid4()}/radicados", headers=TENANT_HEADERS)
    assert visto in ([], [1])  # 403 por RBAC sin identidad, o 1; jamás > 1


# ── Traza de lectura de /terceros/{id}/radicados (RF-BUS-10) ────────────────


def _asientos(mock_conn) -> list:
    return [
        c
        for c in mock_conn.execute.await_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]


async def _consulta(client, mock_conn, monkeypatch, *, canal: str | None, headers=None):
    _conn(mock_conn)

    async def _rad(self, tercero_id, *, limit, max_clearance):
        return []

    monkeypatch.setattr(TerceroRepository, "radicados_de", _rad)
    h = dict(headers or _H)
    if canal:
        h["X-Canal"] = canal
    return await client.get(f"/api/v1/terceros/{uuid.uuid4()}/radicados", headers=h)


@pytest.mark.asyncio
@pytest.mark.parametrize("canal", ["api", "ui", "mcp"])
async def test_la_consulta_de_un_usuario_deja_un_asiento(
    client_with_conn, monkeypatch, canal
) -> None:
    client, mock_conn = client_with_conn
    resp = await _consulta(client, mock_conn, monkeypatch, canal=canal)
    assert resp.status_code == 200
    asientos = _asientos(mock_conn)
    assert len(asientos) == 1
    assert "document.tercero_radicados_consultado" in asientos[0].args


@pytest.mark.asyncio
@pytest.mark.parametrize("canal", [None, "system", "interno"])
async def test_canal_no_auditable_no_deja_asiento(
    client_with_conn, monkeypatch, canal
) -> None:
    client, mock_conn = client_with_conn
    resp = await _consulta(client, mock_conn, monkeypatch, canal=canal)
    assert resp.status_code == 200
    assert _asientos(mock_conn) == []


@pytest.mark.asyncio
async def test_si_el_asiento_falla_la_consulta_no_se_degrada(
    client_with_conn, monkeypatch
) -> None:
    from app.services import tercero_service

    async def _falla(*a, **k):
        raise RuntimeError("audit caído")

    monkeypatch.setattr(tercero_service.audit, "append", _falla)
    client, mock_conn = client_with_conn
    resp = await _consulta(client, mock_conn, monkeypatch, canal="api")
    assert resp.status_code == 200, "la traza es best-effort: no puede volver 500 una lectura"


# ── El nivel del borrador no puede caer a un default silencioso ─────────────


def test_borrador_response_exige_el_nivel() -> None:
    """Sin default: una fila que no trae el nivel debe romper, no pasar como 1."""
    datos = {k: v for k, v in _borrador_row().items() if k != "nivel_seguridad"}
    datos.pop("faltantes", None)
    with pytest.raises(Exception):  # noqa: B017 — pydantic.ValidationError
        BorradorResponse(**datos, faltantes=[])


# ── Contrato saliente hacia storage (fetch_file_sha256) ─────────────────────


def _http(monkeypatch, handler) -> list[httpx.Request]:
    capturadas: list[httpx.Request] = []
    real = httpx.AsyncClient

    def _handler(req: httpx.Request) -> httpx.Response:
        capturadas.append(req)
        return handler(req)

    def _factory(*a, **k):
        k["transport"] = httpx.MockTransport(_handler)
        return real(*a, **k)

    monkeypatch.setattr(storage_client.httpx, "AsyncClient", _factory)
    monkeypatch.setattr(storage_client.settings, "storage_internal_token", "tok-interno")
    monkeypatch.setattr(storage_client.settings, "storage_service_url", "http://storage")
    return capturadas


@pytest.mark.asyncio
async def test_fetch_file_sha256_cumple_el_contrato_de_storage(monkeypatch) -> None:
    fid = uuid.uuid4()
    reqs = _http(
        monkeypatch, lambda r: httpx.Response(200, json={"hashes": {str(fid): "ab" * 32}})
    )
    huella = await storage_client.fetch_file_sha256(tenant_slug="demo", file_id=fid)
    assert huella == "ab" * 32
    (req,) = reqs
    assert req.method == "POST"
    assert req.url.path == "/api/v1/storage/internal/files/hashes"
    assert req.headers["X-Internal-Token"] == "tok-interno", "sin el sello storage responde 401"
    assert req.headers["X-Tenant-Slug"] == "demo"
    assert json.loads(req.content) == {"file_ids": [str(fid)]}


@pytest.mark.asyncio
async def test_fetch_file_sha256_id_desconocido_es_none(monkeypatch) -> None:
    _http(monkeypatch, lambda r: httpx.Response(200, json={"hashes": {}}))
    assert await storage_client.fetch_file_sha256(tenant_slug="d", file_id=uuid.uuid4()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 404, 500, 503])
async def test_fetch_file_sha256_http_no_200_no_se_degrada(monkeypatch, status) -> None:
    _http(monkeypatch, lambda r: httpx.Response(status, json={}))
    with pytest.raises(storage_client.StorageContentError):
        await storage_client.fetch_file_sha256(tenant_slug="d", file_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_fetch_file_sha256_red_caida_no_se_degrada(monkeypatch) -> None:
    def _cae(r):
        raise httpx.ConnectError("sin red")

    _http(monkeypatch, _cae)
    with pytest.raises(storage_client.StorageContentError):
        await storage_client.fetch_file_sha256(tenant_slug="d", file_id=uuid.uuid4())


# ── Firma declarativa: anexo sin huella → 409, y no se firma ────────────────


@pytest.mark.asyncio
async def test_anexo_sin_huella_es_409_y_no_inserta(client_with_conn, monkeypatch) -> None:
    async def _nada(*, tenant_slug, file_id):
        return None

    monkeypatch.setattr(storage_client, "fetch_file_sha256", _nada)
    client, mock_conn = client_with_conn
    mock_conn.fetchval.side_effect = [True, uuid.uuid4()]
    mock_conn.fetchrow.side_effect = [{"is_root": False, "max_level": 1}]
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/signatures",
        json={"anexo_id": str(uuid.uuid4())},
        headers=_H,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "anexo_sin_huella"
    assert not mock_conn.transaction.called
    assert mock_conn.fetchrow.await_count == 1, "no debe haber INSERT tras el 409"
