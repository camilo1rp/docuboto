# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""El sello institucional NO es alcanzable con el JWT de un humano (AREA-04-07).

`/sign-xml` con `objeto_tipo` del allow-set XAdES estampa el sello de la
ENTIDAD. Un humano con PERM_FIRMA nivel 1 podía llamarlo directo con un XML
arbitrario y saltarse los gates de archive-service. Ahora exige además
`X-Internal-Token`; fuera del allow-set (también los tipos de cadena, B9-3), 422.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.constants import CADENA_OBJETO_TIPOS, XADES_ELIGIBLE_OBJETO_TIPOS
from tests.conftest import TENANT_HEADERS

_TOKEN = "itest-internal-token-abc123"
# Derivado del allow-set REAL: un tipo institucional nuevo queda cubierto solo.
_INSTITUCIONALES = sorted(XADES_ELIGIBLE_OBJETO_TIPOS)


@pytest.mark.asyncio
@pytest.mark.parametrize("tipo", _INSTITUCIONALES)
async def test_sello_sin_token_interno_se_rechaza_y_no_toca_sello_ni_storage(
    client_with_conn, monkeypatch, tipo
) -> None:
    monkeypatch.setattr(settings, "signature_internal_token", _TOKEN)
    from unittest.mock import AsyncMock, patch

    client, _conn = client_with_conn
    with (
        patch("app.services.signing_service.load_seal", new=AsyncMock()) as seal,
        patch(
            "app.services.signing_service.storage_client.upload_signed_xml", new=AsyncMock()
        ) as upload,
    ):
        resp = await client.post(
            "/api/v1/signature/sign-xml",
            json={"payload_xml": "<x/>", "objeto_tipo": tipo, "objeto_id": "exp-1"},
            headers=TENANT_HEADERS,
        )
    assert resp.status_code in (401, 403)
    seal.assert_not_called()
    upload.assert_not_called()


@pytest.mark.asyncio
async def test_sello_con_token_equivocado_es_403(client_with_conn, monkeypatch) -> None:
    monkeypatch.setattr(settings, "signature_internal_token", _TOKEN)
    client, _conn = client_with_conn
    resp = await client.post(
        "/api/v1/signature/sign-xml",
        json={"payload_xml": "<x/>", "objeto_tipo": "indice", "objeto_id": "exp-1"},
        headers={**TENANT_HEADERS, "X-Internal-Token": "otro"},
    )
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_sello_con_token_interno_y_identidad_pasa_el_gate(
    client_with_conn, monkeypatch
) -> None:
    """Con el token, la petición ya no la corta el gate (llega a sign_payload)."""
    from unittest.mock import AsyncMock, patch

    monkeypatch.setattr(settings, "signature_internal_token", _TOKEN)
    client, _conn = client_with_conn
    with patch(
        "app.routers.signature.signing_service.sign_payload", new=AsyncMock()
    ) as sign:
        sign.side_effect = RuntimeError("llegó al servicio")
        with pytest.raises(RuntimeError, match="llegó al servicio"):
            await client.post(
                "/api/v1/signature/sign-xml",
                json={"payload_xml": "<x/>", "objeto_tipo": "indice", "objeto_id": "exp-1"},
                headers={**TENANT_HEADERS, "X-Internal-Token": _TOKEN},
            )
    sign.assert_awaited_once()


@pytest.mark.asyncio
async def test_objeto_tipo_libre_da_422(client_with_conn, monkeypatch) -> None:
    monkeypatch.setattr(settings, "signature_internal_token", _TOKEN)
    client, _conn = client_with_conn
    resp = await client.post(
        "/api/v1/signature/sign-xml",
        json={"payload_xml": "<x/>", "objeto_tipo": "borrador_libre", "objeto_id": "x"},
        headers={**TENANT_HEADERS, "X-Internal-Token": _TOKEN},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("tipo", _INSTITUCIONALES)
async def test_sello_con_token_del_servidor_sin_configurar_es_503_fail_closed(
    client_with_conn, monkeypatch, tipo
) -> None:
    """Token no configurado en el despliegue: ni siquiera una cabecera vacia o
    cualquiera abre el sello (fail-closed, no 'sin token configurado = abierto')."""
    from unittest.mock import AsyncMock, patch

    monkeypatch.setattr(settings, "signature_internal_token", "")
    client, _conn = client_with_conn
    with patch("app.routers.signature.signing_service.sign_payload", new=AsyncMock()) as sign:
        for hdr in ({}, {"X-Internal-Token": ""}, {"X-Internal-Token": "x"}):
            resp = await client.post(
                "/api/v1/signature/sign-xml",
                json={"payload_xml": "<x/>", "objeto_tipo": tipo, "objeto_id": "e"},
                headers={**TENANT_HEADERS, **hdr},
            )
            assert resp.status_code in (401, 403, 503), hdr
    sign.assert_not_called()


# -- B9-3: la firma humana NO pasa por /sign-xml ---------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [False, True], ids=["sin_token", "con_token_interno"])
@pytest.mark.parametrize("tipo", sorted(CADENA_OBJETO_TIPOS))
async def test_los_tipos_de_cadena_ya_no_se_firman_por_sign_xml(
    client_with_conn, monkeypatch, tipo, token
) -> None:
    """``radicado|expediente|borrador|documento`` daban una fila ``firma`` VERIFICABLE
    («X firmó el radicado R») sin cadena, sin turno, sin 2FA y sin gate XAdES. Ahora:
    422, con o sin token interno, y no se firma, ni se inserta, ni se asienta nada.

    Mutación: restaurar ``CADENA_OBJETO_TIPOS`` en el validador de
    ``SignXmlRequest.objeto_tipo`` => el sin-token cae a 201/500 y el con-token
    llega a ``sign_payload``."""
    from unittest.mock import AsyncMock, patch

    monkeypatch.setattr(settings, "signature_internal_token", _TOKEN)
    client, conn = client_with_conn
    headers = {**TENANT_HEADERS, **({"X-Internal-Token": _TOKEN} if token else {})}
    with patch("app.routers.signature.signing_service.sign_payload", new=AsyncMock()) as sign:
        resp = await client.post(
            "/api/v1/signature/sign-xml",
            json={"payload_xml": "<x/>", "objeto_tipo": tipo, "objeto_id": "o"},
            headers=headers,
        )
    assert resp.status_code == 422
    sign.assert_not_called()
    insertos = [
        c
        for c in [*conn.fetchrow.call_args_list, *conn.execute.call_args_list]
        if c.args and "INSERT" in str(c.args[0])
    ]
    assert insertos == []


@pytest.mark.asyncio
async def test_sign_xml_sin_objeto_id_de_cadena_tampoco_firma(client_with_conn) -> None:
    from unittest.mock import AsyncMock, patch

    client, _conn = client_with_conn
    with patch("app.routers.signature.signing_service.sign_payload", new=AsyncMock()) as sign:
        resp = await client.post(
            "/api/v1/signature/sign-xml",
            json={"payload_xml": "<x/>", "objeto_tipo": "radicado"},
            headers=TENANT_HEADERS,
        )
    assert resp.status_code == 422
    sign.assert_not_called()


@pytest.mark.asyncio
async def test_sign_xml_ya_no_se_abre_con_el_permiso_en_modo_lectura(client_with_conn) -> None:
    """`require_permission('PERM_FIRMA', min_crud=2)`: el nivel minimo viaja como
    parametro de la consulta RBAC. Con `min_crud=1` (default) un usuario de
    lectura firmaba."""
    client, conn = client_with_conn
    await client.post(
        "/api/v1/signature/sign-xml",
        json={"payload_xml": "<x/>", "objeto_tipo": "indice", "objeto_id": "R-1"},
        headers=TENANT_HEADERS,
    )
    rbac = [c for c in conn.fetchval.call_args_list if "auth_group_permissions" in c.args[0]]
    assert rbac and rbac[0].args[-1] == 2
