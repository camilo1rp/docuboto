# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests de la orquestación del asistente (E18/ADR-019): `app.services.assistant`.

Cubren el loop manual sobre Claude (simulado), el filtrado v1 SOLO-LECTURA, la
defensa en profundidad de `_execute_tool`, el aislamiento del historial por
tenant, los topes (iteraciones/turnos) y el mapeo de identidad. NUNCA se toca la
red: Anthropic y el gateway están simulados (ver `_assistant_fakes`).
"""

from __future__ import annotations

import httpx
import pytest

from app.services import assistant
from app.services.assistant import (
    ConversationLimitError,
    ConversationState,
    run_turn,
    to_anthropic_schema,
)
from app.services.identity import IdentityError
from app.tools import catalog
from tests._assistant_fakes import (
    FakeAnthropic,
    Response,
    make_gateway,
    text_block,
    tool_use_block,
)


async def _run(
    *,
    anthropic,
    gateway,
    mensaje="hola",
    conversation_id=None,
    tenant_slug="demo",
    conversations=None,
):
    return await run_turn(
        mensaje=mensaje,
        conversation_id=conversation_id,
        tenant_slug=tenant_slug,
        authorization="Bearer tok",
        gateway_client=gateway,
        anthropic_client=anthropic,
        conversations=conversations if conversations is not None else {},
    )


# -- camino feliz -----------------------------------------------------


@pytest.mark.asyncio
async def test_end_turn_devuelve_texto_y_conv_id() -> None:
    anthropic = FakeAnthropic([Response([text_block("Hay 3 radicados.")], "end_turn")])
    gateway = make_gateway(permissions=[])
    conversations: dict = {}

    resp = await _run(anthropic=anthropic, gateway=gateway, conversations=conversations)

    assert resp.message.texto == "Hay 3 radicados."
    assert resp.conversation_id  # uuid generado
    # el historial quedó registrado bajo (tenant, user_id, conv_id) y contó el turno
    state = conversations[("demo", "user-1", resp.conversation_id)]
    assert state.turn_count == 1
    assert state.messages[0] == {"role": "user", "content": "hola"}


@pytest.mark.asyncio
async def test_loop_de_tool_despacha_al_gateway() -> None:
    # 1ª respuesta pide la tool; 2ª cierra con texto.
    anthropic = FakeAnthropic(
        [
            Response([tool_use_block("t1", "buscar_radicados", {"q": "tutela"})], "tool_use"),
            Response([text_block("Encontré 1 radicado sobre tutela.")], "end_turn"),
        ]
    )
    gateway = make_gateway(permissions=[], tool_response=httpx.Response(200, json={"total": 1}))

    resp = await _run(anthropic=anthropic, gateway=gateway)

    assert "tutela" in resp.message.texto
    # el dispatcher tradujo la tool a GET /api/v1/search
    despachos = [c.args[1] for c in gateway.request.call_args_list]
    assert "/api/v1/search" in despachos


# -- v1 SOLO LECTURA: la tool de escritura nunca se ofrece ------------


@pytest.mark.asyncio
async def test_write_tool_excluida_del_conjunto_ofrecido() -> None:
    anthropic = FakeAnthropic([Response([text_block("ok")], "end_turn")])
    # ROOT ve TODAS en el catálogo, pero el asistente v1 filtra las write.
    gateway = make_gateway(permissions=["PERM_RADI"], roles=["ROOT"])

    await _run(anthropic=anthropic, gateway=gateway)

    offered = {t["name"] for t in anthropic.messages.calls[0]["tools"]}
    assert "radicar_documento" not in offered  # write=True excluida
    assert "buscar_radicados" in offered and "consultar_expediente" in offered


@pytest.mark.asyncio
async def test_tools_filtradas_por_permiso() -> None:
    anthropic = FakeAnthropic([Response([text_block("ok")], "end_turn")])
    gateway = make_gateway(permissions=[])  # sin permisos, no root

    await _run(anthropic=anthropic, gateway=gateway)

    offered = {t["name"] for t in anthropic.messages.calls[0]["tools"]}
    # todas las de lectura no exigen permiso atómico -> visibles
    assert "buscar_radicados" in offered
    assert "radicar_documento" not in offered


# -- defensa en profundidad de _execute_tool -------------------------


@pytest.mark.asyncio
async def test_tool_fuera_del_conjunto_se_deniega_sin_tocar_gateway() -> None:
    # El modelo "alucina"/es inducido a pedir radicar_documento (write, NO
    # ofrecida). _execute_tool debe denegar sin despachar; luego cierra.
    anthropic = FakeAnthropic(
        [
            Response(
                [tool_use_block("t1", "radicar_documento", {"foo": "bar"})],
                "tool_use",
            ),
            Response([text_block("No puedo radicar desde aquí.")], "end_turn"),
        ]
    )
    gateway = make_gateway(permissions=["PERM_RADI"], roles=["ROOT"])
    conversations: dict = {}

    resp = await _run(anthropic=anthropic, gateway=gateway, conversations=conversations)

    # El gateway solo recibió LECTURAS de contexto —identidad y marco de alcance—,
    # nunca una llamada de despacho. Se comprueba por lo que NO está en la lista,
    # no por igualdad exacta: el marco de alcance (bloque A) añadió `/auth/clearance`
    # y `/auth/context`, y un test atado a la lista literal habría marcado en rojo
    # una lectura legítima mientras dejaba de vigilar lo único que importa aquí.
    called_paths = [c.args[0] for c in gateway.get.call_args_list]
    lecturas_de_contexto = {
        "/api/v1/auth/me",
        "/api/v1/auth/clearance",
        "/api/v1/auth/context",
    }
    assert set(called_paths) <= lecturas_de_contexto, (
        f"se despachó algo que no es contexto: {sorted(set(called_paths) - lecturas_de_contexto)}"
    )
    # `request` es exclusivo de los despachos de tool: las lecturas de contexto
    # (identidad, marco de alcance) usan `.get`. Así esta aserción dice
    # exactamente «no se despachó ninguna tool», y no se vacía si mañana la tool
    # que se intenta cambia de método.
    assert gateway.request.await_count == 0
    # el tool_result inyectado al modelo marcó el error (se busca en el historial;
    # los kwargs de `create` comparten la MISMA lista por referencia, así que se
    # inspecciona el estado final de la conversación)
    state = conversations[("demo", "user-1", resp.conversation_id)]
    tool_results = [
        b
        for m in state.messages
        if isinstance(m["content"], list)
        for b in m["content"]
        if isinstance(b, dict) and b.get("type") == "tool_result"
    ]
    assert len(tool_results) == 1
    assert tool_results[0]["is_error"] is True
    assert "herramienta_no_disponible" in tool_results[0]["content"]
    assert resp.message.texto  # respondió con normalidad


# -- degradación honesta: tope de iteraciones ------------------------


@pytest.mark.asyncio
async def test_tope_de_iteraciones_degrada_honestamente(monkeypatch) -> None:
    monkeypatch.setattr(assistant.settings, "assistant_max_iterations", 2)
    # Siempre pide tool -> nunca cierra -> se agota el tope.
    anthropic = FakeAnthropic(
        [
            Response([tool_use_block("t1", "buscar_radicados", {"q": "x"})], "tool_use"),
            Response([tool_use_block("t2", "buscar_radicados", {"q": "y"})], "tool_use"),
        ]
    )
    gateway = make_gateway(permissions=[], tool_response=httpx.Response(200, json={}))

    resp = await _run(anthropic=anthropic, gateway=gateway)

    assert "límite de pasos" in resp.message.texto
    assert len(anthropic.messages.calls) == 2  # respetó el tope


@pytest.mark.asyncio
async def test_max_tokens_marca_respuesta_truncada() -> None:
    anthropic = FakeAnthropic([Response([text_block("Respuesta parcial")], "max_tokens")])
    gateway = make_gateway(permissions=[])

    resp = await _run(anthropic=anthropic, gateway=gateway)

    assert "Respuesta parcial" in resp.message.texto
    assert "truncada" in resp.message.texto


@pytest.mark.asyncio
async def test_refusal_da_mensaje_neutro() -> None:
    anthropic = FakeAnthropic([Response([], "refusal")])
    gateway = make_gateway(permissions=[])

    resp = await _run(anthropic=anthropic, gateway=gateway)

    assert "No puedo continuar" in resp.message.texto


# -- historial: tope de turnos y aislamiento por tenant --------------


@pytest.mark.asyncio
async def test_tope_de_turnos_por_conversacion(monkeypatch) -> None:
    monkeypatch.setattr(assistant.settings, "assistant_max_turns", 1)
    anthropic = FakeAnthropic([Response([text_block("ok")], "end_turn")])
    gateway = make_gateway(permissions=[])
    conversations: dict = {("demo", "user-1", "c1"): ConversationState(turn_count=1)}

    with pytest.raises(ConversationLimitError):
        await _run(
            anthropic=anthropic,
            gateway=gateway,
            conversation_id="c1",
            conversations=conversations,
        )


@pytest.mark.asyncio
async def test_conversation_id_no_cruza_usuarios_del_mismo_tenant() -> None:
    # Fix del auditor de seguridad (#1, Alta): un conversation_id NO puede ser
    # reanudado por otro usuario del mismo tenant -> la llave incluye user_id.
    conversations: dict = {}
    anthropic_a = FakeAnthropic([Response([text_block("secreto de A")], "end_turn")])
    gw_a = make_gateway(permissions=[], user_id="user-A")
    await _run(
        anthropic=anthropic_a,
        gateway=gw_a,
        conversation_id="shared",
        tenant_slug="demo",
        conversations=conversations,
    )

    # el usuario B, mismo tenant, mismo conversation_id: NO ve el historial de A
    anthropic_b = FakeAnthropic([Response([text_block("respuesta a B")], "end_turn")])
    gw_b = make_gateway(permissions=[], user_id="user-B")
    await _run(
        anthropic=anthropic_b,
        gateway=gw_b,
        conversation_id="shared",
        tenant_slug="demo",
        conversations=conversations,
    )

    assert ("demo", "user-A", "shared") in conversations
    assert ("demo", "user-B", "shared") in conversations
    # el historial de B arranca vacío (solo su propio turno), sin los mensajes de A
    b_state = conversations[("demo", "user-B", "shared")]
    assert b_state.turn_count == 1
    assert all("secreto de A" not in str(m.get("content")) for m in b_state.messages)


@pytest.mark.asyncio
async def test_identidad_sin_user_id_falla_cerrado() -> None:
    # /auth/me sin user_id -> no se puede aislar el historial -> 401 fail-closed.
    anthropic = FakeAnthropic([Response([text_block("x")], "end_turn")])
    gateway = make_gateway(permissions=[], user_id="")

    with pytest.raises(IdentityError) as ei:
        await _run(anthropic=anthropic, gateway=gateway)
    assert ei.value.status == 401


@pytest.mark.asyncio
async def test_403_y_404_colapsan_a_generico_para_el_modelo() -> None:
    # Fix de ambos auditores (#2/#4, Media): el status crudo no llega al modelo;
    # 403 (sin permiso) y 404 (no existe) son INDISTINGUIBLES (no-oráculo).
    for status in (403, 404):
        anthropic = FakeAnthropic(
            [
                Response(
                    [tool_use_block("t1", "consultar_expediente", {"expediente_id": "11111111-1111-1111-1111-111111111111"})],
                    "tool_use",
                ),
                Response([text_block("No encontré ese expediente.")], "end_turn"),
            ]
        )
        # CON el permiso: lo que aquí se fija es el colapso de lo que responde
        # el GATEWAY, y sin permiso la tool ni se despacharía — el `authorize`
        # local respondería `herramienta_no_disponible` y este test estaría
        # comprobando otra cosa. Ese otro camino no es un oráculo: dice que a
        # esta persona le falta un permiso, no si el expediente existe.
        gateway = make_gateway(
            permissions=["USUA_PERM_EXPEDIENTE"],
            tool_response=httpx.Response(status, json={"detail": "clasificado"}),
        )
        conversations: dict = {}

        resp = await _run(anthropic=anthropic, gateway=gateway, conversations=conversations)

        state = conversations[("demo", "user-1", resp.conversation_id)]
        tr = [
            b
            for m in state.messages
            if isinstance(m["content"], list)
            for b in m["content"]
            if isinstance(b, dict) and b.get("type") == "tool_result"
        ][0]
        # el payload es SOLO no_encontrado: ni status, ni 403/404, ni "clasificado"
        assert tr["content"] == '{"error": "no_encontrado"}'
        assert "403" not in tr["content"] and "404" not in tr["content"]
        assert "clasificado" not in tr["content"]


@pytest.mark.asyncio
async def test_conversation_id_no_cruza_tenants() -> None:
    # Mismo conversation_id, distinto tenant -> estados independientes.
    anthropic = FakeAnthropic(
        [
            Response([text_block("a")], "end_turn"),
            Response([text_block("b")], "end_turn"),
        ]
    )
    gateway = make_gateway(permissions=[])
    conversations: dict = {}

    await _run(
        anthropic=anthropic,
        gateway=gateway,
        conversation_id="shared",
        tenant_slug="t1",
        conversations=conversations,
    )
    await _run(
        anthropic=anthropic,
        gateway=gateway,
        conversation_id="shared",
        tenant_slug="t2",
        conversations=conversations,
    )

    assert ("t1", "user-1", "shared") in conversations
    assert ("t2", "user-1", "shared") in conversations
    # cada uno con su propio historial (un turno), sin mezclar
    assert conversations[("t1", "user-1", "shared")].turn_count == 1
    assert conversations[("t2", "user-1", "shared")].turn_count == 1


# -- identidad --------------------------------------------------------


@pytest.mark.asyncio
async def test_identidad_401_propaga_identity_error() -> None:
    anthropic = FakeAnthropic([Response([text_block("no debería llegar")], "end_turn")])
    gateway = make_gateway(me_status=401)

    with pytest.raises(IdentityError) as ei:
        await _run(anthropic=anthropic, gateway=gateway)
    assert ei.value.status == 401


@pytest.mark.asyncio
async def test_identidad_gateway_caido_da_502() -> None:
    anthropic = FakeAnthropic([Response([text_block("x")], "end_turn")])
    gateway = make_gateway()

    async def _boom(path, **kwargs):
        raise httpx.ConnectError("down")

    gateway.get.side_effect = _boom

    with pytest.raises(IdentityError) as ei:
        await _run(anthropic=anthropic, gateway=gateway)
    assert ei.value.status == 502


# -- E21: buscar_conocimiento (integración knowledge-service) --------


@pytest.mark.asyncio
async def test_buscar_conocimiento_se_ofrece_a_cualquier_usuario() -> None:
    # permission=None + write=False -> visible incluso sin permisos especiales
    # (espejo negativo de test_write_tool_excluida_del_conjunto_ofrecido).
    anthropic = FakeAnthropic([Response([text_block("ok")], "end_turn")])
    gateway = make_gateway(permissions=[])

    await _run(anthropic=anthropic, gateway=gateway)

    offered = {t["name"] for t in anthropic.messages.calls[0]["tools"]}
    assert "buscar_conocimiento" in offered


@pytest.mark.asyncio
async def test_buscar_conocimiento_despacha_post_al_gateway_con_token_del_usuario() -> None:
    anthropic = FakeAnthropic(
        [
            Response(
                [tool_use_block("t1", "buscar_conocimiento", {"query": "tutela salud", "top_k": 3})],
                "tool_use",
            ),
            Response([text_block("Encontré fragmentos relevantes.")], "end_turn"),
        ]
    )
    tool_response = httpx.Response(
        200,
        json=[
            {
                "id": "chunk-1",
                "source_type": "radicado",
                "source_ref": "2024-ICETEX-E-001234",
                "texto": "fragmento relevante",
                "metadata": {},
                "score": 0.9,
            }
        ],
    )
    gateway = make_gateway(permissions=[], tool_response=tool_response)

    resp = await _run(anthropic=anthropic, gateway=gateway)

    assert "fragmentos" in resp.message.texto
    # el dispatcher tradujo la tool a POST /api/v1/knowledge/search con el body
    # {query, top_k}, propagando Authorization + X-Tenant-Slug del usuario.
    assert gateway.request.await_count == 1
    call = gateway.request.await_args
    assert call.args[0] == "POST"
    assert call.args[1] == "/api/v1/knowledge/search"
    assert call.kwargs["json"] == {"query": "tutela salud", "top_k": 3}
    assert call.kwargs["headers"]["Authorization"] == "Bearer tok"
    assert call.kwargs["headers"]["X-Tenant-Slug"] == "demo"


@pytest.mark.asyncio
async def test_input_no_declarado_se_descarta_antes_de_despachar() -> None:
    # Defensa en profundidad: el modelo (o una inyección) cuela un `acl` NO
    # declarado en el schema de buscar_conocimiento -> se descarta del body
    # despachado (el techo de clearance server-side ya lo impediría, pero el
    # contrato ofrecido debe ser el despachado).
    anthropic = FakeAnthropic(
        [
            Response(
                [
                    tool_use_block(
                        "t1",
                        "buscar_conocimiento",
                        {"query": "algo", "acl": {"nivel_seguridad": 3}, "otra": "x"},
                    )
                ],
                "tool_use",
            ),
            Response([text_block("ok")], "end_turn"),
        ]
    )
    gateway = make_gateway(permissions=[], tool_response=httpx.Response(200, json=[]))

    await _run(anthropic=anthropic, gateway=gateway)

    body = gateway.request.await_args.kwargs["json"]
    assert body == {"query": "algo"}  # `acl` y `otra` (no declaradas) descartadas


def test_buscar_conocimiento_schema_no_expone_acl() -> None:
    tool = catalog.get_tool("buscar_conocimiento")
    assert tool is not None
    schema = to_anthropic_schema(tool)
    input_schema = schema["input_schema"]

    assert input_schema["required"] == ["query"]
    assert "acl" not in input_schema["properties"]
    assert input_schema["additionalProperties"] is False

    # Los límites se alinearon con el backend al migrar los parámetros al
    # catálogo. `SearchRequest` (knowledge-service/app/routers/knowledge.py)
    # declara `query: min_length=1` y `top_k: ge=1, le=50`; la tabla paralela que
    # había aquí anunciaba al modelo `minLength: 2`, y este test **congelaba esa
    # divergencia**: nadie la revalidaba contra la API porque el test la daba por
    # buena. Anunciar al modelo un contrato más estrecho que el real le hace
    # descartar consultas que el sistema sí acepta.
    assert input_schema["properties"]["query"]["minLength"] == 1

    # `top_k` sí se queda por debajo del backend (20 frente a 50), pero ahora es
    # una decisión declarada en el catálogo —un límite del ASISTENTE para no
    # inundar la ventana de contexto del modelo— y no una deriva heredada.
    assert input_schema["properties"]["top_k"]["minimum"] == 1
    assert input_schema["properties"]["top_k"]["maximum"] == 20


@pytest.mark.asyncio
async def test_buscar_conocimiento_403_404_colapsan_a_generico() -> None:
    # No-oráculo (RF-SEG-08): igual que las demás tools de lectura, un 403/404
    # del knowledge-service no debe filtrar "existe pero está clasificado".
    for status in (403, 404):
        anthropic = FakeAnthropic(
            [
                Response(
                    [tool_use_block("t1", "buscar_conocimiento", {"query": "x"})],
                    "tool_use",
                ),
                Response([text_block("No encontré resultados.")], "end_turn"),
            ]
        )
        gateway = make_gateway(
            permissions=[], tool_response=httpx.Response(status, json={"detail": "clasificado"})
        )
        conversations: dict = {}

        resp = await _run(anthropic=anthropic, gateway=gateway, conversations=conversations)

        state = conversations[("demo", "user-1", resp.conversation_id)]
        tr = [
            b
            for m in state.messages
            if isinstance(m["content"], list)
            for b in m["content"]
            if isinstance(b, dict) and b.get("type") == "tool_result"
        ][0]
        assert tr["content"] == '{"error": "no_encontrado"}'
