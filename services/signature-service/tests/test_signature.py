# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del signature-service (E06, ADR-016, RF-FIR-11..14, D-01)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.database import get_tenant_conn
from app.main import app
from app.services import signer
from tests.conftest import TENANT_HEADERS, USER

# Fixed timestamp used to build test firma rows so verify tests are deterministic.
SELLO_DT = datetime(2024, 1, 1, tzinfo=UTC)
SELLO_STR = SELLO_DT.astimezone(UTC).isoformat()
NOW = datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Unit tests — signer module
# ---------------------------------------------------------------------------


TENANT = "demo"
OBJ = {"tenant_slug": TENANT, "objeto_tipo": "indice", "objeto_id": "exp-1"}


def _firmar(payload: bytes = b"<indice/>", **over) -> dict:
    kw = {**OBJ, **over}
    return signer.firmar(payload, str(USER), SELLO_STR, settings.signature_secret, **kw)


def _verificar(firma: dict, payload: bytes = b"<indice/>", **over) -> bool:
    kw = {"key_version": firma["key_version"], **OBJ, **over}
    firmante = kw.pop("firmante", str(USER))
    sello = kw.pop("sello", SELLO_STR)
    secret = kw.pop("secret", settings.signature_secret)
    return signer.verificar(payload, firmante, firma["firma_blob"], sello, secret, **kw)


def test_signer_hmac_verify_roundtrip() -> None:
    """Sign and verify succeed for same inputs."""
    assert _verificar(_firmar()) is True


def test_signer_detects_tampered_payload() -> None:
    assert _verificar(_firmar(), b"<indice ALTERADO/>") is False


def test_signer_detects_wrong_firmante() -> None:
    assert _verificar(_firmar(), firmante=str(uuid.uuid4())) is False


def test_signer_non_reproducible_without_secret() -> None:
    """firma_blob is not reproducible by a caller who doesn't know the secret."""
    assert _verificar(_firmar(), secret="wrong-secret") is False


def test_signer_detects_wrong_sello_tiempo() -> None:
    assert _verificar(_firmar(), sello="2025-06-01T00:00:00+00:00") is False


# --- AREA-04-11: clave por entidad y mensaje que ata entidad+objeto ----------


def test_firma_nativa_no_verifica_en_otro_tenant() -> None:
    """Una fila copiada a otro schema (restauración, import) deja de verificar.

    Mismo blob, mismo firmante, mismo sello, mismo objeto: solo cambia la entidad.
    """
    firma = _firmar(tenant_slug="a")
    assert _verificar(firma, tenant_slug="a") is True
    assert _verificar(firma, tenant_slug="b") is False


def test_firma_nativa_no_verifica_sobre_otro_objeto() -> None:
    firma = _firmar()
    assert _verificar(firma, objeto_id="exp-2") is False
    assert _verificar(firma, objeto_tipo="radicado") is False


def test_firma_nativa_nueva_sale_en_key_version_actual() -> None:
    from app.core.secret_derivation import KEY_VERSION_HKDF

    assert _firmar()["key_version"] == KEY_VERSION_HKDF == 1


def test_firma_nativa_key_version_0_ya_no_verifica() -> None:
    """B9-5: el esquema anterior a la 020 se retiró. El HMAC se calcula a mano, con
    la fórmula literal de entonces, para no probar el código con sí mismo: aun
    siendo el correcto de aquel esquema, ``key_version=0`` ya no verifica.
    Mutación: restaurar la rama v0 en ``signer._mac`` => ``True`` y cae."""
    import hashlib
    import hmac

    payload = b"<indice/>"
    sha = hashlib.sha256(payload).hexdigest()
    blob = hmac.new(
        settings.signature_secret.encode(),
        f"{sha}|{USER}|{SELLO_STR}".encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()
    for kv in (0, 1):
        assert signer.verificar(
            payload, str(USER), blob, SELLO_STR, settings.signature_secret,
            key_version=kv, **OBJ,
        ) is False


def test_key_version_desconocida_no_verifica() -> None:
    firma = _firmar()
    assert _verificar(firma, key_version=7) is False


def test_clave_derivada_distinta_por_proposito_y_entidad() -> None:
    from app.core.secret_derivation import derive_key

    r = settings.signature_secret
    assert derive_key(r, purpose="firma_nativa", tenant_slug="a") != derive_key(
        r, purpose="otp", tenant_slug="a"
    )
    assert derive_key(r, purpose="firma_nativa", tenant_slug="a") != derive_key(
        r, purpose="firma_nativa", tenant_slug="b"
    )
    assert derive_key(r, purpose="otp", tenant_slug="a") == derive_key(
        r, purpose="otp", tenant_slug="a"
    )


@pytest.mark.parametrize("slug", ["", None])
def test_derivar_clave_sin_entidad_se_rechaza(slug) -> None:
    from app.core.secret_derivation import derive_key

    with pytest.raises(ValueError):
        derive_key(settings.signature_secret, purpose="otp", tenant_slug=slug or "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _firma_row(payload: bytes, firmante: uuid.UUID) -> dict:
    """Build a fake DB row matching FirmaRepository._COLS."""
    r = signer.firmar(payload, str(firmante), SELLO_STR, settings.signature_secret, **OBJ)
    return {
        "id": uuid.uuid4(),
        "objeto_tipo": "indice",
        "objeto_id": "exp-1",
        "key_version": r["key_version"],
        "formato_solicitado": "XAdES",
        "firmante_id": firmante,
        "payload_sha256": r["payload_sha256"],
        "firma_blob": r["firma_blob"],
        "provider": r["provider"],
        "sello_tiempo": SELLO_DT,
        "created_at": NOW,
    }


# ---------------------------------------------------------------------------
# HTTP tests — sign-xml
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_xml_happy_path(client_with_conn, monkeypatch) -> None:
    """POST /sign-xml con token interno y PERM_FIRMA crea la fila ``firma``.

    Desde B9-3 solo admite el sello institucional (``indice``, actas) y exige
    ``X-Internal-Token``. Con ``signer_provider`` distinto de ``xades_local`` el
    sello cae al HMAC 'nativa', y es lo que se ejercita aquí de punta a punta por
    el router (la ruta XAdES real vive en tests/test_sign_xml_xades.py).
    """
    monkeypatch.setattr(settings, "signer_provider", "nativa")
    monkeypatch.setattr(settings, "signature_internal_token", "tok-interno")
    client, conn = client_with_conn
    conn.fetchrow.return_value = _firma_row(b"<indice/>", USER)

    resp = await client.post(
        "/api/v1/signature/sign-xml",
        json={"payload_xml": "<indice/>", "objeto_tipo": "indice", "objeto_id": "exp-1"},
        headers={**TENANT_HEADERS, "X-Internal-Token": "tok-interno"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["provider"] == "nativa"
    assert body["objeto_tipo"] == "indice"
    assert len(body["payload_sha256"]) == 64
    # firmante_id must come from X-User-Id header, not from request body
    assert body["firmante_id"] == str(USER)


@pytest.mark.asyncio
async def test_sign_xml_body_firmante_id_ignored() -> None:
    """firmante_id in the request body must not exist in the schema (RF-FIR-11)."""
    from app.schemas.firma import SignXmlRequest

    # Pydantic should reject or ignore a firmante_id field — it's not in the schema.
    req = SignXmlRequest(payload_xml="<x/>", objeto_tipo="indice", firmante_id=str(uuid.uuid4()))  # type: ignore[call-arg]
    # Pydantic v2 with extra="ignore" silently drops unknown fields.
    assert not hasattr(req, "firmante_id")


@pytest.mark.asyncio
async def test_sign_xml_requires_auth() -> None:
    """POST /sign-xml without X-User-Id header returns 401 (require_permission gate, RF-FIR-11)."""
    conn = AsyncMock()
    conn.fetchval.return_value = True
    conn.transaction = MagicMock(return_value=AsyncMock())

    async def _conn_dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _conn_dep
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/sign-xml",
                json={"payload_xml": "<x/>", "objeto_tipo": "indice"},
                headers={"X-Tenant-Slug": "demo"},  # X-User-Id intentionally omitted
            )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_sign_xml_requires_perm_firma() -> None:
    """Valid X-User-Id but PERM_FIRMA denied in DB → 403 forbidden (RF-FIR-11 / ADR-013).

    check_permission returns False (fetchval=None → user not found / no perm).
    require_permission raises 403. The router must propagate it.
    """
    conn = AsyncMock()
    conn.fetchval.return_value = None  # check_permission → denied
    conn.transaction = MagicMock(return_value=AsyncMock())

    async def _conn_dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _conn_dep
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/sign-xml",
                json={"payload_xml": "<x/>", "objeto_tipo": "indice"},
                headers=TENANT_HEADERS,  # includes X-User-Id
            )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "forbidden"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# HTTP tests — verify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_ok(client_with_conn) -> None:
    """POST /verify returns valida=true for untampered payload."""
    client, conn = client_with_conn
    row = _firma_row(b"<indice/>", USER)
    conn.fetchrow.return_value = row

    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(row["id"]), "payload_xml": "<indice/>"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["valida"] is True


@pytest.mark.asyncio
async def test_verify_tampered_payload(client_with_conn) -> None:
    """POST /verify returns valida=false when payload differs from signed content."""
    client, conn = client_with_conn
    row = _firma_row(b"<indice/>", USER)
    conn.fetchrow.return_value = row

    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(row["id"]), "payload_xml": "<indice ALTERADO/>"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["valida"] is False


@pytest.mark.asyncio
async def test_verify_not_found(client_with_conn) -> None:
    """POST /verify returns 404 when firma_id does not exist."""
    client, conn = client_with_conn
    conn.fetchrow.return_value = None

    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(uuid.uuid4()), "payload_xml": "<x/>"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 404
