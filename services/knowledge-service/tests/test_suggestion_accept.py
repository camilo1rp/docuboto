# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""CC-02 (AREA-11-11): `POST /knowledge/suggestions/{id}/aceptar` — rama HTTP.

La garantía real (propiedad en el `WHERE`, atomicidad con el asiento) se prueba en
`tests/integration/test_suggestion_accept_real.py` contra Postgres; aquí, el
contrato HTTP con la conexión simulada.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import TENANT_HEADERS

ACTOR = uuid.uuid4()
SID = uuid.uuid4()
NOW = datetime.now(tz=UTC)


_FILA = {
    "sugerencia": {
        "query": "q",
        "answer": "Texto sugerido",
        "citations": [
            {"cited_text": "SECRETO", "source_type": "radicado", "source_ref": "R-2", "score": 1},
            {"cited_text": "SECRETO", "source_type": "radicado", "source_ref": "R-1", "score": 1},
            {"cited_text": "SECRETO", "source_type": "radicado", "source_ref": "R-1", "score": 1},
        ],
    },
    "proveedor": "anthropic",
    "modelo": "claude-x",
}


def _url(sid: uuid.UUID = SID) -> str:
    return f"/api/v1/knowledge/suggestions/{sid}/aceptar"


def _h(actor: uuid.UUID | None = ACTOR) -> dict:
    base = {k: v for k, v in TENANT_HEADERS.items() if k != "X-User-Id"}
    return {**base, **({"X-User-Id": str(actor)} if actor else {})}


def _audit():
    ctx = patch("app.routers.knowledge.audit")
    m = ctx.start()
    m.append = AsyncMock()
    return ctx, m


@pytest.mark.asyncio
async def test_el_creador_acepta_y_se_asienta(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.side_effect = [
        {"id": SID, "aceptada_por": None, "aceptada_en": None, **_FILA},
        {"id": SID, "aceptada_por": ACTOR, "aceptada_en": NOW},
    ]
    ctx, audit = _audit()
    try:
        resp = await client.post(_url(), headers=_h())
    finally:
        ctx.stop()

    assert resp.status_code == 200
    assert resp.json()["aceptada_por"] == str(ACTOR)
    assert "sugerencia" not in resp.json()  # nunca el contenido
    conn.transaction.assert_called_once()
    audit.append.assert_awaited_once()
    kw = audit.append.await_args.kwargs
    assert kw["action"] == "rag.aceptada" and kw["actor"] == str(ACTOR)
    assert kw["object_ref"] == str(SID)
    # El asiento fija QUÉ se aceptó (huella + proveedor/modelo + fuentes), sin contenido.
    assert kw["payload"]["sha256_texto"] == hashlib.sha256(b"Texto sugerido").hexdigest()
    assert kw["payload"]["proveedor"] == "anthropic" and kw["payload"]["modelo"] == "claude-x"
    assert kw["payload"]["fuentes"] == ["R-1", "R-2"]
    assert "SECRETO" not in json.dumps(kw["payload"]) and "Texto sugerido" not in json.dumps(
        kw["payload"]
    )


@pytest.mark.asyncio
async def test_ajena_o_inexistente_da_404_sin_asiento(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.side_effect = [None]
    ctx, audit = _audit()
    try:
        resp = await client.post(_url(), headers=_h())
    finally:
        ctx.stop()

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "suggestion_not_found"
    audit.append.assert_not_awaited()
    assert conn.fetchrow.await_count == 1  # no llegó al UPDATE


@pytest.mark.asyncio
async def test_ya_aceptada_da_409_sin_asiento(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.side_effect = [{"id": SID, "aceptada_por": ACTOR, "aceptada_en": NOW}]
    ctx, audit = _audit()
    try:
        resp = await client.post(_url(), headers=_h())
    finally:
        ctx.stop()

    assert resp.status_code == 409
    audit.append.assert_not_awaited()


@pytest.mark.asyncio
async def test_sin_identidad_da_401(client_with_conn) -> None:
    client, conn = client_with_conn
    resp = await client.post(_url(), headers=_h(None))

    assert resp.status_code == 401
    assert conn.fetchrow.await_count == 0


@pytest.mark.asyncio
async def test_la_huella_tambien_sale_si_jsonb_llega_como_texto(client_with_conn) -> None:
    """Sin códec jsonb asyncpg entrega `str`: la huella no puede depender de eso."""
    client, conn = client_with_conn
    fila = {**_FILA, "sugerencia": json.dumps(_FILA["sugerencia"])}
    conn.fetchrow.side_effect = [
        {"id": SID, "aceptada_por": None, "aceptada_en": None, **fila},
        {"id": SID, "aceptada_por": ACTOR, "aceptada_en": NOW},
    ]
    ctx, audit = _audit()
    try:
        resp = await client.post(_url(), headers=_h())
    finally:
        ctx.stop()

    assert resp.status_code == 200
    p = audit.append.await_args.kwargs["payload"]
    assert p["sha256_texto"] == hashlib.sha256(b"Texto sugerido").hexdigest()
