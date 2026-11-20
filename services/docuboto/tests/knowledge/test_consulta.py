# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Buscar delega, rechequea y vuelve al índice local si el proveedor no responde."""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any

import httpx
import pytest
from orpycamcp_common.kek import seal

from knowledge.clients.ppi import ClientePpi
from knowledge.core import consulta
from knowledge.core.config import settings
from knowledge.core.rechequeo import Registro, filtrar_busqueda, refs_a_borrar
from knowledge.core.seudonimo import aad, referenciar
from knowledge.routers import knowledge as rutas
from tests.knowledge.conftest import TENANT_HEADERS

ACTOR = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
KEK = b"\x44" * 32
CLAVE = b"\x55" * 32


def _fila(
    ident: uuid.UUID, tracking: str, *, nivel: int = 1, status: str = "radicado"
) -> dict[str, Any]:
    return {
        "id": ident,
        "tracking_number": tracking,
        "status": status,
        "nivel_seguridad": nivel,
    }


def _cita(ident: uuid.UUID, quote: str, *, score: float = 0.8) -> dict[str, Any]:
    return {
        "score": score,
        "citation": {
            "record_ref": str(ident),
            "attachment_ref": None,
            "page": 1,
            "span": {"start": 0, "end": len(quote)},
            "quote": quote,
        },
    }


class _Conn:
    def __init__(
        self,
        *,
        credencial: dict[str, Any] | None,
        filas: list[dict[str, Any]],
        clearance: int = 3,
        proveedor: str = "ppi",
        niveles: list[int] | None = None,
        bandera: bool = True,
        suspendido: bool = False,
    ) -> None:
        self.credencial = credencial
        self.filas = {fila["id"]: fila for fila in filas}
        self.clearance = clearance
        self.proveedor = proveedor
        self.niveles = [1] if niveles is None else niveles
        self.bandera = bandera
        self.suspendido = suspendido
        self.escrituras: list[tuple[Any, ...]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        self.escrituras.append(args)
        return "OK"

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        if "public.tenants" in sql:
            return {
                "asistente_habilitado": self.bandera,
                "intelligence_provider": self.proveedor,
                "intelligence_levels": self.niveles,
                "intelligence_send_metadata": False,
            }
        if "intelligence_state" in sql:
            return {"send_suspended": self.suspendido}
        if "intelligence_credentials" in sql:
            return self.credencial
        if "auth_users" in sql:
            return {"is_root": False, "max_level": self.clearance}
        if "radicados" in sql:
            return self.filas.get(args[0])
        raise AssertionError(sql)

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        if "radicados" not in sql:
            raise AssertionError(sql)
        pedidas = set(args[0])
        return [fila for fila in self.filas.values() if fila["id"] in pedidas]


class _Transporte:
    def __init__(self, respuesta: Any = None, error: BaseException | None = None) -> None:
        self.respuesta = respuesta if respuesta is not None else {"mode": "search", "hits": []}
        self.error = error
        self.llamadas: list[tuple[str, str, dict[str, Any], float | None]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any],
        timeout: float | None = None,
    ) -> Any:
        self.llamadas.append((method, url, json, timeout))
        if self.error is not None:
            raise self.error
        return self.respuesta


def _credencial(slug: str = "demo") -> dict[str, Any]:
    ciphertext, nonce = seal(CLAVE, key=KEK, aad=aad(slug))
    return {"ciphertext": ciphertext, "nonce": nonce, "kek_version": 1}


def _cliente(transporte: _Transporte) -> ClientePpi:
    return ClientePpi(transporte, base_url="http://ppi.internal")


def _kek(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "intelligence_credential_kek", base64.b64encode(KEK).decode())


def test_una_cita_de_nivel_no_habilitado_se_borra() -> None:
    ident = uuid.uuid4()
    vivos = {ident: Registro("2026-E-2", "radicado", 2)}
    utiles, descartes = filtrar_busqueda(
        [_cita(ident, "texto")], vivos, habilitados=[1], clearance=3
    )
    assert utiles == []
    assert refs_a_borrar(descartes) == [ident]


def test_un_nivel_que_esta_persona_no_alcanza_no_se_borra() -> None:
    ident = uuid.uuid4()
    vivos = {ident: Registro("2026-E-2", "radicado", 2)}
    utiles, descartes = filtrar_busqueda(
        [_cita(ident, "texto")], vivos, habilitados=[1, 2], clearance=1
    )
    assert utiles == []
    assert refs_a_borrar(descartes) == []
    assert descartes[0].motivo == "clearance"


async def test_en_local_no_hay_llamada(monkeypatch: pytest.MonkeyPatch) -> None:
    _kek(monkeypatch)
    transporte = _Transporte()
    conn = _Conn(credencial=_credencial(), filas=[], proveedor="local")
    resultado = await consulta.buscar(
        conn,
        "demo",
        _cliente(transporte),
        texto="contrato",
        limite=5,
        actor=ACTOR,
    )
    assert resultado.delegada is False
    assert transporte.llamadas == []
    assert conn.escrituras == []


async def test_la_consulta_lleva_seudonimo_y_clearance_acotado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _kek(monkeypatch)
    bueno = uuid.uuid4()
    transporte = _Transporte(
        {"mode": "search", "hits": [_cita(bueno, "Contrato de obra", score=0.7)]}
    )
    resultado = await consulta.buscar(
        _Conn(credencial=_credencial(), filas=[_fila(bueno, "2026-E-1")], clearance=3, niveles=[1]),
        "demo",
        _cliente(transporte),
        texto="contrato",
        limite=5,
        actor=ACTOR,
        alcance=bueno,
    )
    assert resultado.delegada is True
    [hallazgo] = resultado.hallazgos
    assert hallazgo.tracking == "2026-E-1"
    assert hallazgo.texto == "Contrato de obra"
    assert hallazgo.score == 0.7

    [llamada] = transporte.llamadas
    metodo, url, cuerpo, plazo = llamada
    assert metodo == "POST"
    assert url == "http://ppi.internal/v1/query"
    assert plazo == consulta.PLAZO_BUSCAR
    assert cuerpo["clearance"] == "public"
    assert cuerpo["on_behalf_of"] == referenciar(CLAVE, ACTOR)
    assert str(ACTOR) not in json.dumps(cuerpo)
    assert cuerpo["scope"] == {"record_ref": str(bueno), "include_children": True}
    assert "ingest" not in url


async def test_un_anulado_se_descarta_y_se_ordena_borrar(monkeypatch: pytest.MonkeyPatch) -> None:
    _kek(monkeypatch)
    bueno = uuid.uuid4()
    anulado = uuid.uuid4()
    transporte = _Transporte(
        {
            "mode": "search",
            "hits": [_cita(bueno, "sigue"), _cita(anulado, "no sigue")],
        }
    )
    resultado = await consulta.buscar(
        _Conn(
            credencial=_credencial(),
            filas=[_fila(bueno, "2026-E-1"), _fila(anulado, "2026-E-2", status="anulado")],
        ),
        "demo",
        _cliente(transporte),
        texto="contrato",
        limite=5,
        actor=ACTOR,
    )
    assert [h.id for h in resultado.hallazgos] == [bueno]
    borrados = [url for _m, url, _c, _p in transporte.llamadas]
    assert borrados[0].endswith("/v1/query")
    assert borrados[1].endswith("/v1/ingest")
    assert transporte.llamadas[1][2]["items"][0]["deletion"]["external_ref"] == str(anulado)


async def test_si_el_proveedor_no_responde_buscar_vuelve_al_indice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _kek(monkeypatch)
    transporte = _Transporte(error=TimeoutError("plazo"))
    conn = _Conn(credencial=_credencial(), filas=[])
    resultado = await consulta.buscar(
        conn,
        "demo",
        _cliente(transporte),
        texto="contrato",
        limite=5,
        actor=ACTOR,
    )
    assert resultado.delegada is False
    assert len(transporte.llamadas) == 1
    assert conn.escrituras == [("fallback", "local")]


async def test_sin_clave_de_seudonimo_no_hay_llamada(monkeypatch: pytest.MonkeyPatch) -> None:
    _kek(monkeypatch)
    transporte = _Transporte()
    resultado = await consulta.buscar(
        _Conn(credencial=None, filas=[]),
        "demo",
        _cliente(transporte),
        texto="contrato",
        limite=5,
        actor=ACTOR,
    )
    assert resultado.delegada is False
    assert transporte.llamadas == []


async def test_suspendido_buscar_anota_el_respaldo(monkeypatch: pytest.MonkeyPatch) -> None:
    _kek(monkeypatch)
    transporte = _Transporte()
    conn = _Conn(credencial=_credencial(), filas=[], suspendido=True)
    resultado = await consulta.buscar(
        conn, "demo", _cliente(transporte), texto="contrato", limite=5, actor=ACTOR
    )
    assert resultado.delegada is False
    assert transporte.llamadas == []
    assert conn.escrituras == [("fallback", "local")]


async def test_search_publica_el_origen_cuando_delega(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _conn = client_with_conn
    ident = uuid.uuid4()

    async def delega(*_args: Any, **_kwargs: Any) -> consulta.Busqueda:
        from knowledge.core.rechequeo import Hallazgo

        return consulta.Busqueda(True, (Hallazgo(ident, "2026-E-1", "cita viva", 0.9, 2, None),))

    async def prohibido(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("no debía ir al índice local")

    monkeypatch.setattr(rutas.consulta, "buscar", delega)
    monkeypatch.setattr(rutas.document_api, "search", prohibido)

    resp = await client.post(
        "/api/v1/knowledge/search",
        json={"query": "contrato"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    [hit] = resp.json()
    assert hit["origin"] == "ppi"
    assert hit["source_ref"] == "2026-E-1"
    assert hit["texto"] == "cita viva"


async def test_el_transporte_declara_la_version(monkeypatch: pytest.MonkeyPatch) -> None:
    recibidas: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        recibidas.append(req)
        return httpx.Response(200, json={"mode": "search", "hits": []})

    from knowledge.core import salida_http, token_ppi
    from knowledge.core.salida_http import PPI_VERSION, TransporteHttp

    async def _portador(_conn: Any, _slug: str) -> str:
        return "token-de-servicio"

    monkeypatch.setattr(token_ppi, "portador", _portador)
    transporte = TransporteHttp()
    await transporte.cerrar()
    transporte._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with salida_http.con_entidad(object(), "demo"):
            await transporte.request(
                "POST",
                "http://ppi.internal/v1/query",
                json={"mode": "search"},
                timeout=3,
            )
    finally:
        await transporte.cerrar()
    assert recibidas[0].headers["PPI-Version"] == PPI_VERSION
    assert recibidas[0].headers["Authorization"] == "Bearer token-de-servicio"


async def test_el_transporte_no_llama_sin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    recibidas: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        recibidas.append(req)
        return httpx.Response(200, json={})

    from knowledge.core import salida_http, token_ppi
    from knowledge.core.salida_http import TransporteHttp

    async def _portador(_conn: Any, _slug: str) -> str:
        raise RuntimeError("sin token de servicio")

    monkeypatch.setattr(token_ppi, "portador", _portador)
    transporte = TransporteHttp()
    await transporte.cerrar()
    transporte._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with salida_http.con_entidad(object(), "demo"):
            with pytest.raises(RuntimeError, match="sin token"):
                await transporte.request("GET", "http://ppi.internal/v1/capabilities")
        with pytest.raises(RuntimeError, match="sin entidad"):
            await transporte.request("GET", "http://ppi.internal/v1/capabilities")
    finally:
        await transporte.cerrar()
    assert recibidas == []
