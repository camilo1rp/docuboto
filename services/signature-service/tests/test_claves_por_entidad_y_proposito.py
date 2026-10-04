# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""
AREA-04-11 — claves por entidad y por propósito; el OTP ata el desafío.

El HMAC del OTP y el de la firma nativa usaban el mismo secreto raíz para todas
las entidades. Ahora cada (propósito, entidad) tiene su clave HKDF, y el OTP ata
además el ``challenge_id``. Las firmas y los desafíos anteriores (``key_version=0``)
siguen verificándose con el esquema original.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.core.otp import hmac_otp, verify_otp
from tests.conftest import TENANT_HEADERS
from tests.test_sign_xml_xades import _FakeFirmaTable  # tabla `firma` falsa con INSERT/SELECT

C1 = uuid.UUID("11111111-aaaa-aaaa-aaaa-111111111111")
C2 = uuid.UUID("22222222-aaaa-aaaa-aaaa-222222222222")
SECRETO = settings.signature_secret


def _otp(otp="123456", *, tenant="a", challenge=C1, v=1) -> str:
    return hmac_otp(otp, SECRETO, tenant_slug=tenant, challenge_id=challenge, key_version=v)


def test_otp_hash_distinto_por_challenge() -> None:
    """El mismo código en dos desafíos produce hashes distintos."""
    assert _otp(challenge=C1) != _otp(challenge=C2)


def test_otp_hash_distinto_por_tenant() -> None:
    assert _otp(tenant="a") != _otp(tenant="b")


def test_otp_verifica_solo_con_su_challenge_y_su_tenant() -> None:
    h = _otp()
    ok = {"key_version": 1, "tenant_slug": "a", "challenge_id": C1}
    assert verify_otp("123456", h, SECRETO, **ok) is True
    assert verify_otp("123456", h, SECRETO, **{**ok, "challenge_id": C2}) is False
    assert verify_otp("123456", h, SECRETO, **{**ok, "tenant_slug": "b"}) is False
    assert verify_otp("654321", h, SECRETO, **ok) is False


def test_otp_legado_key_version_0_sigue_verificando() -> None:
    """El hash de un desafío anterior a la 020, calculado con la fórmula literal de
    entonces (HMAC del código con la clave raíz), se acepta mientras no expira."""
    legado = hmac.new(SECRETO.encode(), b"123456", digestmod=hashlib.sha256).hexdigest()
    assert (
        verify_otp(
            "123456", legado, SECRETO, key_version=0, tenant_slug="a", challenge_id=C1
        )
        is True
    )
    # …y no se hace pasar por v1.
    assert (
        verify_otp(
            "123456", legado, SECRETO, key_version=1, tenant_slug="a", challenge_id=C1
        )
        is False
    )


def test_otp_key_version_desconocida_no_verifica() -> None:
    assert (
        verify_otp("123456", _otp(), SECRETO, key_version=9, tenant_slug="a", challenge_id=C1)
        is False
    )


def test_el_mensaje_de_la_firma_nombra_entidad_y_objeto() -> None:
    """Defensa en profundidad: además de la clave por entidad, el MENSAJE que se
    autentica ata entidad, tipo y objeto (y es inequívoco: un ``|`` dentro de un
    identificador no mueve la frontera entre campos)."""
    from app.services.signer import _mensaje_v1

    base = {"tenant_slug": "a", "objeto_tipo": "radicado", "objeto_id": "X"}

    def m(**o) -> bytes:
        return _mensaje_v1("h", "f", "s", **{**base, **o})

    assert m() != m(tenant_slug="b")
    assert m() != m(objeto_tipo="expediente")
    assert m() != m(objeto_id="Y")
    assert m(objeto_id="a|b", objeto_tipo="c") != m(objeto_id="a", objeto_tipo="b|c")


def test_la_clave_del_otp_no_es_la_de_la_firma_nativa() -> None:
    """Dos propósitos, dos claves: el hash de un OTP no puede ser un MAC de firma."""
    from app.core.secret_derivation import derive_key

    assert derive_key(SECRETO, purpose="otp", tenant_slug="a") != derive_key(
        SECRETO, purpose="firma_nativa", tenant_slug="a"
    )


@pytest.mark.asyncio
async def test_firma_nativa_de_sign_xml_verifica_de_punta_a_punta(client_with_conn) -> None:
    """``/sign-xml`` nativa -> ``/verify``: la fila lleva el instante que quedó DENTRO
    del HMAC (antes se persistía otro ``now()`` y ninguna firma nativa de este camino
    llegó nunca a verificar) y la ``key_version`` con que se firmó."""
    client, conn = client_with_conn
    table = _FakeFirmaTable()
    conn.fetchrow.side_effect = table.fetchrow
    with patch(
        "app.routers.signature.CadenaRepository.objeto_visible",
        new=AsyncMock(return_value=True),
    ):
        resp = await client.post(
            "/api/v1/signature/sign-xml",
            json={"payload_xml": "<indice/>", "objeto_tipo": "radicado", "objeto_id": "exp-1"},
            headers=TENANT_HEADERS,
        )
    assert resp.status_code == 201, resp.text
    firma_id = resp.json()["firma_id"]
    assert table.rows[firma_id]["key_version"] == 1

    ok = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": "<indice/>"},
        headers=TENANT_HEADERS,
    )
    assert ok.json()["valida"] is True, ok.json()
    mal = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": "<indice ALTERADO/>"},
        headers=TENANT_HEADERS,
    )
    assert mal.json()["valida"] is False

    # Misma fila, otra entidad (restauración/import al schema de otro tenant): no verifica.
    otra = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": "<indice/>"},
        headers={**TENANT_HEADERS, "X-Tenant-Slug": "otro"},
    )
    assert otra.json()["valida"] is False


@pytest.mark.asyncio
async def test_verify_de_una_firma_anterior_a_la_020_usa_su_key_version(client_with_conn) -> None:
    """Fila ``key_version=0`` (HMAC con la fórmula literal anterior): ``/verify`` la
    lee de la fila y verifica con el esquema original, no con el actual."""
    from datetime import UTC, datetime

    client, conn = client_with_conn
    firmante = uuid.uuid4()
    sello = datetime(2024, 1, 1, tzinfo=UTC)
    sha = hashlib.sha256(b"<indice/>").hexdigest()
    blob = hmac.new(
        SECRETO.encode(),
        f"{sha}|{firmante}|{sello.astimezone(UTC).isoformat()}".encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()
    fila = {
        "id": uuid.uuid4(), "objeto_tipo": "indice", "objeto_id": "exp-1",
        "firmante_id": firmante, "payload_sha256": sha, "firma_blob": blob,
        "provider": "nativa", "sello_tiempo": sello, "created_at": sello,
        "key_version": 0,
    }
    conn.fetchrow.side_effect = [{"is_root": False, "max_level": None}, fila]
    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(fila["id"]), "payload_xml": "<indice/>"},
        headers=TENANT_HEADERS,
    )
    assert resp.json()["valida"] is True, resp.json()


def test_la_migracion_020_etiqueta_lo_existente_como_legado() -> None:
    """Las filas que ya existen se produjeron con el esquema anterior: el DEFAULT
    tiene que ser 0 (no 1) o dejarían de verificar al migrar; y el código nuevo
    escribe el valor actual explícito."""
    sql = (
        Path(__file__).resolve().parents[1] / "migrations" / "tenant" / "020_key_version_hkdf.sql"
    ).read_text(encoding="utf-8")
    sin_comentarios = re.sub(r"--[^\n]*", "", sql)
    for tabla in ("firma", "firma_challenge"):
        assert re.search(
            rf"ALTER TABLE {tabla}\s+ADD COLUMN IF NOT EXISTS key_version SMALLINT NOT NULL "
            r"DEFAULT 0\s+CHECK \(key_version IN \(0, 1\)\)",
            sin_comentarios,
        ), tabla
