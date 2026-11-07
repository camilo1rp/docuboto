# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Ingesta de preparación: record padre, adjunto en línea, estado y extracción."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from knowledge.clients.ppi import PLAZO_EXTRACT_S, ClientePpi
from knowledge.clients.storage import TOPE, Archivo, Falta
from knowledge.core import ingesta
from tests.conftest import TENANT_HEADERS

pytestmark = pytest.mark.asyncio

ARCHIVO_ID = uuid.UUID("00000000-0000-4000-8000-0000000000d4")
BYTES = b"%PDF-1.4 test"
SHA = hashlib.sha256(BYTES).hexdigest()
CUANDO = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)


class _Conn:
    def __init__(self, **datos: Any) -> None:
        self.datos = datos
        self.escrituras: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        self.escrituras.append((sql, args))
        return "INSERT 0 1"

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        if "public.tenants" in sql:
            return {
                "asistente_habilitado": self.datos.get("bandera", True),
                "intelligence_provider": self.datos.get("proveedor", "ppi"),
                "intelligence_levels": self.datos.get("niveles", [1]),
                "intelligence_send_metadata": False,
            }
        if "intelligence_state" in sql:
            return {"send_suspended": self.datos.get("suspendido", False)}
        raise AssertionError(sql)


class _Transporte:
    def __init__(self, respuestas: dict[str, Any] | None = None, error: BaseException | None = None) -> None:
        self.respuestas = respuestas or {}
        self.error = error
        self.llamadas: list[tuple[str, str, dict[str, Any] | None]] = []
        self.timeouts: list[float | None] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        del params
        self.llamadas.append((method, url, json))
        self.timeouts.append(timeout)
        if self.error is not None:
            raise self.error
        if url.endswith("/status"):
            return self.respuestas.get(
                "status", {"external_ref": "staging-1.file", "ingest_state": "indexed"}
            )
        if url.endswith("/extract"):
            return self.respuestas.get(
                "extract", {"external_ref": "staging-1.file", "values": {"direction": "E"}}
            )
        return {"results": [{"status": "stored"}]}


def _cliente(transporte: _Transporte) -> ClientePpi:
    return ClientePpi(transporte, base_url="http://ppi.internal")


def _archivo() -> Archivo:
    return Archivo(
        id=ARCHIVO_ID,
        filename="solicitud.pdf",
        media_type="application/pdf",
        size_bytes=len(BYTES),
        sha256=SHA,
        contenido=BYTES,
    )


async def _leer(_ident: uuid.UUID, _slug: str) -> Archivo | Falta:
    return _archivo()


async def test_crear_manda_el_adjunto_en_linea() -> None:
    transporte = _Transporte()
    resultado = await ingesta.crear(
        _Conn(),
        "demo",
        _cliente(transporte),
        ARCHIVO_ID,
        leer=_leer,
        ahora=CUANDO,
        staging_id="staging-1",
    )
    assert resultado.sale is True
    assert resultado.cuerpo == {
        "staging_ref": "staging-1",
        "file_ref": "staging-1.file",
        "file_id": str(ARCHIVO_ID),
        "filename": "solicitud.pdf",
        "size_bytes": len(BYTES),
        "sha256": SHA,
    }
    assert len(transporte.llamadas) == 1
    metodo, url, lote = transporte.llamadas[0]
    assert metodo == "POST" and url == "http://ppi.internal/v1/ingest"
    assert lote is not None
    record = lote["items"][0]["record"]
    adjunto = lote["items"][1]["attachment"]
    assert record["subject"] == "solicitud.pdf"
    assert record["registration_number"] == "staging-1"
    assert record["direction"] == "incoming"
    assert adjunto["content"]["mode"] == "inline"
    assert adjunto["parent_ref"] == "staging-1"


async def test_en_local_no_llama() -> None:
    transporte = _Transporte()
    resultado = await ingesta.crear(
        _Conn(proveedor="local"),
        "demo",
        _cliente(transporte),
        ARCHIVO_ID,
        leer=_leer,
        staging_id="staging-1",
    )
    assert resultado.sale is False and resultado.motivo == "local"
    assert transporte.llamadas == []


async def test_un_archivo_que_no_existe_no_llama() -> None:
    async def vacio(_ident: uuid.UUID, _slug: str) -> Archivo | Falta:
        return Falta("file_not_found")

    transporte = _Transporte()
    resultado = await ingesta.crear(
        _Conn(), "demo", _cliente(transporte), ARCHIVO_ID, leer=vacio
    )
    assert resultado.sale is False and resultado.motivo == "file_not_found"
    assert transporte.llamadas == []


async def test_un_archivo_que_excede_el_tope_no_llama() -> None:
    async def grande(_ident: uuid.UUID, _slug: str) -> Archivo | Falta:
        return Falta("too_large")

    transporte = _Transporte()
    resultado = await ingesta.crear(
        _Conn(), "demo", _cliente(transporte), ARCHIVO_ID, leer=grande
    )
    assert resultado.sale is False and resultado.motivo == "too_large"
    assert TOPE == 104_857_600
    assert transporte.llamadas == []


async def test_el_estado_sigue_los_cuatro_valores() -> None:
    transporte = _Transporte(
        {"status": {"external_ref": "staging-1.file", "ingest_state": "processing"}}
    )
    resultado = await ingesta.estado(
        _Conn(), "demo", _cliente(transporte), "staging-1.file"
    )
    assert resultado.sale is True
    assert resultado.cuerpo is not None
    assert resultado.cuerpo["ingest_state"] == "processing"
    metodo, url, _cuerpo = transporte.llamadas[0]
    assert metodo == "GET"
    assert url == "http://ppi.internal/v1/ingest/staging-1.file/status"


async def test_extraer_devuelve_el_valor_listado() -> None:
    transporte = _Transporte()
    resultado = await ingesta.extraer(
        _Conn(),
        "demo",
        _cliente(transporte),
        {
            "external_ref": "staging-1.file",
            "fields": [
                {
                    "key": "direction",
                    "kind": "choice",
                    "choices": [{"value": "E", "label": "Entrada"}],
                }
            ],
        },
    )
    assert resultado.sale is True
    assert resultado.cuerpo is not None
    assert resultado.cuerpo["values"] == {"direction": "E"}
    metodo, url, cuerpo = transporte.llamadas[0]
    assert metodo == "POST" and url == "http://ppi.internal/v1/extract"
    assert cuerpo is not None and cuerpo["external_ref"] == "staging-1.file"
    assert transporte.timeouts[0] == PLAZO_EXTRACT_S


async def test_not_indexed_sale_como_motivo() -> None:
    respuesta = httpx.Response(409, json={"code": "not_indexed"})
    error = httpx.HTTPStatusError("conflicto", request=httpx.Request("POST", "http://x"), response=respuesta)
    transporte = _Transporte(error=error)
    resultado = await ingesta.extraer(
        _Conn(),
        "demo",
        _cliente(transporte),
        {"external_ref": "staging-1.file", "fields": [{"key": "a", "kind": "text"}]},
    )
    assert resultado.sale is False and resultado.motivo == "not_indexed"


async def test_borrar_manda_la_eliminacion_del_stub() -> None:
    transporte = _Transporte()
    resultado = await ingesta.borrar(_Conn(), "demo", _cliente(transporte), "staging-1")
    assert resultado.sale is True
    _metodo, _url, lote = transporte.llamadas[0]
    assert lote is not None
    assert lote["items"][0]["deletion"]["external_ref"] == "staging-1"
    assert lote["items"][0]["deletion"]["include_children"] is True


async def test_la_ruta_crea_y_consulta(client_with_conn, monkeypatch: pytest.MonkeyPatch) -> None:
    from knowledge.core import ingesta as modulo

    async def falso_crear(*_args: object, **_kwargs: object) -> ingesta.Resultado:
        return ingesta.Resultado(
            True,
            "ok",
            {
                "staging_ref": "staging-1",
                "file_ref": "staging-1.file",
                "file_id": str(ARCHIVO_ID),
                "filename": "solicitud.pdf",
                "size_bytes": 4,
                "sha256": SHA,
            },
        )

    monkeypatch.setattr(modulo, "crear", falso_crear)
    client, _conn = client_with_conn
    resp = await client.post(
        "/api/v1/knowledge/ingesta",
        json={"file_id": str(ARCHIVO_ID)},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["file_ref"] == "staging-1.file"

    async def falso_estado(*_args: object, **_kwargs: object) -> ingesta.Resultado:
        return ingesta.Resultado(
            True, "ok", {"external_ref": "staging-1.file", "ingest_state": "indexed"}
        )

    monkeypatch.setattr(modulo, "estado", falso_estado)
    visto = await client.get("/api/v1/knowledge/ingesta/staging-1.file", headers=TENANT_HEADERS)
    assert visto.status_code == 200
    assert visto.json()["ingest_state"] == "indexed"
