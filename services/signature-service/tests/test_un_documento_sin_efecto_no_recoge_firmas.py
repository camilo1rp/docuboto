# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Un radicado anulado no recoge firmas (E01/RF-RAD, E06).

Anular es irreversible y conserva el número; lo que deja de valer es el
documento. Firmarlo después compromete a la entidad sobre algo que ella misma
declaró sin efecto, y la firma sobrevive al motivo por el que se pidió.

**Se comprueba en los dos extremos, y eso es el fondo del asunto.** La pantalla
ya no ofrecía el acto sobre un radicado anulado, pero eso es cortesía, no
barrera: por el API se abría igual. Y un control solo en la creación tampoco
bastaría, porque anular puede ocurrir ENTRE la apertura de la cadena y cada
turno — todas las cadenas ya abiertas quedarían firmables. Mismo criterio que el
gate de vigencia del documento.

El estado viaja **en la propia fila de la solicitud** (`radicado_status`, que
resuelve `get_solicitud` con un LEFT JOIN): quien decide tiene delante lo que lo
impide, en vez de depender de acordarse de consultarlo aparte.
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
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _sol(radicado_status: str | None) -> dict:
    return {
        "id": SOL_ID,
        "objeto_tipo": "radicado",
        "objeto_id": "2026-DEMO-S-000057",
        "titulo": "Respuesta a solicitud",
        "payload_sha256": None,
        "estado": "pendiente",
        "creado_por": USER,
        "nivel_seguridad": 1,
        "created_at": NOW,
        "updated_at": NOW,
        "radicado_status": radicado_status,
    }


def _turno(rol: str = "firma") -> dict:
    return {
        "id": TURNO_ID,
        "solicitud_id": SOL_ID,
        "orden": 1,
        "firmante_id": USER,
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


async def _post(ruta: str, json_body: dict | None = None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.post(ruta, headers=TENANT_HEADERS, json=json_body)


# ---------------------------------------------------------------------------
# Firmar un turno
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_se_firma_un_turno_de_un_radicado_anulado() -> None:
    conn = _make_conn()
    conn.fetchrow.side_effect = [_sol("anulado"), {}, _turno()]
    conn.fetchval.side_effect = [True, 1]

    _override(conn)
    try:
        resp = await _post(f"/api/v1/signature/requests/{SOL_ID}/sign")
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "radicado_anulado"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_tampoco_se_da_visto_bueno() -> None:
    """El gemelo. Van juntos o dentro de un mes uno de los dos vuelve a abrirse."""
    conn = _make_conn()
    conn.fetchrow.side_effect = [_sol("anulado"), {}]
    conn.fetchval.side_effect = [True, 1]

    _override(conn)
    try:
        resp = await _post(f"/api/v1/signature/requests/{SOL_ID}/vobo", {})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "radicado_anulado"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_un_radicado_vigente_pasa_el_control() -> None:
    """Control positivo: sin él, un gate que negara siempre dejaría los tests de
    arriba en verde y la firma de cadenas inalcanzable para todo el sistema."""
    conn = _make_conn()
    conn.fetchrow.side_effect = [_sol("registered"), {}, _turno()] + [None] * 6
    conn.fetchval.side_effect = [True, 1] + [None] * 6

    _override(conn)
    try:
        resp = await _post(f"/api/v1/signature/requests/{SOL_ID}/sign")
        # Pasa el control de anulación y llega al segundo factor, que es el
        # siguiente gate del camino. Lo que importa es que NO es 409 anulado.
        assert resp.json().get("detail", {}).get("error") != "radicado_anulado"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sobre_lo_que_no_es_un_radicado_el_control_no_aplica() -> None:
    """`radicado_status` nulo —un expediente, un borrador, o un schema sin la
    tabla— significa «aquí no hay nada que impedir», nunca «está bien»."""
    conn = _make_conn()
    conn.fetchrow.side_effect = [_sol(None), {}, _turno()] + [None] * 6
    conn.fetchval.side_effect = [True, 1] + [None] * 6

    _override(conn)
    try:
        resp = await _post(f"/api/v1/signature/requests/{SOL_ID}/sign")
        assert resp.json().get("detail", {}).get("error") != "radicado_anulado"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_a_quien_no_alcanza_el_objeto_no_se_le_dice_que_esta_anulado() -> None:
    """Primero quién eres, después el estado del documento.

    «El radicado está anulado» le confirma la existencia del objeto a quien no
    lo alcanza. El no-read-up contesta antes, con su 404 neutro — la misma
    corrección que se hizo con el gate de vigencia.
    """
    conn = _make_conn()
    sol = _sol("anulado")
    sol["nivel_seguridad"] = 3
    conn.fetchrow.side_effect = [sol, {}]  # clearance resuelve a 1 < 3
    conn.fetchval.side_effect = [True]

    _override(conn)
    try:
        resp = await _post(f"/api/v1/signature/requests/{SOL_ID}/sign")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "solicitud_not_found"
    finally:
        _clear()


# ---------------------------------------------------------------------------
# Abrir la cadena
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_se_abre_una_cadena_sobre_un_radicado_anulado() -> None:
    """Negarlo al final, turno por turno, sería hacerle gastar el trabajo a quien
    ya no puede hacer nada con él."""
    conn = _make_conn()
    # [permiso, resolver_nivel(1), status del radicado]. El tercero es el ESTADO,
    # no un booleano: `objeto_anulado` compara la cadena que trae la columna, y
    # mockear `True` aquí dejaba el test en verde sin ejercitar nada —el mismo
    # error de valor que dejó inerte la guarda del frontend—.
    conn.fetchval.side_effect = [True, 1, "anulado"] + [None] * 4
    conn.fetchrow.side_effect = [{}] + [None] * 4  # clearance 1 ≥ nivel 1

    _override(conn)
    try:
        resp = await _post(
            "/api/v1/signature/requests",
            {
                "objeto_tipo": "radicado",
                "objeto_id": "2026-DEMO-S-000057",
                "firmantes": [str(USER)],
            },
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "radicado_anulado"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_al_abrir_tampoco_se_dice_anulado_a_quien_no_alcanza_el_objeto() -> None:
    """El mismo orden que al firmar, y esta vez lo dijo una mutación.

    Mover el control delante del no-read-up NO ponía en rojo ningún test: el de
    arriba cubre el camino de firmar y este camino se había quedado sin nadie
    que lo mirara. Un gate correcto sin test que fije su POSICIÓN dura hasta la
    próxima refactorización.
    """
    conn = _make_conn()
    # [permiso, resolver_nivel(3)] — y el estado del radicado NO debe llegar a
    # consultarse: quien no alcanza el objeto recibe el 404 antes.
    conn.fetchval.side_effect = [True, 3, "anulado"] + [None] * 4
    conn.fetchrow.side_effect = [{}] + [None] * 4  # clearance 1 < nivel 3

    _override(conn)
    try:
        resp = await _post(
            "/api/v1/signature/requests",
            {
                "objeto_tipo": "radicado",
                "objeto_id": "2026-DEMO-S-000057",
                "firmantes": [str(USER)],
            },
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "solicitud_not_found"
    finally:
        _clear()
