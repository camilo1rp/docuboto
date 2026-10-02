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
`X-Internal-Token`; fuera del allow-set y de los tipos de cadena, 422.
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


@pytest.mark.asyncio
@pytest.mark.parametrize("tipo", sorted(CADENA_OBJETO_TIPOS))
async def test_tipos_de_cadena_siguen_sin_exigir_token_interno(
    client_with_conn, monkeypatch, tipo
) -> None:
    """Control positivo: el gate es SOLO del sello; el resto (HMAC nativo, flujo
    humano) no se rompe. Sin el, el test de 'sin token = 401' pasaria en vacio
    si el gate se aplicara a todo."""
    from unittest.mock import AsyncMock, patch

    monkeypatch.setattr(settings, "signature_internal_token", _TOKEN)
    client, _conn = client_with_conn
    with (
        patch(
            "app.routers.signature.CadenaRepository.objeto_visible",
            new=AsyncMock(return_value=True),
        ),
        patch("app.routers.signature.signing_service.sign_payload", new=AsyncMock()) as sign,
    ):
        sign.side_effect = RuntimeError("llego al servicio")
        with pytest.raises(RuntimeError, match="llego al servicio"):
            await client.post(
                "/api/v1/signature/sign-xml",
                json={"payload_xml": "<x/>", "objeto_tipo": tipo, "objeto_id": "o"},
                headers=TENANT_HEADERS,
            )
    sign.assert_awaited_once()


# -- AREA-04-07 (residual): tipos de cadena -------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("tipo", sorted(CADENA_OBJETO_TIPOS))
async def test_objeto_inexistente_o_por_encima_del_clearance_es_404_sin_firmar(
    client_with_conn, tipo
) -> None:
    """Sin fila visible (no existe, o lo supera el clearance) => 404 neutro y
    `sign_payload` no se llama. El mock devuelve `None` en `fetchrow`: ninguna
    tabla aporta coincidencias."""
    from unittest.mock import AsyncMock, patch

    client, _conn = client_with_conn
    with patch("app.routers.signature.signing_service.sign_payload", new=AsyncMock()) as sign:
        resp = await client.post(
            "/api/v1/signature/sign-xml",
            json={"payload_xml": "<x/>", "objeto_tipo": tipo, "objeto_id": "no-existe"},
            headers=TENANT_HEADERS,
        )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "objeto_no_encontrado"
    sign.assert_not_called()


@pytest.mark.asyncio
async def test_objeto_id_ausente_en_tipo_de_cadena_es_404(client_with_conn) -> None:
    from unittest.mock import AsyncMock, patch

    client, _conn = client_with_conn
    with patch("app.routers.signature.signing_service.sign_payload", new=AsyncMock()) as sign:
        resp = await client.post(
            "/api/v1/signature/sign-xml",
            json={"payload_xml": "<x/>", "objeto_tipo": "radicado"},
            headers=TENANT_HEADERS,
        )
    assert resp.status_code == 404
    sign.assert_not_called()


@pytest.mark.asyncio
async def test_el_clearance_viaja_dentro_de_la_consulta_del_objeto(client_with_conn) -> None:
    """El gate va en el SQL (FILTER ... <= $2), con el clearance REAL del firmante
    como parametro — no en una comparacion posterior."""
    client, conn = client_with_conn
    conn.fetchrow.side_effect = [
        {"is_root": False, "max_level": 2},  # resolve_caller_clearance
        {"total": 1, "visibles": 1},  # radicados
        {"total": 0, "visibles": 0},  # expedientes
    ]
    from unittest.mock import AsyncMock, patch

    with patch(
        "app.routers.signature.signing_service.sign_payload", new=AsyncMock()
    ) as sign:
        sign.side_effect = RuntimeError("firmo")
        with pytest.raises(RuntimeError, match="firmo"):
            await client.post(
                "/api/v1/signature/sign-xml",
                json={"payload_xml": "<x/>", "objeto_tipo": "radicado", "objeto_id": "R-1"},
                headers=TENANT_HEADERS,
            )
    consultas = [c for c in conn.fetchrow.call_args_list if "FILTER" in c.args[0]]
    assert consultas, "la consulta del objeto no lleva el clearance en el SQL"
    assert all(c.args[2] == 2 for c in consultas)


@pytest.mark.asyncio
async def test_coincidencia_que_supera_el_clearance_no_es_visible(client_with_conn) -> None:
    from unittest.mock import AsyncMock, patch

    client, conn = client_with_conn
    conn.fetchrow.side_effect = [
        {"is_root": False, "max_level": 1},
        {"total": 1, "visibles": 0},  # existe pero por encima
        {"total": 0, "visibles": 0},
    ]
    with patch("app.routers.signature.signing_service.sign_payload", new=AsyncMock()) as sign:
        resp = await client.post(
            "/api/v1/signature/sign-xml",
            json={"payload_xml": "<x/>", "objeto_tipo": "expediente", "objeto_id": "E-1"},
            headers=TENANT_HEADERS,
        )
    assert resp.status_code == 404
    sign.assert_not_called()


@pytest.mark.asyncio
async def test_sign_xml_ya_no_se_abre_con_el_permiso_en_modo_lectura(client_with_conn) -> None:
    """`require_permission('PERM_FIRMA', min_crud=2)`: el nivel minimo viaja como
    parametro de la consulta RBAC. Con `min_crud=1` (default) un usuario de
    lectura firmaba."""
    client, conn = client_with_conn
    await client.post(
        "/api/v1/signature/sign-xml",
        json={"payload_xml": "<x/>", "objeto_tipo": "radicado", "objeto_id": "R-1"},
        headers=TENANT_HEADERS,
    )
    rbac = [c for c in conn.fetchval.call_args_list if "auth_group_permissions" in c.args[0]]
    assert rbac and rbac[0].args[-1] == 2
