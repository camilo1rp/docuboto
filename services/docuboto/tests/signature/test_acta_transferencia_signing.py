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
`objeto_tipo="acta_transferencia"`, con atribución humana y atribuido a
`SYSTEM_RECONCILER_ID` (reconciliación, `signature.api.sign_as_system`).

Reutiliza `signature_env` (fixture + helpers de `test_sign_xml_xades.py`) —
mismo sello institucional efímero, mismo `_FakeFirmaTable`. La única
diferencia de comportamiento frente al índice: el filename MinIO usa el
prefijo `acta-transferencia-` y NUNCA se publica `firma.indice.firmado`
(evento sin consumidor para este tipo de objeto).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from signature import api as signature_api
from signature.core.constants import SYSTEM_RECONCILER_ID
from tests.signature.conftest import TENANT_HEADERS, USER, en_proceso, sellar
from tests.signature.test_sign_xml_xades import _generate_cert, _signer_ref
from tests.signature.test_sign_xml_xades import (
    signature_env as signature_env,  # re-exported fixture
)

_ACTA_XML = (
    b'<acta xmlns="urn:orpyca:acta-fuid">'
    b'<expediente id="exp-1">Inventario congelado</expediente>'
    b"</acta>"
)

async def _sign_acta(conn, *, xml: bytes = _ACTA_XML):
    return await sellar(
        conn,
        {
            "payload_xml": xml.decode(),
            "objeto_tipo": "acta_transferencia",
            "objeto_id": "transferencia-1",
            "indice_id": None,
            "version": 1,
        },
    )


def _internal_acta_body(*, xml: bytes = _ACTA_XML, objeto_id: str = "transferencia-1") -> dict:
    return {
        "payload_xml": xml.decode(),
        "objeto_tipo": "acta_transferencia",
        "objeto_id": objeto_id,
        "indice_id": None,
        "version": 1,
    }


# ---------------------------------------------------------------------------
# Sello atribuido a quien lo pide
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_xml_acta_transferencia_produces_xades(signature_env) -> None:
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await _sign_acta(conn)
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

    resp = await _sign_acta(conn)
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

    resp1 = await _sign_acta(conn)
    resp2 = await _sign_acta(conn)
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
    monkeypatch.setattr("signature.services.signing_service.publish_indice_firmado", mock_publish)

    resp = await _sign_acta(conn)
    assert resp.status_code == 201
    mock_publish.assert_not_called()


@pytest.mark.asyncio
async def test_sign_xml_indice_still_publishes_event_retrocompat(
    signature_env, monkeypatch
) -> None:
    """Retrocompat: el índice SIGUE publicando el evento — el gate nuevo NO
    rompe el consumidor existente (E15)."""
    from tests.signature.test_sign_xml_xades import _sign_indice

    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    mock_publish = AsyncMock()
    monkeypatch.setattr("signature.services.signing_service.publish_indice_firmado", mock_publish)

    resp = await _sign_indice(conn)
    assert resp.status_code == 201
    mock_publish.assert_awaited_once()
    # ADR-049: se publica con la conexión del acto, dentro de su transacción.
    assert mock_publish.await_args.args[0] is conn


@pytest.mark.asyncio
async def test_sign_xml_ya_no_admite_tipos_fuera_del_sello(signature_env) -> None:
    """B9-3: el sello es solo institucional. Un ``objeto_tipo`` fuera del allow-set
    (radicado, anexo, ...) da 422, y no se firma ni se sube nada (antes caía a HMAC
    'nativa')."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await sellar(
        conn, {"payload_xml": "<radicado/>", "objeto_tipo": "radicado", "objeto_id": "R-1"}
    )
    assert resp.status_code == 422
    assert uploads == []
    assert table.rows == {}


# ---------------------------------------------------------------------------
# signature.api.sign_as_system — atribución a SYSTEM_RECONCILER_ID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_internal_sign_acta_transferencia_produces_xades_attributed_to_system(
    signature_env, monkeypatch
) -> None:
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    en_proceso(monkeypatch, conn)
    cert_pem, key_pem = _generate_cert("orpycamcp-dev-reconciler")
    install_seal(_signer_ref(cert_pem, key_pem))

    outcome = await signature_api.sign_as_system(tenant_slug="demo", **_internal_acta_body())
    assert outcome.signed is not None
    fila = table.rows[str(outcome.signed.firma_id)]
    assert fila["provider"] == "xades_local"
    assert str(fila["firmante_id"]) == str(SYSTEM_RECONCILER_ID)
    assert uploads[0]["filename"].startswith("acta-transferencia-")


@pytest.mark.asyncio
async def test_internal_sign_indice_default_objeto_tipo_still_indice(
    signature_env, monkeypatch
) -> None:
    """Sin `objeto_tipo`, el sello de sistema es del índice: filename con el prefijo `indice-`."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    en_proceso(monkeypatch, conn)
    cert_pem, key_pem = _generate_cert("orpycamcp-dev-reconciler")
    install_seal(_signer_ref(cert_pem, key_pem))

    outcome = await signature_api.sign_as_system(
        tenant_slug="demo",
        payload_xml=_ACTA_XML.decode(),
        objeto_id="exp-1",
        indice_id=str(uuid.uuid4()),
        version=1,
    )
    assert outcome.signed is not None
    assert uploads[0]["filename"] == f"indice-exp-1-v1-{outcome.signed.firma_id}-firmado.xml"


@pytest.mark.asyncio
async def test_internal_sign_rejects_objeto_tipo_outside_allow_set(
    client_with_conn, monkeypatch
) -> None:
    """`objeto_tipo` fuera de `XADES_ELIGIBLE_OBJETO_TIPOS` (p. ej. "radicado") no se sella:
    el sello de sistema NUNCA puede firmar un tipo de objeto arbitrario."""
    _client, conn = client_with_conn
    en_proceso(monkeypatch, conn)
    sign = AsyncMock()
    monkeypatch.setattr("signature.services.signing_service.sign_payload", sign)
    outcome = await signature_api.sign_as_system(
        tenant_slug="demo", payload_xml="<x/>", objeto_tipo="radicado", objeto_id="exp-1",
        indice_id=None, version=1,
    )
    assert outcome.signed is None and outcome.transient
    sign.assert_not_awaited()
