# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El sello institucional NO es alcanzable desde fuera (AREA-04-07, ADR-047 §1).

El sello de la ENTIDAD (índice y actas) ya no tiene ruta HTTP: solo lo pide archive
en proceso, por ``signature.api``. Lo que no está montado no se alcanza con el JWT de
nadie. Lo que queda es el gate de quien lo pide: ``PERM_FIRMA`` de escritura en la
BD, y solo el allow-set institucional (los tipos de cadena, B9-3, no se sellan).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from signature import api as signature_api
from signature.core.constants import CADENA_OBJETO_TIPOS, SYSTEM_RECONCILER_ID
from tests.signature.app import app
from tests.signature.conftest import TENANT_HEADERS, USER, en_proceso
from tests.signature.test_sign_xml_xades import INDICE_XML, _generate_cert, _signer_ref
from tests.signature.test_sign_xml_xades import signature_env as signature_env  # noqa: F401  (fixture)


def _inserts(conn: AsyncMock) -> list:
    return [
        c
        for c in [*conn.fetchrow.call_args_list, *conn.execute.call_args_list]
        if c.args and "INSERT" in str(c.args[0])
    ]


async def _sellar(**kw):
    datos = {
        "tenant_slug": "demo",
        "actor_id": str(USER),
        "payload_xml": "<x/>",
        "objeto_id": "exp-1",
        "indice_id": None,
        "version": 1,
        "objeto_tipo": "indice",
    }
    return await signature_api.sign_xml_outcome(**{**datos, **kw})


def test_ninguna_ruta_estampa_el_sello() -> None:
    """Ni ``/sign-xml`` ni las rutas ``/internal/`` del dominio de firma están montadas.

    Mutación: volver a montar cualquiera de las dos => cae."""
    rutas = {getattr(r, "path", "") for r in app.routes}
    assert not any("sign-xml" in r or "sign-indice" in r for r in rutas), rutas
    assert not any(r.startswith("/api/v1/signature/internal/") for r in rutas), rutas


@pytest.mark.asyncio
async def test_post_a_sign_xml_no_encuentra_nada(client_with_conn) -> None:
    client, conn = client_with_conn
    resp = await client.post(
        "/api/v1/signature/sign-xml",
        json={"payload_xml": "<x/>", "objeto_tipo": "indice", "objeto_id": "o"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code in (404, 405)
    assert _inserts(conn) == []


@pytest.mark.asyncio
async def test_sin_perm_firma_de_escritura_no_se_sella_y_se_asienta(
    client_with_conn, monkeypatch
) -> None:
    """``PERM_FIRMA`` a nivel 2: con nivel 1 un usuario sellaba a nombre de la entidad.
    Denegado es ``transient`` (el objeto queda ``pendiente_firma``) y deja asiento."""
    _client, conn = client_with_conn
    conn.fetchval.return_value = None  # check_permission -> denegado
    en_proceso(monkeypatch, conn)
    sign = AsyncMock()
    monkeypatch.setattr("signature.services.signing_service.sign_payload", sign)

    outcome = await _sellar()

    assert outcome.signed is None and outcome.transient and not outcome.seal_absent
    sign.assert_not_awaited()
    rbac = [c for c in conn.fetchval.call_args_list if "auth_group_permissions" in c.args[0]]
    assert rbac and rbac[0].args[-1] == 2
    asientos = [c for c in conn.execute.call_args_list if "INSERT INTO public.audit_log" in str(c.args[0])]
    assert [a.args[5] for a in asientos] == ["acceso.denegado"]
    assert asientos[0].args[3] == str(USER)


@pytest.mark.asyncio
@pytest.mark.parametrize("tipo", sorted(CADENA_OBJETO_TIPOS))
async def test_los_tipos_de_cadena_no_se_sellan(client_with_conn, monkeypatch, tipo) -> None:
    """``radicado|expediente|borrador|documento`` darían una firma VERIFICABLE sin cadena,
    turno, 2FA ni gate XAdES. No se firma, ni se inserta, ni se asienta nada.

    Mutación: restaurar ``CADENA_OBJETO_TIPOS`` en el validador de ``SignXmlRequest`` => cae."""
    _client, conn = client_with_conn
    en_proceso(monkeypatch, conn)
    sign = AsyncMock()
    monkeypatch.setattr("signature.services.signing_service.sign_payload", sign)

    outcome = await _sellar(objeto_tipo=tipo)

    assert outcome.signed is None and outcome.transient
    sign.assert_not_awaited()
    assert _inserts(conn) == []


@pytest.mark.asyncio
async def test_con_permiso_se_sella_a_nombre_de_quien_lo_pide(client_with_conn, monkeypatch) -> None:
    _client, conn = client_with_conn
    en_proceso(monkeypatch, conn)
    sign = AsyncMock(return_value=MagicMock(acreditado=False))
    monkeypatch.setattr("signature.services.signing_service.sign_payload", sign)

    outcome = await _sellar(objeto_tipo="acta_transferencia")

    assert outcome.signed is not None
    kw = sign.await_args.kwargs
    assert kw["firmante_id"] == USER and kw["origen"] == "manual"
    assert kw["payload"].objeto_tipo == "acta_transferencia"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "seal_absent"),
    [("signing_seal_unavailable", True), ("timestamp_authority_unavailable", False)],
)
async def test_solo_el_sello_ausente_no_se_reintenta(
    client_with_conn, monkeypatch, error, seal_absent
) -> None:
    _client, conn = client_with_conn
    en_proceso(monkeypatch, conn)
    monkeypatch.setattr(
        "signature.services.signing_service.sign_payload",
        AsyncMock(side_effect=HTTPException(503, {"error": error, "detail": "x", "status": 503})),
    )
    outcome = await _sellar()
    assert outcome.signed is None
    assert outcome.seal_absent is seal_absent
    assert outcome.transient is not seal_absent


# -- Sello de SISTEMA (reconciliación) ------------------------------------------


@pytest.mark.asyncio
async def test_sello_de_sistema_es_el_mismo_material_atribuido_a_sistema(
    signature_env, monkeypatch  # noqa: F811
) -> None:
    """Mismo ``signing_service.sign_payload`` que el sello manual: el XML verifica
    ``valida=True``. La única diferencia es la atribución: ``SYSTEM_RECONCILER_ID`` y
    ``origen='reconciliation'`` en el asiento ``firma.creada``."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    en_proceso(monkeypatch, conn)
    cert_pem, key_pem = _generate_cert("orpycamcp-dev-reconciler")
    install_seal(_signer_ref(cert_pem, key_pem))

    outcome = await signature_api.sign_as_system(
        tenant_slug="demo",
        payload_xml=INDICE_XML.decode(),
        objeto_id="exp-1",
        indice_id="00000000-0000-0000-0000-0000000000aa",
        version=1,
    )
    assert outcome.signed is not None
    firma_id = str(outcome.signed.firma_id)
    fila = table.rows[firma_id]
    assert fila["provider"] == "xades_local" and fila["tipo_firma"] == "sello_sistema"
    assert str(fila["firmante_id"]) == str(SYSTEM_RECONCILER_ID)

    asientos = [c for c in conn.execute.call_args_list if "INSERT INTO public.audit_log" in str(c.args[0])]
    assert len(asientos) == 1
    assert asientos[0].args[3] == str(SYSTEM_RECONCILER_ID)
    assert asientos[0].args[5] == "firma.creada"
    assert json.loads(asientos[0].args[8])["origen"] == "reconciliation"

    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": uploads[0]["content"].decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.status_code == 200
    assert verify_resp.json()["valida"] is True
