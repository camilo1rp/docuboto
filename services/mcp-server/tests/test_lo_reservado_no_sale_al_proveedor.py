# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lo clasificado no sale al proveedor de generación externo.

El proyecto lo decidió sin excepciones (ADR-006) y lo implementó en el servicio
de conocimiento. El asistente también genera con un proveedor externo y no
replicaba la barrera: lo que la consulta aumentada retira del contexto salía
igualmente por la herramienta de búsqueda.
"""

import pytest

from app.services.assistant import _recortar_por_residencia


def test_un_radicado_reservado_no_viaja_con_su_contenido():
    dato = {
        "id": "r-1",
        "tracking_number": "2026-X-E-000123",
        "subject": "Investigación disciplinaria contra funcionario",
        "observations": "el texto completo del expediente",
        "nivel_seguridad": 2,
    }

    salida = _recortar_por_residencia(dato)

    assert "Investigación" not in str(salida)
    assert "expediente" not in str(salida)
    assert salida["tracking_number"] == "2026-X-E-000123", (
        "el asistente debe poder decir CUÁL documento no puede mostrar"
    )
    assert "_residencia" in salida


def test_lo_publico_pasa_entero():
    """La barrera no puede dejar inservible al asistente para el trabajo normal."""
    dato = {"id": "r-2", "subject": "Solicitud de certificado", "nivel_seguridad": 1}

    assert _recortar_por_residencia(dato) == dato


def test_en_una_lista_mezclada_solo_cae_lo_clasificado():
    """Mismo criterio que la consulta aumentada: se recorta el fragmento, no la petición."""
    datos = [
        {"id": "a", "texto": "acta pública", "nivel_seguridad": 1},
        {"id": "b", "texto": "informe reservado", "nivel_seguridad": 3},
    ]

    salida = _recortar_por_residencia(datos)

    assert salida[0]["texto"] == "acta pública"
    assert "informe reservado" not in str(salida)


def test_alcanza_a_lo_anidado():
    """Un resultado puede traer los objetos dentro de otro; decidir por el nombre
    de la herramienta sería una lista que se queda corta."""
    dato = {"total": 1, "items": [{"id": "x", "texto": "secreto", "nivel_seguridad": 2}]}

    assert "secreto" not in str(_recortar_por_residencia(dato))


def test_sin_nivel_declarado_no_se_inventa_uno():
    """Un objeto sin clasificación no se recorta: el nivel es dato del objeto.

    Recortar por defecto lo que no declara nivel dejaría al asistente sin poder
    leer respuestas que no son documentos —listados de dependencias, catálogos—
    y un gate que suprime lo que no entiende acaba desactivado.
    """
    dato = {"codigo": 901, "nombre": "Ventanilla única"}

    assert _recortar_por_residencia(dato) == dato


@pytest.mark.asyncio
async def test_la_barrera_ESTA_CABLEADA_en_el_turno_real():
    """Probar la función aislada no prueba que se invoque.

    Es la aserción que importa y la que faltaba: se ejecuta un turno completo
    con una herramienta que devuelve un radicado reservado, y se comprueba que
    el texto NO aparece en lo que se le manda al proveedor. Sin esto, quitar la
    llamada a la barrera dejaba las demás pruebas en verde.
    """
    import httpx

    from app.services.assistant import run_turn
    from tests._assistant_fakes import (
        FakeAnthropic,
        Response,
        make_gateway,
        text_block,
        tool_use_block,
    )

    reservado = {
        "id": "r-9",
        "tracking_number": "2026-X-E-000999",
        "subject": "Investigación disciplinaria",
        "observations": "el cuerpo entero del expediente reservado",
        "nivel_seguridad": 3,
    }
    anthropic = FakeAnthropic(
        [
            Response([tool_use_block("t1", "buscar_radicados", {"q": "disciplinaria"})], "tool_use"),
            Response([text_block("listo")], "end_turn"),
        ]
    )
    gateway = make_gateway(
        permissions=["PERM_RADI"],
        tool_response=httpx.Response(status_code=200, json=reservado),
    )

    await run_turn(
        mensaje="busca lo de la investigación",
        conversation_id=None,
        tenant_slug="demo",
        authorization="Bearer tok",
        gateway_client=gateway,
        anthropic_client=anthropic,
        conversations={},
    )

    # Lo que de verdad se le mandó al proveedor, no lo que devolvió la tool.
    enviado = str(anthropic.messages.calls)
    # CONTROL POSITIVO, primero: una aserción de ausencia es trivialmente cierta
    # si la herramienta nunca llegó a ejecutarse. Sin esto, la prueba pasaba
    # incluso con la barrera desconectada.
    assert gateway.request.await_count == 1, "la herramienta no se despachó: la prueba no mide nada"
    assert "2026-X-E-000999" in enviado, (
        "el resultado de la herramienta no llegó al proveedor: no se está midiendo el recorte"
    )

    assert "expediente reservado" not in enviado, (
        "el contenido clasificado viajó al proveedor de generación externo"
    )
    assert "Investigación disciplinaria" not in enviado
