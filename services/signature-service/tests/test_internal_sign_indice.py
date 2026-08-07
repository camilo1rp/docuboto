# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""``POST /internal/sign-indice`` (residual Ac. AGN 001/2024 art. 4.3.2.4,
reconciliación del sellado del índice) y el gate `require_internal_token`.

Reutiliza `signature_env` (fixture + helpers de `test_sign_xml_xades.py`,
`_generate_cert`/`_signer_ref`) para instalar un sello institucional efímero
y verificar que el sellado producido por la ruta interna es MATERIALMENTE
idéntico (misma XAdES-B/-T real, misma verificación) al de `/sign-xml` —
solo cambia la ATRIBUCIÓN (`SYSTEM_RECONCILER_ID` vs. `X-User-Id`) y el gate
de autorización (`X-Internal-Token` vs. `PERM_FIRMA`/`X-User-Id`).
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.constants import SYSTEM_RECONCILER_ID
from app.core.internal_auth import require_internal_token
from tests.conftest import TENANT_HEADERS
from tests.test_sign_xml_xades import (
    INDICE_XML,
    _generate_cert,
    _signer_ref,
)
from tests.test_sign_xml_xades import (
    signature_env as signature_env,  # re-exported fixture
)

_INTERNAL_TOKEN = "itest-internal-token-abc123"


def _internal_body(*, xml: bytes = INDICE_XML, objeto_id: str = "exp-1") -> dict:
    return {
        "payload_xml": xml.decode(),
        "objeto_id": objeto_id,
        "indice_id": str(uuid.uuid4()),
        "version": 1,
    }


# ---------------------------------------------------------------------------
# require_internal_token — gate unitario (sin HTTP)
# ---------------------------------------------------------------------------


def test_require_internal_token_fails_closed_when_unconfigured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "signature_internal_token", "")
    with pytest.raises(HTTPException) as exc_info:
        require_internal_token(x_internal_token="anything")
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "internal_signing_not_configured"


def test_require_internal_token_rejects_missing_header(monkeypatch) -> None:
    monkeypatch.setattr(settings, "signature_internal_token", _INTERNAL_TOKEN)
    with pytest.raises(HTTPException) as exc_info:
        require_internal_token(x_internal_token=None)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"] == "missing_internal_token"


def test_require_internal_token_rejects_wrong_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, "signature_internal_token", _INTERNAL_TOKEN)
    with pytest.raises(HTTPException) as exc_info:
        require_internal_token(x_internal_token="not-the-right-token")
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"] == "invalid_internal_token"


def test_require_internal_token_accepts_matching_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, "signature_internal_token", _INTERNAL_TOKEN)
    # No exception == pass.
    require_internal_token(x_internal_token=_INTERNAL_TOKEN)


def test_require_internal_token_rejects_wrong_token_of_different_length(monkeypatch) -> None:
    """Remediación de auditoría (Baja): la comparación pasó de `!=` a
    `hmac.compare_digest` (side-channel de temporización) -- se prueba
    explícitamente con tokens de LONGITUD DISTINTA (más corto y más largo que
    el configurado), que `compare_digest` maneja sin excepción (a diferencia
    de un XOR byte-a-byte ingenuo) y que los tests existentes de arriba
    (mismo tamaño) no ejercitan."""
    monkeypatch.setattr(settings, "signature_internal_token", _INTERNAL_TOKEN)

    with pytest.raises(HTTPException) as exc_info:
        require_internal_token(x_internal_token="short")
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"] == "invalid_internal_token"

    with pytest.raises(HTTPException) as exc_info:
        require_internal_token(x_internal_token=_INTERNAL_TOKEN + "-extra-suffix")
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"] == "invalid_internal_token"


# ---------------------------------------------------------------------------
# POST /internal/sign-indice — nivel HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_internal_sign_indice_rejects_without_token(client_with_conn, monkeypatch) -> None:
    monkeypatch.setattr(settings, "signature_internal_token", _INTERNAL_TOKEN)
    client, _conn = client_with_conn
    resp = await client.post(
        "/api/v1/signature/internal/sign-indice",
        json=_internal_body(),
        headers={"X-Tenant-Slug": "demo"},  # sin X-Internal-Token
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "missing_internal_token"


@pytest.mark.asyncio
async def test_internal_sign_indice_rejects_wrong_token(client_with_conn, monkeypatch) -> None:
    monkeypatch.setattr(settings, "signature_internal_token", _INTERNAL_TOKEN)
    client, _conn = client_with_conn
    resp = await client.post(
        "/api/v1/signature/internal/sign-indice",
        json=_internal_body(),
        headers={"X-Tenant-Slug": "demo", "X-Internal-Token": "wrong"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "invalid_internal_token"


@pytest.mark.asyncio
async def test_internal_sign_indice_503_when_unconfigured(client_with_conn, monkeypatch) -> None:
    """`settings.signature_internal_token` vacío (el default del modelo —
    monkeypatch explícito porque el `docker-compose.yml` de DESARROLLO fija
    un valor no vacío vía `SIGNATURE_INTERNAL_TOKEN`, así que este contenedor
    de tests NO parte de "" por defecto): sin ella, este despliegue NO tiene
    la reconciliación configurada — 503 fail-closed, no un simple 401
    (distingue "sin configurar" de "sin token")."""
    monkeypatch.setattr(settings, "signature_internal_token", "")
    client, _conn = client_with_conn
    resp = await client.post(
        "/api/v1/signature/internal/sign-indice",
        json=_internal_body(),
        headers={"X-Tenant-Slug": "demo", "X-Internal-Token": "whatever"},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "internal_signing_not_configured"


@pytest.mark.asyncio
async def test_internal_sign_indice_produces_material_identical_to_sign_xml(
    signature_env, monkeypatch
) -> None:
    """Con token válido, sella el índice con el MISMO sello institucional que
    `/sign-xml` (mismo `signing_service.sign_payload`, sin duplicar lógica):
    `provider`/`xades_level`/`certificado_fingerprint` idénticos, y el XML
    firmado resultante verifica `valida=True` — prueba MATERIAL, no solo de
    forma del payload. La ÚNICA diferencia es la atribución: `firmante_id`
    es `SYSTEM_RECONCILER_ID`, nunca un `X-User-Id` humano."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signature_internal_token", _INTERNAL_TOKEN)
    cert_pem, key_pem = _generate_cert("orpycamcp-dev-reconciler")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await client.post(
        "/api/v1/signature/internal/sign-indice",
        json=_internal_body(),
        headers={"X-Tenant-Slug": "demo", "X-Internal-Token": _INTERNAL_TOKEN},
    )
    assert resp.status_code == 201
    body = resp.json()

    assert body["provider"] == "xades_local"
    assert body["xades_level"] == "XAdES-T"
    assert body["tipo_firma"] == "sello_sistema"
    assert body["nivel_conformidad"] == "xades_b_no_acreditado"
    assert body["acreditado"] is False
    # Atribución a SISTEMA — NUNCA el X-User-Id de un humano (no se envió
    # ninguno en esta petición, y aunque se enviara, esta ruta lo ignora).
    assert body["firmante_id"] == str(SYSTEM_RECONCILER_ID)

    firma_id = body["firma_id"]
    signed_xml = uploads[0]["content"]

    # Prueba MATERIAL: el XML producido por la ruta interna verifica válido
    # exactamente igual que uno producido por /sign-xml (mismo proveedor,
    # mismo sello institucional, misma canonicalización).
    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": signed_xml.decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.status_code == 200
    vbody = verify_resp.json()
    assert vbody["valida"] is True
    assert vbody["provider"] == "xades_local"
    assert vbody["xades_level"] == "XAdES-T"


@pytest.mark.asyncio
async def test_internal_sign_indice_audit_marks_origen_reconciliation(
    signature_env, monkeypatch
) -> None:
    """El asiento `firma.creada` distingue el sellado por reconciliación
    (`origen='reconciliation'`, actor=SYSTEM_RECONCILER_ID) del manual
    (`/sign-xml`, `origen='manual'`, actor=X-User-Id real)."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signature_internal_token", _INTERNAL_TOKEN)
    cert_pem, key_pem = _generate_cert("orpycamcp-dev-reconciler")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await client.post(
        "/api/v1/signature/internal/sign-indice",
        json=_internal_body(),
        headers={"X-Tenant-Slug": "demo", "X-Internal-Token": _INTERNAL_TOKEN},
    )
    assert resp.status_code == 201

    audit_calls = [
        c for c in conn.execute.call_args_list if "INSERT INTO public.audit_log" in str(c.args[0])
    ]
    assert len(audit_calls) == 1
    call_args = audit_calls[0].args
    # _INSERT_SQL, tenant, service, actor, canal, action, object_type, object_ref, payload_json, prev_hash, hash
    assert call_args[3] == str(SYSTEM_RECONCILER_ID)  # actor
    assert call_args[5] == "firma.creada"  # action
    payload = json.loads(call_args[8])
    assert payload["origen"] == "reconciliation"


# ---------------------------------------------------------------------------
# Las rutas de firma PERSONAL / /sign-xml NUNCA aceptan el token interno.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_xml_ignores_internal_token_still_requires_user_id(
    client_with_conn, monkeypatch
) -> None:
    """Un `X-Internal-Token` válido NO sustituye a `X-User-Id`/PERM_FIRMA en
    `/sign-xml`: esa ruta ni siquiera lee esa cabecera — sigue exigiendo
    autenticación humana normal."""
    monkeypatch.setattr(settings, "signature_internal_token", _INTERNAL_TOKEN)
    client, _conn = client_with_conn
    resp = await client.post(
        "/api/v1/signature/sign-xml",
        json={"payload_xml": "<indice/>", "objeto_tipo": "indice"},
        headers={"X-Tenant-Slug": "demo", "X-Internal-Token": _INTERNAL_TOKEN},  # sin X-User-Id
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_personal_chain_sign_ignores_internal_token_still_requires_user_id(
    monkeypatch,
) -> None:
    """La cadena de firma personal (`POST /requests/{id}/sign`) NUNCA acepta
    el token interno: esa ruta no declara `require_internal_token` como
    dependencia — un `X-Internal-Token` presente no tiene ningún efecto,
    sigue exigiendo `X-User-Id` (`get_actor`, fail-closed 401)."""
    monkeypatch.setattr(settings, "signature_internal_token", _INTERNAL_TOKEN)
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/signature/requests/{uuid.uuid4()}/sign",
            json={},
            headers={"X-Tenant-Slug": "demo", "X-Internal-Token": _INTERNAL_TOKEN},  # sin X-User-Id
        )
    assert resp.status_code == 401
