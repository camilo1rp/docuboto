# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La conexión fija la huella firmada y suspende el envío si esa huella cambia."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from knowledge.clients.ppi import ClientePpi
from knowledge.core.conexion import asegurar
from knowledge.core.config import settings
from knowledge.core.firma_capabilities import verificar
from knowledge.core.jcs import canonico
from knowledge.core.salida_http import PPI_VERSION

KEK = b"\x44" * 32
KID = "pin-1"
_PRIVADA = ed25519.Ed25519PrivateKey.generate()
_PEM = _PRIVADA.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()


def _statement(**cambios: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "protocol_version": "1.2.0",
        "provider": {"id": "example-provider", "name": "Example", "version": "1"},
        "connection_id": "conn-1",
        "accepted_levels": ["public"],
        "routing": [],
        "subprocessors": [],
        "recommendation_kinds": [],
        "run_tasks": [],
        "limits": {"max_attachment_bytes": 10, "max_batch_items": 100},
        "issued_at": "2026-10-27T14:05:09Z",
        "expires_at": "2099-01-01T00:00:00Z",
    }
    base.update(cambios)
    return base


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _jws(
    statement: dict[str, Any],
    *,
    privada: Any = _PRIVADA,
    alg: str = "EdDSA",
    kid: str = KID,
) -> str:
    encabezado = _b64url(json.dumps({"alg": alg, "kid": kid}, separators=(",", ":")).encode())
    carga = _b64url(canonico(statement))
    entrada = f"{encabezado}.{carga}".encode()
    if alg == "ES256":
        der = privada.sign(entrada, ec.ECDSA(hashes.SHA256()))
        ere, ese = decode_dss_signature(der)
        firma = ere.to_bytes(32, "big") + ese.to_bytes(32, "big")
    else:
        firma = privada.sign(entrada)
    return f"{encabezado}..{_b64url(firma)}"


def _firmado(statement: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    cuerpo = _statement() if statement is None else statement
    return {"statement": cuerpo, "jws": _jws(cuerpo, **kwargs)}


class _Conn:
    def __init__(
        self,
        *,
        proveedor: str = "ppi",
        niveles: list[int] | None = None,
        huella: str | None = None,
    ) -> None:
        self.proveedor = proveedor
        self.niveles = [1] if niveles is None else niveles
        self.huella = huella
        self.suspendido = False
        self.credencial: dict[str, Any] | None = None
        self.inserts = 0

    async def execute(self, sql: str, *args: Any) -> str:
        if "capabilities_sha256" in sql and args:
            self.huella = args[0]
        if "send_suspended = true" in sql:
            self.suspendido = True
        if "INSERT INTO intelligence_credentials" in sql:
            self.inserts += 1
            self.credencial = {"ciphertext": args[2], "nonce": args[3], "kek_version": 1}
        return "OK"

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        if "public.tenants" in sql:
            return {
                "asistente_habilitado": True,
                "intelligence_provider": self.proveedor,
                "intelligence_levels": self.niveles,
                "intelligence_send_metadata": False,
            }
        if "conexion:huella" in sql:
            return {"capabilities_sha256": self.huella}
        if "conexion:retorno" in sql:
            return self.credencial
        if "intelligence_state" in sql:
            return {"send_suspended": self.suspendido}
        raise AssertionError(sql)


class _Transporte:
    def __init__(
        self, firmado: dict[str, Any] | None = None, error: BaseException | None = None
    ) -> None:
        self.firmado = _firmado() if firmado is None else firmado
        self.error = error
        self.llamadas: list[tuple[str, str, dict[str, Any] | None]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        self.llamadas.append((method, url, json))
        if method == "GET" and self.error is not None:
            raise self.error
        if method == "GET":
            return self.firmado
        return {
            "connection_id": "conn-1",
            "accepted_levels": ["public"],
            "rejected_levels": [],
            "recommendation_kinds": [],
            "key_ids": ["return-1"],
            "updated_at": "2026-10-31T00:00:00Z",
        }


def _cliente(transporte: _Transporte) -> ClientePpi:
    return ClientePpi(transporte, base_url="http://ppi.internal")


def _ajustar(monkeypatch: pytest.MonkeyPatch, *, base: str = "https://host.example") -> None:
    monkeypatch.setattr(settings, "ppi_capabilities_public_key", _PEM)
    monkeypatch.setattr(settings, "ppi_capabilities_kid", KID)
    monkeypatch.setattr(settings, "ppi_callback_base_url", base)
    monkeypatch.setattr(settings, "intelligence_credential_kek", base64.b64encode(KEK).decode())


def _puts(transporte: _Transporte) -> list[dict[str, Any]]:
    return [cuerpo for metodo, _url, cuerpo in transporte.llamadas if metodo == "PUT" and cuerpo]


def test_el_canonico_ordena_claves_y_escapa() -> None:
    assert canonico({"b": 1, "a": "x\n"}) == b'{"a":"x\\n","b":1}'


def test_la_huella_no_cambia_si_solo_cambia_el_plazo() -> None:
    primera = verificar(_firmado(_statement()), pem=_PEM, kid=KID)
    segunda = verificar(
        _firmado(
            _statement(
                issued_at="2026-11-05T16:00:00Z",
                expires_at="2099-06-01T00:00:00Z",
                connection_id="pending",
            )
        ),
        pem=_PEM,
        kid=KID,
    )
    assert primera.ok is True
    assert segunda.ok is True
    assert primera.huella == segunda.huella


def test_la_firma_eddsa_sostiene_y_otra_clave_no() -> None:
    statement = _statement()
    veredicto = verificar(_firmado(statement), pem=_PEM, kid=KID)
    assert veredicto.ok is True
    assert veredicto.huella is not None
    otra = ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    assert verificar(_firmado(statement), pem=otra, kid=KID).ok is False
    assert verificar(_firmado(statement), pem=_PEM, kid="otro").motivo == "kid"


def test_la_firma_es256_sostiene() -> None:
    privada = ec.generate_private_key(ec.SECP256R1())
    pem = privada.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    statement = _statement()
    veredicto = verificar(_firmado(statement, privada=privada, alg="ES256"), pem=pem, kid=KID)
    assert veredicto.ok is True


async def test_en_local_no_hay_llamada(monkeypatch: pytest.MonkeyPatch) -> None:
    _ajustar(monkeypatch)
    transporte = _Transporte()
    conn = _Conn(proveedor="local")
    await asegurar(conn, "demo", _cliente(transporte))
    assert transporte.llamadas == []
    assert conn.suspendido is False


async def test_sin_clave_fijada_no_hay_llamada_ni_suspension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ajustar(monkeypatch)
    monkeypatch.setattr(settings, "ppi_capabilities_public_key", "")
    transporte = _Transporte()
    conn = _Conn()
    await asegurar(conn, "demo", _cliente(transporte))
    assert transporte.llamadas == []
    assert conn.suspendido is False
    assert conn.huella is None


async def test_la_primera_declaracion_valida_fija_la_huella_y_declara(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ajustar(monkeypatch)
    statement = _statement()
    transporte = _Transporte(_firmado(statement))
    conn = _Conn()
    await asegurar(conn, "demo", _cliente(transporte))
    assert conn.suspendido is False
    assert conn.huella == verificar(_firmado(statement), pem=_PEM, kid=KID).huella
    [cuerpo] = _puts(transporte)
    assert cuerpo["protocol_version"] == PPI_VERSION
    assert cuerpo["sensitivity_levels"] == [{"code": "public", "label": "Pública"}]
    assert cuerpo["vocabularies"] == {"document_types": [], "units": []}
    assert cuerpo["callbacks"]["recommend_url"] == (
        "https://host.example/ppi/v1/demo/recommendations"
    )
    assert cuerpo["callbacks"]["content_url"] == "https://host.example/ppi/v1/demo/content"
    assert cuerpo["callbacks"]["keys"][0]["kid"] == "return-1"
    assert len(base64.b64decode(cuerpo["callbacks"]["keys"][0]["secret_base64"])) == 32
    assert transporte.llamadas[0][0] == "GET"
    assert transporte.llamadas[0][1].endswith("/v1/capabilities")


async def test_la_misma_huella_no_suspende_y_reutiliza_la_clave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ajustar(monkeypatch)
    statement = _statement()
    huella = verificar(_firmado(statement), pem=_PEM, kid=KID).huella
    transporte = _Transporte(_firmado(statement))
    conn = _Conn(huella=huella)
    await asegurar(conn, "demo", _cliente(transporte))
    await asegurar(conn, "demo", _cliente(transporte))
    assert conn.suspendido is False
    assert conn.huella == huella
    assert conn.inserts == 1
    primero, segundo = _puts(transporte)
    assert primero["callbacks"]["keys"] == segundo["callbacks"]["keys"]


async def test_una_huella_distinta_suspende_y_no_declara(monkeypatch: pytest.MonkeyPatch) -> None:
    _ajustar(monkeypatch)
    anterior = "ab" * 32
    transporte = _Transporte()
    conn = _Conn(huella=anterior)
    await asegurar(conn, "demo", _cliente(transporte))
    assert conn.suspendido is True
    assert conn.huella == anterior
    assert _puts(transporte) == []


async def test_una_firma_invalida_suspende_sin_fijar_huella(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ajustar(monkeypatch)
    statement = _statement()
    malo = _firmado(statement)
    malo["jws"] = _jws(statement, privada=ed25519.Ed25519PrivateKey.generate())
    transporte = _Transporte(malo)
    conn = _Conn()
    await asegurar(conn, "demo", _cliente(transporte))
    assert conn.suspendido is True
    assert conn.huella is None
    assert _puts(transporte) == []


async def test_una_declaracion_vencida_suspende(monkeypatch: pytest.MonkeyPatch) -> None:
    _ajustar(monkeypatch)
    statement = _statement(expires_at="2020-01-01T00:00:00Z")
    transporte = _Transporte(_firmado(statement))
    conn = _Conn()
    await asegurar(conn, "demo", _cliente(transporte))
    assert conn.suspendido is True
    assert conn.huella is None


async def test_si_capabilities_no_responde_no_suspende(monkeypatch: pytest.MonkeyPatch) -> None:
    _ajustar(monkeypatch)
    transporte = _Transporte(error=TimeoutError("plazo"))
    conn = _Conn()
    await asegurar(conn, "demo", _cliente(transporte))
    assert conn.suspendido is False
    assert conn.huella is None
    assert _puts(transporte) == []


async def test_sin_url_de_retorno_fija_la_huella_y_no_declara(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ajustar(monkeypatch, base="")
    transporte = _Transporte()
    conn = _Conn()
    await asegurar(conn, "demo", _cliente(transporte))
    assert conn.huella is not None
    assert conn.suspendido is False
    assert _puts(transporte) == []
