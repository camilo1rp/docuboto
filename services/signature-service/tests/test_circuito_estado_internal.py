# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`GET /internal/circuitos/{tipo}/{id}` — lo que hace que el circuito ate.

document-service consulta esta ruta antes de aprobar un borrador. Es un acto de
sistema: sellado con `X-Internal-Token`, sin `PERM_FIRMA` y sin clearance,
porque la respuesta es content-free (dos booleanos y un identificador). Que sea
content-free es la razón de que pueda no llevar gate — no al revés.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.config import settings

_TOKEN = "itest-internal-token-circuitos-xyz789"
_RUTA = "/api/v1/signature/internal/circuitos/borrador"


def _headers(token: str | None = _TOKEN) -> dict:
    h = {"X-Tenant-Slug": "demo"}
    if token is not None:
        h["X-Internal-Token"] = token
    return h


@pytest.mark.asyncio
async def test_circuito_abierto(client_with_conn, monkeypatch) -> None:
    client, conn = client_with_conn
    monkeypatch.setattr(settings, "signature_internal_token", _TOKEN)
    sid = uuid.uuid4()
    conn.fetchrow.return_value = {"id": sid, "estado": "pendiente"}
    conn.fetchval.return_value = None

    resp = await client.get(f"{_RUTA}/{uuid.uuid4()}", headers=_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert body["pendiente"] is True
    assert body["solicitud_id"] == str(sid)


@pytest.mark.asyncio
async def test_sin_ningun_circuito_ambos_false(client_with_conn, monkeypatch) -> None:
    """El camino directo de siempre: sin circuito, aprobar no encuentra nada que respetar."""
    client, conn = client_with_conn
    monkeypatch.setattr(settings, "signature_internal_token", _TOKEN)
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None

    resp = await client.get(f"{_RUTA}/{uuid.uuid4()}", headers=_headers())

    assert resp.status_code == 200
    assert resp.json()["pendiente"] is False
    assert resp.json()["completo"] is False
    assert resp.json()["solicitud_id"] is None


@pytest.mark.asyncio
async def test_circuito_cerrado_no_es_pendiente(client_with_conn, monkeypatch) -> None:
    """`pendiente` y `completo` no son opuestos.

    Un circuito resuelto deja de bloquear la aprobación, y eso es lo que
    permite que el aviso automático pueda perderse sin consecuencias.
    """
    client, conn = client_with_conn
    monkeypatch.setattr(settings, "signature_internal_token", _TOKEN)
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = 1

    resp = await client.get(f"{_RUTA}/{uuid.uuid4()}", headers=_headers())

    assert resp.json() == {
        "objeto_tipo": "borrador",
        "objeto_id": resp.json()["objeto_id"],
        "pendiente": False,
        "completo": True,
        "solicitud_id": None,
    }


@pytest.mark.asyncio
async def test_sin_token_no_se_responde(client_with_conn, monkeypatch) -> None:
    client, conn = client_with_conn
    monkeypatch.setattr(settings, "signature_internal_token", _TOKEN)

    resp = await client.get(f"{_RUTA}/{uuid.uuid4()}", headers=_headers(token=None))

    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_token_equivocado_no_se_responde(client_with_conn, monkeypatch) -> None:
    client, conn = client_with_conn
    monkeypatch.setattr(settings, "signature_internal_token", _TOKEN)

    resp = await client.get(f"{_RUTA}/{uuid.uuid4()}", headers=_headers(token="otro"))

    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_sin_token_configurado_es_503_no_se_deja_pasar(
    client_with_conn, monkeypatch
) -> None:
    """Un despliegue sin el secreto no puede responder «no hay circuito».

    Es el mismo criterio que el resto de gates D-02: la falta de configuración
    es un problema del servicio, no de la petición, y el lado seguro de no
    saber es no contestar.
    """
    client, conn = client_with_conn
    monkeypatch.setattr(settings, "signature_internal_token", "")

    resp = await client.get(f"{_RUTA}/{uuid.uuid4()}", headers=_headers())

    assert resp.status_code == 503
