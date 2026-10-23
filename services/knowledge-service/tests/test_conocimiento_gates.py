# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""ADR-037: las rutas de consulta exigen la bandera de la entidad Y PERM_CONOCIMIENTO.

Corren con los gates REALES (``gates_reales``) contra un ``conn`` simulado cuyo
``fetchval`` responde según la SQL: la bandera de ``public.tenants`` y el permiso del
usuario. Cada rama negativa tiene su control positivo (200) en la misma tabla.
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import TENANT_HEADERS

pytestmark = [pytest.mark.gates_reales, pytest.mark.asyncio]

SID = uuid.uuid4()
RUTAS = [
    ("/api/v1/knowledge/search", {"query": "contratos"}),
    ("/api/v1/knowledge/antecedentes", {"query": "contratos"}),
    ("/api/v1/knowledge/rag", {"query": "contratos"}),
]
ACEPTAR = (f"/api/v1/knowledge/suggestions/{SID}/aceptar", None)


def _fetchval(*, bandera: bool | Exception, crud: int):
    """``fetchval`` que distingue la lectura de la bandera de la del permiso."""

    async def _f(sql: str, *params: object) -> object:
        if "asistente_habilitado" in sql:
            if isinstance(bandera, Exception):
                raise bandera
            return bandera
        if "auth_group_permissions" in sql:  # check_permission(conn, user, perm, min_crud)
            _user, perm, min_crud = params
            assert perm == "PERM_CONOCIMIENTO"
            return crud >= min_crud
        return crud  # nivel_efectivo (mensaje del 403)

    return _f


async def _post(client, conn, ruta, *, bandera=True, crud=1, headers=None):
    conn.fetchval.side_effect = _fetchval(bandera=bandera, crud=crud)
    url, cuerpo = ruta
    return await client.post(url, json=cuerpo, headers=headers or TENANT_HEADERS)


@pytest.mark.parametrize("ruta", RUTAS, ids=[r[0].rsplit("/", 1)[1] for r in RUTAS])
async def test_con_bandera_y_permiso_la_consulta_pasa(client_with_conn, ruta) -> None:
    """Control positivo: bandera encendida + nivel 1 = 200."""
    client, conn = client_with_conn
    resp = await _post(client, conn, ruta, bandera=True, crud=1)
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("ruta", RUTAS, ids=[r[0].rsplit("/", 1)[1] for r in RUTAS])
async def test_sin_permiso_403_aunque_la_bandera_este_encendida(client_with_conn, ruta) -> None:
    client, conn = client_with_conn
    resp = await _post(client, conn, ruta, bandera=True, crud=0)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.parametrize("ruta", RUTAS + [ACEPTAR], ids=["search", "antecedentes", "rag", "aceptar"])
async def test_bandera_apagada_403_aunque_tenga_el_permiso(client_with_conn, ruta) -> None:
    client, conn = client_with_conn
    resp = await _post(client, conn, ruta, bandera=False, crud=5)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "asistente_deshabilitado"


async def test_bandera_apagada_tampoco_la_salva_ser_root(client_with_conn) -> None:
    """ROOT pasa `check_permission` por bypass; la bandera igual lo frena (va primero)."""
    client, conn = client_with_conn

    async def _f(sql: str, *params: object) -> object:
        return False if "asistente_habilitado" in sql else True  # permiso: siempre sí

    conn.fetchval.side_effect = _f
    resp = await client.post(RUTAS[0][0], json=RUTAS[0][1], headers=TENANT_HEADERS)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "asistente_deshabilitado"


@pytest.mark.parametrize("ruta", RUTAS, ids=[r[0].rsplit("/", 1)[1] for r in RUTAS])
async def test_bandera_ilegible_503_fail_closed(client_with_conn, ruta) -> None:
    client, conn = client_with_conn
    resp = await _post(client, conn, ruta, bandera=RuntimeError("registro caído"), crud=5)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "asistente_estado_no_disponible"


@pytest.mark.parametrize("ruta", RUTAS + [ACEPTAR], ids=["search", "antecedentes", "rag", "aceptar"])
async def test_sin_identidad_401(client_with_conn, ruta) -> None:
    client, conn = client_with_conn
    sin_usuario = {k: v for k, v in TENANT_HEADERS.items() if k != "X-User-Id"}
    resp = await _post(client, conn, ruta, headers=sin_usuario)
    assert resp.status_code == 401


async def test_aceptar_pide_nivel_2(client_with_conn) -> None:
    """Nivel 1 consulta pero NO acepta; nivel 2 pasa el gate (404 neutro: no hay sugerencia)."""
    client, conn = client_with_conn
    nivel1 = await _post(client, conn, ACEPTAR, crud=1)
    assert nivel1.status_code == 403
    nivel2 = await _post(client, conn, ACEPTAR, crud=2)
    assert nivel2.status_code == 404
    assert nivel2.json()["detail"]["error"] == "suggestion_not_found"


async def test_con_la_bandera_apagada_la_ingesta_sigue_indexando(
    client_with_conn, token_settings
) -> None:
    """ADR-037: apagar el asistente cierra la CONSULTA, no la ingesta (embeddings locales,
    sin egreso). Control positivo: `/ingest` da 201 con la bandera apagada y, en la misma
    prueba, las rutas de consulta dan 403."""
    from datetime import UTC, datetime

    from tests.conftest import TEST_INTERNAL_TOKEN

    client, conn = client_with_conn
    conn.fetchval.side_effect = _fetchval(bandera=False, crud=5)
    conn.fetchrow.return_value = {
        "id": uuid.uuid4(),
        "source_type": "radicado",
        "source_ref": "R-1",
        "created_at": datetime.now(UTC),
    }
    cuerpo = {"source_type": "radicado", "source_ref": "R-1", "texto": "hola", "nivel_seguridad": 1}
    cab = {"X-Tenant-Slug": "demo", "X-Internal-Token": TEST_INTERNAL_TOKEN}

    ingesta = await client.post("/api/v1/knowledge/ingest", json=cuerpo, headers=cab)
    assert ingesta.status_code == 201, ingesta.text
    assert any("knowledge_chunk" in str(c.args[0]) for c in conn.fetchrow.call_args_list)

    for url, body in [*RUTAS, ACEPTAR]:
        resp = await client.post(url, json=body, headers=TENANT_HEADERS)
        assert resp.status_code == 403, url
        assert resp.json()["detail"]["error"] == "asistente_deshabilitado"


async def test_con_la_bandera_apagada_el_consumidor_de_created_indexa_igual() -> None:
    """El consumidor de `document.radicado.created` ni siquiera lee la bandera."""
    from unittest.mock import AsyncMock, patch

    from orpycamcp_common import events

    from app.core import clearance
    from app.workers import document_consumer
    from tests.test_document_consumer import _AcquireTenantCtx

    fields = events.to_fields(
        events.build_event(
            "document.radicado.created",
            tenant_slug="demo",
            payload={"tracking_number": "2026-DEMO-E-000001", "nivel_seguridad": 1},
        )
    )
    bandera = AsyncMock(return_value=False)
    with (
        patch.object(clearance, "asistente_habilitado", bandera),
        patch.object(document_consumer, "acquire_tenant", return_value=_AcquireTenantCtx(AsyncMock())),
        patch("app.workers.document_consumer.ingestion.ingest_radicado_created", AsyncMock()) as ingesta,
    ):
        await document_consumer._handle(fields)

    ingesta.assert_awaited_once()
    bandera.assert_not_awaited()
