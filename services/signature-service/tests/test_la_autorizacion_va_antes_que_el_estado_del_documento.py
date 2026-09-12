# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Primero quién eres, después en qué estado está el documento (B6 §14.4).

`_gate_documento_vigente` corría ANTES de las guardas de pertenencia y turno, de
modo que a quien no forma parte de la cadena se le contestaba
`409 documento_modificado` **con el número de versión vigente**: le confirmaba
que existe un circuito de firma sobre ese objeto, que el texto cambió y por
dónde va, teniendo como único requisito el nivel de acceso. Sobre la misma
solicitud y el mismo actor, `emitir_challenge` respondía `no_es_firmante` — el
criterio ya estaba escrito allí: un colapso que solo existe en uno de dos
caminos no es una defensa, es una incoherencia.

Estos tests fijan el orden en los DOS caminos que llaman al gate (`sign` y
`vobo`), y el control positivo: quien sí es parte y sí tiene el turno se sigue
topando con el 409 antes de firmar, que es lo que el gate existe para hacer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_tenant_conn
from app.main import app
from tests.conftest import TENANT_HEADERS, USER

SOL_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TURNO_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
OTRO = uuid.UUID("22222222-2222-2222-2222-222222222222")
NOW = datetime(2026, 1, 1, tzinfo=UTC)

HUELLA_APROBADA = "a" * 64
HUELLA_VIGENTE = "b" * 64  # el texto cambió


def _sol_borrador(rol_turno: str = "firma") -> dict:
    """Solicitud sobre un `borrador`: el único tipo al que el gate se aplica."""
    return {
        "id": SOL_ID,
        "objeto_tipo": "borrador",
        "objeto_id": str(uuid.uuid4()),
        "titulo": "Oficio en circuito",
        "payload_sha256": HUELLA_APROBADA,
        "estado": "pendiente",
        "creado_por": OTRO,
        "nivel_seguridad": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _turno(firmante_id: uuid.UUID, rol: str = "firma") -> dict:
    return {
        "id": TURNO_ID,
        "solicitud_id": SOL_ID,
        "orden": 1,
        "firmante_id": firmante_id,
        "rol": rol,
        "estado": "pendiente",
        "firma_id": None,
        "comentario": None,
        "decidido_at": None,
    }


def _make_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = True
    conn.fetch.return_value = []
    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    return conn


def _override(conn: AsyncMock) -> None:
    async def _conn_dep():
        yield conn

    app.dependency_overrides[get_tenant_conn] = _conn_dep


def _clear() -> None:
    app.dependency_overrides.clear()


def _espiar_huella(monkeypatch) -> list[str]:
    """Sustituye la lectura de la huella vigente y CUENTA las llamadas.

    Contarlas es la mitad del test: el gate no solo no debe decidir antes, es
    que no debe llegar a preguntar. Una consulta saliente sobre un documento
    ajeno es la misma fuga con otro disfraz —y de paso es trabajo tirado en un
    acto que se va a rechazar de todas formas—.
    """
    from app.services import cadena_service as mod

    llamadas: list[str] = []

    async def _huella(borrador_id: str, tenant_slug: str) -> dict:
        llamadas.append(borrador_id)
        return {"cuerpo_sha256": HUELLA_VIGENTE, "version": 3}

    monkeypatch.setattr(mod, "get_huella_borrador", _huella)
    return llamadas


# ---------------------------------------------------------------------------
# POST /requests/{id}/sign
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ajeno_a_la_cadena_no_se_entera_de_que_el_texto_cambio(monkeypatch) -> None:
    llamadas = _espiar_huella(monkeypatch)
    conn = _make_conn()
    conn.fetchrow.side_effect = [_sol_borrador(), {}]  # sol, clearance
    conn.fetchval.side_effect = [True, None]  # permiso, es_firmante=False

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign", headers=TENANT_HEADERS
            )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "no_es_firmante"
        assert llamadas == []
    finally:
        _clear()


@pytest.mark.asyncio
async def test_miembro_sin_turno_recibe_lo_del_turno_y_no_lo_del_documento(monkeypatch) -> None:
    llamadas = _espiar_huella(monkeypatch)
    conn = _make_conn()
    conn.fetchrow.side_effect = [_sol_borrador(), {}, _turno(OTRO)]
    conn.fetchval.side_effect = [True, 1]  # permiso, es_firmante=True

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign", headers=TENANT_HEADERS
            )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "no_es_su_turno"
        assert llamadas == []
    finally:
        _clear()


@pytest.mark.asyncio
async def test_a_quien_le_toca_si_le_para_el_gate_del_documento(monkeypatch) -> None:
    """Control positivo: mover la guarda no la desactiva donde tiene que morder."""
    llamadas = _espiar_huella(monkeypatch)
    conn = _make_conn()
    conn.fetchrow.side_effect = [_sol_borrador(), {}, _turno(USER)]
    conn.fetchval.side_effect = [True, 1]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign", headers=TENANT_HEADERS
            )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "documento_modificado"
        assert len(llamadas) == 1
    finally:
        _clear()


# ---------------------------------------------------------------------------
# POST /requests/{id}/vobo — el gemelo, que tenía el mismo orden
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vobo_ajeno_a_la_cadena_tampoco_se_entera(monkeypatch) -> None:
    llamadas = _espiar_huella(monkeypatch)
    conn = _make_conn()
    conn.fetchrow.side_effect = [_sol_borrador(), {}]
    conn.fetchval.side_effect = [True, None]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/vobo",
                headers=TENANT_HEADERS,
                json={},
            )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "no_es_firmante"
        assert llamadas == []
    finally:
        _clear()
