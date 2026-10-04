# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""
AREA-05-02 — ``acreditado`` en la verificación sale de lo PERSISTIDO al firmar.

Antes ``/verify`` y ``GET /firmas/{id}`` devolvían un literal ``False``: una firma
hecha con un sello ACREDITADO se reportaba como no acreditada justo donde el
comprobante tiene que poder decirlo. La acreditación es un hecho del momento de
firmar (``firma.nivel_conformidad``); la verificación solo la confirma si la
criptografía valida.
"""

from __future__ import annotations

import pytest

from app.providers.base import SignerRef
from tests.conftest import TENANT_HEADERS
from tests.test_sign_xml_xades import (  # noqa: F401 — fixtures y helpers compartidos
    _generate_cert,
    _sign_indice,
    signature_env,
)


def _sello(cert_pem: bytes, key_pem: bytes, *, acreditado: bool) -> SignerRef:
    return SignerRef(
        cert_pem=cert_pem,
        key_pem=key_pem,
        key_password=None,
        tipo_firma="sello_sistema",
        nivel_conformidad="xades_b_acreditado" if acreditado else "xades_b_no_acreditado",
        acreditado=acreditado,
    )


async def _verify(client, firma_id: str, xml: str):
    return await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": xml},
        headers=TENANT_HEADERS,
    )


@pytest.mark.asyncio
async def test_verify_reporta_acreditado_desde_lo_persistido(signature_env) -> None:  # noqa: F811
    client, conn, table, uploads, install_seal, _ = signature_env
    install_seal(_sello(*_generate_cert("sello-acreditado"), acreditado=True))

    resp = await _sign_indice(client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["nivel_conformidad"] == "xades_b_acreditado"
    assert body["acreditado"] is True

    v = await _verify(client, body["firma_id"], uploads[0]["content"].decode())
    assert v.status_code == 200, v.text
    assert v.json()["valida"] is True
    assert v.json()["nivel_conformidad"] == "xades_b_acreditado"
    assert v.json()["acreditado"] is True

    g = await client.get(f"/api/v1/signature/firmas/{body['firma_id']}", headers=TENANT_HEADERS)
    assert g.status_code == 200, g.text
    assert g.json()["acreditado"] is True


@pytest.mark.asyncio
async def test_verify_sello_no_acreditado_sigue_en_false(signature_env) -> None:  # noqa: F811
    """Control negativo: sello sin acreditación -> False, aunque valide."""
    client, conn, table, uploads, install_seal, _ = signature_env
    install_seal(_sello(*_generate_cert("sello-dev"), acreditado=False))
    body = (await _sign_indice(client)).json()
    v = await _verify(client, body["firma_id"], uploads[0]["content"].decode())
    assert v.json()["valida"] is True
    assert v.json()["acreditado"] is False
    g = await client.get(f"/api/v1/signature/firmas/{body['firma_id']}", headers=TENANT_HEADERS)
    assert g.json()["acreditado"] is False


@pytest.mark.asyncio
async def test_firma_alterada_no_es_acreditada_aunque_la_fila_lo_diga(
    signature_env,  # noqa: F811
) -> None:
    """La fila dice acreditado, pero la criptografía no valida: ``acreditado`` es
    False. Una firma que no valida no acredita nada."""
    client, conn, table, uploads, install_seal, _ = signature_env
    install_seal(_sello(*_generate_cert("sello-acreditado-2"), acreditado=True))
    body = (await _sign_indice(client)).json()
    firmado = uploads[0]["content"].decode()
    # Fila anterior a la migración 010 (sin `signed_xml_sha256`): sin ese atajo,
    # el XML alterado lo corta el cotejo de huella ANTES de la criptografía y el
    # test no llegaría a probar la derivación desde la verificación real.
    table.rows[body["firma_id"]]["signed_xml_sha256"] = None
    # Cambio de contenido: un elemento nuevo dentro del documento firmado.
    alterado = firmado.replace("</indice>", "<extra/></indice>")
    assert alterado != firmado

    v = await _verify(client, body["firma_id"], alterado)
    assert v.status_code == 200
    assert v.json()["valida"] is False
    assert v.json()["motivo"].startswith("firma_invalida")  # la criptografía, no el atajo
    assert v.json()["nivel_conformidad"] == "xades_b_acreditado"  # lo persistido sigue ahí
    assert v.json()["acreditado"] is False


@pytest.mark.asyncio
async def test_verify_pasa_el_certificado_firmante_al_sello_de_tiempo(
    signature_env, monkeypatch  # noqa: F811
) -> None:
    """AREA-04-08: ``verify_xades`` entrega el certificado FIRMANTE a la
    verificación del SignatureTimeStamp (el cruce genTime/vigencia)."""
    from app.core import tsa as tsa_module

    client, conn, table, uploads, install_seal, _ = signature_env
    install_seal(_sello(*_generate_cert("sello-t"), acreditado=False))
    body = (await _sign_indice(client)).json()

    vistos: list[dict] = []
    real = tsa_module.verify_timestamp_token

    def _espia(*a, **kw):
        vistos.append(kw)
        return real(*a, **kw)

    monkeypatch.setattr(tsa_module, "verify_timestamp_token", _espia)
    v = await _verify(client, body["firma_id"], uploads[0]["content"].decode())
    assert v.json()["valida"] is True
    assert v.json()["sello_tiempo_valido"] is True
    assert len(vistos) == 1 and vistos[0]["signer_cert"] is not None
