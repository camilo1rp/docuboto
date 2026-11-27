# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`signature_api.get_estado_circuito` — lo que hace que el circuito ate.

document lo pregunta antes de aprobar o radicar un borrador. Sin `PERM_FIRMA` y sin
clearance porque la respuesta es content-free (dos booleanos, un id y una huella).
Que sea content-free es la razón de que pueda no llevar gate — no al revés.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest

from signature import api as signature_api
from tests.signature.conftest import en_proceso


async def _estado() -> dict:
    return await signature_api.get_estado_circuito(
        borrador_id=str(uuid.uuid4()), tenant_slug="demo"
    )


@pytest.mark.asyncio
async def test_circuito_abierto(client_with_conn, monkeypatch) -> None:
    _client, conn = client_with_conn
    en_proceso(monkeypatch, conn)
    sid = uuid.uuid4()
    # Dos consultas distintas: la cadena ABIERTA y la última CERRADA (de donde
    # sale la huella firmada). Un `return_value` único las confundiría.
    conn.fetchrow.side_effect = [{"id": sid, "estado": "pendiente"}, None]

    body = await _estado()

    assert body["pendiente"] is True
    assert body["solicitud_id"] == str(sid)


@pytest.mark.asyncio
async def test_sin_ningun_circuito_ambos_false(client_with_conn, monkeypatch) -> None:
    """El camino directo de siempre: sin circuito, aprobar no encuentra nada que respetar."""
    _client, conn = client_with_conn
    en_proceso(monkeypatch, conn)
    conn.fetchrow.side_effect = [None, None]

    body = await _estado()

    assert body["pendiente"] is False
    assert body["completo"] is False
    assert body["solicitud_id"] is None
    assert body["payload_sha256"] is None


@pytest.mark.asyncio
async def test_circuito_cerrado_no_es_pendiente(client_with_conn, monkeypatch) -> None:
    """`pendiente` y `completo` no son opuestos.

    Un circuito resuelto deja de bloquear la aprobación, y eso es lo que
    permite que el aviso automático pueda perderse sin consecuencias.
    """
    _client, conn = client_with_conn
    en_proceso(monkeypatch, conn)
    conn.fetchrow.side_effect = [None, {"payload_sha256": "huella-del-texto-firmado"}]

    body = await _estado()

    assert body == {
        "objeto_tipo": "borrador",
        "objeto_id": body["objeto_id"],
        "pendiente": False,
        "completo": True,
        "solicitud_id": None,
        # La huella viaja con el estado: sin ella, quien radica no puede notar
        # que el texto cambió después de firmarse.
        "payload_sha256": "huella-del-texto-firmado",
    }


@pytest.mark.asyncio
async def test_el_gate_busca_por_objeto_no_por_el_tipo_declarado(client_with_conn, monkeypatch) -> None:
    """Una cadena abierta con otra etiqueta sobre el MISMO borrador no puede esconderse."""
    _client, conn = client_with_conn
    en_proceso(monkeypatch, conn)
    conn.fetchrow.side_effect = [None, None]

    await _estado()

    for llamada in conn.fetchrow.call_args_list:
        assert "objeto_tipo" not in llamada.args[0].split("WHERE", 1)[1]


@pytest.mark.asyncio
async def test_completo_sale_de_la_cadena_cerrada_no_de_un_contador(
    client_with_conn, monkeypatch
) -> None:
    """`completo` y la huella salen de la MISMA fila, o podrían contradecirse.

    Si `completo` viniera de una consulta y la huella de otra, existiría el
    estado imposible «se firmó, pero no sé qué se firmó», y quien radica
    resolvería esa duda por el lado permisivo.
    """
    _client, conn = client_with_conn
    en_proceso(monkeypatch, conn)
    conn.fetchrow.side_effect = [None, None]
    conn.fetchval.return_value = 1  # un contador rancio no puede decidir esto

    body = await _estado()

    assert body["completo"] is False
    assert body["payload_sha256"] is None


@pytest.mark.asyncio
async def test_no_saber_es_no_poder_aprobar(monkeypatch) -> None:
    """Un fallo (entidad vetada, BD caída) es `CircuitoNoVerificableError`, nunca «no hay
    circuito»: document responde 503 y no aprueba."""

    @asynccontextmanager
    async def _caida(*_a, **_k):
        raise RuntimeError("BD caída")
        yield  # pragma: no cover

    monkeypatch.setattr(signature_api, "_tenant_conn", _caida)
    with pytest.raises(signature_api.CircuitoNoVerificableError):
        await _estado()
