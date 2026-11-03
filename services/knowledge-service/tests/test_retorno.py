# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El retorno exige firma, niega lo que ya no sale y sirve los bytes que sí."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from orpycamcp_common.kek import decode_aes256, seal

from knowledge.clients import documento
from knowledge.clients.documento import DocumentoError
from knowledge.core import database
from knowledge.core.clave_retorno import aad
from knowledge.core.config import settings
from knowledge.core.firma_retorno import comprobar
from knowledge.main import app
from knowledge.routers import retorno as ruta

KEK = b"\x44" * 32
SECRETO = b"\x11" * 32
RUTA = "/ppi/v1/demo/content"
ANEXO = uuid.UUID("00000000-0000-4000-8000-0000000000c1")
OCTETOS = b"hola-anexo"
SHA = hashlib.sha256(OCTETOS).hexdigest()
_FILA = {"sha256": SHA, "nivel_seguridad": 1, "status": "radicado"}
_OMITIDO = object()


def _pedido(sha: str = SHA) -> bytes:
    return json.dumps({"locator": f"att:{ANEXO}:{sha}", "sha256": sha}).encode()


def _firmar(
    cuerpo: bytes,
    *,
    nonce: str = "nonce-de-prueba-1",
    marca: int | None = None,
    secreto: bytes = SECRETO,
    kid: str = "return-1",
) -> dict[str, str]:
    instante = int(time.time()) if marca is None else marca
    huella = hashlib.sha256(cuerpo).hexdigest()
    entrada = f"v1\nPOST\n{RUTA}\n{instante}\n{nonce}\n{huella}".encode()
    firma = hmac.new(secreto, entrada, hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(firma).rstrip(b"=").decode("ascii")
    return {
        "PPI-Version": "1.2.0",
        "PPI-Key-Id": kid,
        "PPI-Timestamp": str(instante),
        "PPI-Nonce": nonce,
        "PPI-Signature": f"v1={token}",
    }


class _Redis:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.claves: set[str] = set()

    async def set(
        self, key: str, value: str, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        if self.error is not None:
            raise self.error
        if nx and key in self.claves:
            return None
        self.claves.add(key)
        return True


class _Conn:
    def __init__(
        self,
        *,
        proveedor: str = "ppi",
        niveles: list[int] | None = None,
        suspendido: bool = False,
        anexo: Any = _OMITIDO,
        credencial: dict[str, Any] | None = None,
    ) -> None:
        self.proveedor = proveedor
        self.niveles = [1] if niveles is None else niveles
        self.suspendido = suspendido
        self.anexo = _FILA if anexo is _OMITIDO else anexo
        self.credencial = credencial
        self.sql: list[str] = []
        self.escrituras: list[tuple[Any, ...]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        self.escrituras.append(args)
        return "OK"

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.sql.append(sql)
        if "conexion:retorno" in sql:
            return self.credencial
        if "public.tenants" in sql:
            return {
                "asistente_habilitado": True,
                "intelligence_provider": self.proveedor,
                "intelligence_levels": self.niveles,
                "intelligence_send_metadata": False,
            }
        if "intelligence_state" in sql:
            return {"send_suspended": self.suspendido}
        if "retorno:anexo" in sql:
            return self.anexo
        raise AssertionError(sql)


def _credencial() -> dict[str, Any]:
    clave = decode_aes256(base64.b64encode(KEK).decode(), "INTELLIGENCE_CREDENTIAL_KEK")
    ciphertext, nonce = seal(SECRETO, key=clave, aad=aad("demo", 1))
    return {"ciphertext": ciphertext, "nonce": nonce, "kek_version": 1}


def _montar(
    monkeypatch: pytest.MonkeyPatch, conn: _Conn, redis: _Redis, octetos: Any
) -> list[uuid.UUID]:
    pedidos: list[uuid.UUID] = []

    @asynccontextmanager
    async def _adquirir(slug: str):
        assert slug == "demo"
        yield conn

    async def _leer(slug: str, anexo_id: uuid.UUID) -> bytes | None:
        pedidos.append(anexo_id)
        if isinstance(octetos, Exception):
            raise octetos
        return octetos

    monkeypatch.setattr(settings, "intelligence_credential_kek", base64.b64encode(KEK).decode())
    monkeypatch.setattr(database, "acquire_tenant", _adquirir)
    monkeypatch.setattr(ruta, "get_redis", lambda: redis)
    monkeypatch.setattr(ruta.documento, "leer_anexo", _leer)
    return pedidos


async def _post(cuerpo: bytes, headers: dict[str, str]) -> httpx.Response:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as cliente:
        return await cliente.post(RUTA, content=cuerpo, headers=headers)


def test_una_firma_valida_cuadra_y_otra_clave_no() -> None:
    cuerpo = _pedido()
    cabeceras = _firmar(cuerpo)
    ahora = time.time()
    assert (
        comprobar(
            cuerpo, metodo="POST", ruta=RUTA, cabeceras=cabeceras, secreto=SECRETO, ahora=ahora
        )
        is None
    )
    otra = comprobar(
        cuerpo,
        metodo="POST",
        ruta=RUTA,
        cabeceras=cabeceras,
        secreto=b"\x22" * 32,
        ahora=ahora,
    )
    assert otra == "firma"


async def test_sirve_los_bytes_con_su_huella(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _Conn(credencial=_credencial())
    pedidos = _montar(monkeypatch, conn, _Redis(), OCTETOS)
    cuerpo = _pedido()
    resp = await _post(cuerpo, _firmar(cuerpo))
    assert resp.status_code == 200
    assert resp.content == OCTETOS
    assert resp.headers["ppi-content-sha256"] == SHA
    assert pedidos == [ANEXO]


async def test_una_firma_mala_no_pide_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _Redis()
    pedidos = _montar(monkeypatch, _Conn(credencial=_credencial()), redis, OCTETOS)
    cuerpo = _pedido()
    cabeceras = _firmar(cuerpo)
    cabeceras["PPI-Signature"] = "v1=aaaa"
    resp = await _post(cuerpo, cabeceras)
    assert resp.status_code == 401
    assert resp.json()["code"] == "signature_invalid"
    assert redis.claves == set()
    assert pedidos == []


async def test_un_instante_viejo_no_reclama_el_nonce(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _Redis()
    _montar(monkeypatch, _Conn(credencial=_credencial()), redis, OCTETOS)
    cuerpo = _pedido()
    resp = await _post(cuerpo, _firmar(cuerpo, marca=int(time.time()) - 400))
    assert resp.status_code == 401
    assert redis.claves == set()


async def test_el_nonce_repetido_se_rechaza(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _Redis()
    _montar(monkeypatch, _Conn(credencial=_credencial()), redis, OCTETOS)
    cuerpo = _pedido()
    cabeceras = _firmar(cuerpo, nonce="nonce-repetido-01")
    assert (await _post(cuerpo, cabeceras)).status_code == 200
    repetido = await _post(cuerpo, cabeceras)
    assert repetido.status_code == 401
    assert repetido.json()["code"] == "replayed_nonce"


async def test_un_nivel_no_habilitado_da_410_sin_pedir_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reservada = {"sha256": SHA, "nivel_seguridad": 2, "status": "radicado"}
    conn = _Conn(credencial=_credencial(), anexo=reservada)
    pedidos = _montar(monkeypatch, conn, _Redis(), OCTETOS)
    cuerpo = _pedido()
    resp = await _post(cuerpo, _firmar(cuerpo, nonce="nonce-de-nivel-02"))
    assert resp.status_code == 410
    assert resp.json()["code"] == "gone"
    assert pedidos == []
    assert conn.escrituras == [("barrier", "nivel")]


async def test_en_local_no_pide_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _Conn(proveedor="local", credencial=_credencial())
    pedidos = _montar(monkeypatch, conn, _Redis(), OCTETOS)
    cuerpo = _pedido()
    resp = await _post(cuerpo, _firmar(cuerpo, nonce="nonce-en-local-01"))
    assert resp.status_code == 410
    assert pedidos == []
    assert conn.escrituras == []


async def test_un_localizador_desconocido_da_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _montar(monkeypatch, _Conn(credencial=_credencial(), anexo=None), _Redis(), OCTETOS)
    cuerpo = _pedido()
    resp = await _post(cuerpo, _firmar(cuerpo, nonce="nonce-ausente-01"))
    assert resp.status_code == 404
    assert resp.json()["code"] == "scope_not_found"


async def test_si_los_bytes_no_cuadran_no_salen(monkeypatch: pytest.MonkeyPatch) -> None:
    _montar(monkeypatch, _Conn(credencial=_credencial()), _Redis(), b"otros")
    cuerpo = _pedido()
    resp = await _post(cuerpo, _firmar(cuerpo, nonce="nonce-distinto-1"))
    assert resp.status_code == 409
    assert resp.json()["code"] == "content_mismatch"
    assert resp.content != b"otros"


async def test_si_document_no_lo_tiene_es_410(monkeypatch: pytest.MonkeyPatch) -> None:
    _montar(monkeypatch, _Conn(credencial=_credencial()), _Redis(), None)
    cuerpo = _pedido()
    resp = await _post(cuerpo, _firmar(cuerpo, nonce="nonce-borrado-01"))
    assert resp.status_code == 410


async def test_si_document_falla_es_503(monkeypatch: pytest.MonkeyPatch) -> None:
    _montar(monkeypatch, _Conn(credencial=_credencial()), _Redis(), DocumentoError("red"))
    cuerpo = _pedido()
    resp = await _post(cuerpo, _firmar(cuerpo, nonce="nonce-caido-0001"))
    assert resp.status_code == 503
    assert resp.json()["code"] == "unavailable"


async def test_si_redis_no_responde_no_sirve(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _Redis(error=ConnectionError("redis"))
    _montar(monkeypatch, _Conn(credencial=_credencial()), redis, OCTETOS)
    cuerpo = _pedido()
    resp = await _post(cuerpo, _firmar(cuerpo, nonce="nonce-sin-redis1"))
    assert resp.status_code == 503


async def test_el_cliente_pide_el_anexo_con_el_token(monkeypatch: pytest.MonkeyPatch) -> None:
    vistas: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        vistas.append(req)
        return httpx.Response(200, content=b"xyz")

    real = httpx.AsyncClient

    def cliente(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(documento.httpx, "AsyncClient", cliente)
    monkeypatch.setattr(settings, "document_internal_token", "secreto-interno")
    assert await documento.leer_anexo("demo", ANEXO) == b"xyz"
    assert vistas[0].headers["x-internal-token"] == "secreto-interno"
    assert vistas[0].url.path.endswith(f"/anexos/{ANEXO}/contenido")


async def test_el_cliente_pide_el_registro_con_el_token(monkeypatch: pytest.MonkeyPatch) -> None:
    vistas: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        vistas.append(req)
        return httpx.Response(200, json={"id": str(ANEXO), "anexos": []})

    real = httpx.AsyncClient

    def cliente(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(documento.httpx, "AsyncClient", cliente)
    monkeypatch.setattr(settings, "document_internal_token", "secreto-interno")
    cuerpo = await documento.leer_registro("demo", ANEXO)
    assert cuerpo is not None and cuerpo["anexos"] == []
    assert vistas[0].headers["x-internal-token"] == "secreto-interno"
    assert vistas[0].url.path.endswith(f"/radicados/{ANEXO}")


async def test_sin_token_interno_no_llama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "document_internal_token", "")

    def cliente(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        raise AssertionError("no debía llamar")

    monkeypatch.setattr(documento.httpx, "AsyncClient", cliente)
    with pytest.raises(DocumentoError):
        await documento.leer_anexo("demo", ANEXO)
