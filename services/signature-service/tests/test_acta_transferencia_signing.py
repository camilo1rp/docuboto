# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Firma XAdES del acta de transferencia (H-H, E06/F4) — generalización del
allow-set institucional de `signing_service._select_provider_name` a
`objeto_tipo="acta_transferencia"`, en `/sign-xml` (atribución humana) y
`/internal/sign-indice` (atribución a `SYSTEM_RECONCILER_ID`, reconciliación).

Reutiliza `signature_env` (fixture + helpers de `test_sign_xml_xades.py`) —
mismo sello institucional efímero, mismo `_FakeFirmaTable`. La única
diferencia de comportamiento frente al índice: el filename MinIO usa el
prefijo `acta-transferencia-` y NUNCA se publica `firma.indice.firmado`
(evento sin consumidor para este tipo de objeto).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.core.constants import SYSTEM_RECONCILER_ID
from tests.conftest import TENANT_HEADERS, USER
from tests.test_sign_xml_xades import _generate_cert, _signer_ref
from tests.test_sign_xml_xades import signature_env as signature_env  # re-exported fixture

_ACTA_XML = (
    b'<acta xmlns="urn:orpyca:acta-fuid">'
    b'<expediente id="exp-1">Inventario congelado</expediente>'
    b"</acta>"
)

_INTERNAL_TOKEN = "itest-internal-token-acta-abc123"


async def _sign_acta(client, *, headers=None, xml: bytes = _ACTA_XML):
    return await client.post(
        "/api/v1/signature/sign-xml",
        json={
            "payload_xml": xml.decode(),
            "objeto_tipo": "acta_transferencia",
            "objeto_id": "transferencia-1",
            "indice_id": None,
            "version": 1,
        },
        headers=headers or TENANT_HEADERS,
    )


def _internal_acta_body(*, xml: bytes = _ACTA_XML, objeto_id: str = "transferencia-1") -> dict:
    return {
        "payload_xml": xml.decode(),
        "objeto_tipo": "acta_transferencia",
        "objeto_id": objeto_id,
        "version": 1,
    }


# ---------------------------------------------------------------------------
# /sign-xml — atribución humana (X-User-Id, PERM_FIRMA)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_xml_acta_transferencia_produces_xades(signature_env) -> None:
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await _sign_acta(client)
    assert resp.status_code == 201
    body = resp.json()

    assert body["provider"] == "xades_local"
    assert body["xades_level"] in ("XAdES-B", "XAdES-T")
    assert body["firmante_id"] == str(USER)

    signed_xml = uploads[0]["content"]
    assert b":Signature" in signed_xml

    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": body["firma_id"], "payload_xml": signed_xml.decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.status_code == 200
    assert verify_resp.json()["valida"] is True


@pytest.mark.asyncio
async def test_sign_xml_acta_transferencia_minio_filename_uses_acta_prefix(
    signature_env,
) -> None:
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await _sign_acta(client)
    assert resp.status_code == 201
    body = resp.json()

    # S1 (corrección de verify bajo carrera): el filename incluye el
    # `firma_id` ANTES de `-firmado.xml` — cada firma sube su propio objeto,
    # sin key compartida entre firmas concurrentes de la misma entidad.
    assert uploads[0]["filename"] == (
        f"acta-transferencia-transferencia-1-v1-{body['firma_id']}-firmado.xml"
    )


@pytest.mark.asyncio
async def test_sign_xml_acta_transferencia_two_signatures_never_share_minio_key(
    signature_env,
) -> None:
    """S1: dos sellados exitosos de la MISMA entidad (p. ej. intento síncrono
    post-commit vs. job de reconciliación bajo carrera) deben producir keys
    de MinIO DISTINTAS — cada firma sube su propio XML según su propio
    `firma_id`, así que la segunda nunca sobrescribe el objeto de la primera
    y `minio_key_firmado` persistido en cada fila sigue apuntando a SU XML."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp1 = await _sign_acta(client)
    resp2 = await _sign_acta(client)
    assert resp1.status_code == 201
    assert resp2.status_code == 201
    body1, body2 = resp1.json(), resp2.json()

    assert body1["firma_id"] != body2["firma_id"]
    assert len(uploads) == 2
    assert uploads[0]["filename"] != uploads[1]["filename"]
    assert body1["firma_id"] in uploads[0]["filename"]
    assert body2["firma_id"] in uploads[1]["filename"]
    # object_key deriva de filename (fake_upload) — sin colisión de key real.
    assert body1["minio_key_firmado"] != body2["minio_key_firmado"]


@pytest.mark.asyncio
async def test_sign_xml_acta_transferencia_never_publishes_indice_firmado_event(
    signature_env, monkeypatch
) -> None:
    """Gate por `objeto_tipo` (residual H-H): `firma.indice.firmado` es un
    evento con UN solo consumidor (E15) — nunca se fabrica para el acta."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    mock_publish = AsyncMock()
    monkeypatch.setattr("app.services.signing_service.publish_indice_firmado", mock_publish)

    resp = await _sign_acta(client)
    assert resp.status_code == 201
    mock_publish.assert_not_called()


@pytest.mark.asyncio
async def test_sign_xml_indice_still_publishes_event_retrocompat(
    signature_env, monkeypatch
) -> None:
    """Retrocompat: el índice SIGUE publicando el evento — el gate nuevo NO
    rompe el consumidor existente (E15)."""
    from tests.test_sign_xml_xades import _sign_indice

    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    mock_publish = AsyncMock()
    monkeypatch.setattr("app.services.signing_service.publish_indice_firmado", mock_publish)
    # `get_redis()` no está inicializado en el entorno de test (sin lifespan) —
    # se patchea también para que la llamada llegue a `publish_indice_firmado`
    # en vez de fallar antes en `get_redis()` (best-effort, capturado por el
    # mismo `try/except` que protege el evento).
    monkeypatch.setattr("app.services.signing_service.get_redis", lambda: object())

    resp = await _sign_indice(client)
    assert resp.status_code == 201
    mock_publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_sign_xml_other_objeto_tipo_falls_back_to_nativa_retrocompat(
    signature_env,
) -> None:
    """Cualquier `objeto_tipo` fuera del allow-set (radicado, anexo, ...)
    sigue cayendo a HMAC 'nativa' — comportamiento sin cambios."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    with (
        patch(
            "app.routers.signature.CadenaRepository.objeto_visible",
            new=AsyncMock(return_value=True),
        ),
        patch("app.routers.signature.resolve_caller_clearance", new=AsyncMock(return_value=1)),
    ):
        resp = await client.post(
            "/api/v1/signature/sign-xml",
            json={"payload_xml": "<radicado/>", "objeto_tipo": "radicado", "objeto_id": "R-1"},
            headers=TENANT_HEADERS,
        )
    assert resp.status_code == 201
    assert resp.json()["provider"] == "nativa"
    assert uploads == []


# ---------------------------------------------------------------------------
# /internal/sign-indice — atribución a SYSTEM_RECONCILER_ID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_internal_sign_acta_transferencia_produces_xades_attributed_to_system(
    signature_env, monkeypatch
) -> None:
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signature_internal_token", _INTERNAL_TOKEN)
    cert_pem, key_pem = _generate_cert("orpycamcp-dev-reconciler")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await client.post(
        "/api/v1/signature/internal/sign-indice",
        json=_internal_acta_body(),
        headers={"X-Tenant-Slug": "demo", "X-Internal-Token": _INTERNAL_TOKEN},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["provider"] == "xades_local"
    assert body["firmante_id"] == str(SYSTEM_RECONCILER_ID)
    assert uploads[0]["filename"].startswith("acta-transferencia-")


@pytest.mark.asyncio
async def test_internal_sign_indice_default_objeto_tipo_still_indice_retrocompat(
    signature_env, monkeypatch
) -> None:
    """Retrocompat del Inc.8: el llamador preexistente
    (`archive-service/app/jobs/indice_reconciliation.py`) nunca envía
    `objeto_tipo` — el default sigue siendo `"indice"`, filename con el
    prefijo `indice-` de siempre."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signature_internal_token", _INTERNAL_TOKEN)
    cert_pem, key_pem = _generate_cert("orpycamcp-dev-reconciler")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await client.post(
        "/api/v1/signature/internal/sign-indice",
        json={
            "payload_xml": _ACTA_XML.decode(),
            "objeto_id": "exp-1",
            "indice_id": str(uuid.uuid4()),
            "version": 1,
        },
        headers={"X-Tenant-Slug": "demo", "X-Internal-Token": _INTERNAL_TOKEN},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert uploads[0]["filename"] == f"indice-exp-1-v1-{body['firma_id']}-firmado.xml"


@pytest.mark.asyncio
async def test_internal_sign_rejects_objeto_tipo_outside_allow_set(
    client_with_conn, monkeypatch
) -> None:
    """`objeto_tipo` fuera de `XADES_ELIGIBLE_OBJETO_TIPOS` (p. ej. "radicado")
    es rechazado por el schema (422) — la ruta interna NUNCA puede firmar un
    tipo de objeto arbitrario, aunque el body lo declare."""
    monkeypatch.setattr(settings, "signature_internal_token", _INTERNAL_TOKEN)
    client, _conn = client_with_conn
    resp = await client.post(
        "/api/v1/signature/internal/sign-indice",
        json={
            "payload_xml": "<x/>",
            "objeto_tipo": "radicado",
            "objeto_id": "exp-1",
            "version": 1,
        },
        headers={"X-Tenant-Slug": "demo", "X-Internal-Token": _INTERNAL_TOKEN},
    )
    assert resp.status_code == 422
