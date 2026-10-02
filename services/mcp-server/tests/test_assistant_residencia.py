# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La barrera de residencia con las FORMAS reales de document-service (AREA-11-X1/X2).

`/terceros/{id}/radicados` y `/documents/{id}/respuestas` devolvían el asunto
sin declarar `nivel_seguridad`, y la barrera solo recorta lo que declara el
nivel: el asunto de un reservado viajaba a Anthropic. Document-service ya
declara el campo; estas pruebas fijan que, con la forma que ahora produce, el
texto NO llega al proveedor en un turno real.
"""

import httpx
import pytest

from app.services.assistant import _recortar_por_residencia

# Forma exacta de `RadicadoDeTercero` y de `RespuestaItem` (listas de objetos).
_DE_TERCERO = [
    {
        "tracking_number": "2026-X-E-000321",
        "subject": "Queja disciplinaria contra el jefe de contratos",
        "doc_type": "E",
        "nivel_seguridad": 2,
        "rol": "remitente",
        "creado_at": "2026-10-01T10:00:00Z",
    },
    {
        "tracking_number": "2026-X-E-000322",
        "subject": "Solicitud de certificado laboral",
        "doc_type": "E",
        "nivel_seguridad": 1,
        "rol": "remitente",
        "creado_at": "2026-10-01T11:00:00Z",
    },
]
_RESPUESTAS = [
    {
        "id": "7a0f5c2e-0000-4000-8000-000000000001",
        "tracking_number": "2026-X-S-000045",
        "doc_type": "S",
        "subject": "Respuesta reservada al denunciante",
        "nivel_seguridad": 3,
        "status": "registered",
        "registered_at": "2026-10-01T10:00:00Z",
    }
]


@pytest.mark.parametrize("items", [_DE_TERCERO, _RESPUESTAS])
def test_el_item_clasificado_pierde_el_asunto(items):
    salida = _recortar_por_residencia(items)
    reservados = [i for i in items if i["nivel_seguridad"] >= 2]
    for original, recortado in zip(items, salida, strict=True):
        if original in reservados:
            assert "subject" not in recortado
            assert recortado["tracking_number"] == original["tracking_number"]
        else:
            assert recortado == original


@pytest.mark.asyncio
@pytest.mark.parametrize("items", [_DE_TERCERO, _RESPUESTAS])
async def test_en_el_turno_real_el_asunto_reservado_no_llega_al_proveedor(items):
    from app.services.assistant import run_turn
    from tests._assistant_fakes import (
        FakeAnthropic,
        Response,
        make_gateway,
        text_block,
        tool_use_block,
    )

    anthropic = FakeAnthropic(
        [
            Response([tool_use_block("t1", "buscar_radicados", {"q": "disciplinaria"})], "tool_use"),
            Response([text_block("listo")], "end_turn"),
        ]
    )
    gateway = make_gateway(
        permissions=["PERM_RADI"],
        tool_response=httpx.Response(status_code=200, json=items),
    )
    await run_turn(
        mensaje="qué radicados tiene esta persona",
        conversation_id=None,
        tenant_slug="demo",
        authorization="Bearer tok",
        gateway_client=gateway,
        anthropic_client=anthropic,
        conversations={},
    )
    enviado = str(anthropic.messages.calls)
    assert gateway.request.await_count == 1, "la herramienta no se despachó"
    assert items[0]["tracking_number"] in enviado, "el resultado no llegó: no se mide el recorte"
    assert items[0]["subject"] not in enviado
