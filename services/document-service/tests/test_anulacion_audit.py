# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Auditoría (ADR-008/010) del ciclo de anulación de radicados.

Hasta este incremento, `AnulacionService` no dejaba NINGÚN asiento en
`public.audit_log`: anular un radicado (el acto más consecuente de un SGDEA —
retira de la circulación un documento oficial con número inmutable) era
invisible para la traza legal forense. Cubre:

  (a) `solicitar` deja un asiento `document.anulacion_solicitada` con el
      actor, la causal/motivo y `object_ref=tracking_number` (resuelto
      server-side, nunca del body).
  (b) `aprobar` deja `document.anulacion_aprobada`.
  (c) `rechazar` deja `document.anulacion_rechazada` — acción DISTINTA de
      aprobar, aunque ambas pasen por `_decidir`.
  (d) atomicidad: si `audit.append` falla, la mutación completa (INSERT de la
      solicitud, o el UPDATE que aprueba/rechaza) NO se persiste — a
      diferencia de las lecturas (`test_read_audit.py`), esta auditoría NO es
      best-effort.

Espejo de `test_read_audit.py` (mismo patrón de inspección del SQL crudo
ejecutado contra la conexión mockeada) y de `test_anulacion.py` (mismos
fixtures/side_effects de `fetchrow`).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.conftest import TENANT_HEADERS

NOW = datetime.now(tz=UTC)
_USER = str(uuid.uuid4())
# Quien RESUELVE es siempre otra persona: solicitar y aprobar una anulación no
# pueden ser el mismo acto (segregación de funciones, ver `test_anulacion.py`).
# Estos tests aprobaban con `_USER` —el mismo del fixture como solicitante—, así
# que auditaban un acto que el sistema no debe aceptar.
_SUPERVISOR = str(uuid.uuid4())


def _clearance_row(max_level: int = 3, is_root: bool = False) -> dict:
    return {"is_root": is_root, "max_level": max_level}


def _anulacion_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "radicado_id": uuid.uuid4(),
        "estado": "solicitada",
        "causal": "ANU-ERR",
        "motivo": "Error de radicación",
        "solicitado_por": uuid.UUID(_USER),
        "solicitado_at": NOW,
        "decidido_por": None,
        "decidido_at": None,
        "observacion": None,
    }
    base.update(kw)
    return base


def _no_raise_client() -> AsyncClient:
    """Cliente que NO relanza la excepción de servidor al llamador (a
    diferencia de la fixture `client_with_conn`, que usa
    `raise_app_exceptions=True` por defecto de `ASGITransport`).

    Starlette's `ServerErrorMiddleware` siempre relanza la excepción original
    tras despachar la respuesta 500 del `@app.exception_handler(Exception)` —
    con `raise_app_exceptions=True` (el default), `httpx` propaga esa
    excepción al test en vez de devolver la respuesta 500 ya generada. Estos
    tests de atomicidad quieren justamente inspeccionar esa respuesta 500, así
    que reutilizan los MISMOS `app.dependency_overrides` que `client_with_conn`
    ya dejó activos, pero con un transport que no relanza.
    """
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


def _audit_insert_calls(mock_conn: AsyncMock) -> list:
    return [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]


# ---------------------------------------------------------------------------
# (a) solicitar → document.anulacion_solicitada
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_solicitar_audita_con_actor_causal_y_tracking(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    tracking = "2026-DEMO-E-000042"
    # `fetchval` sirve al gate RBAC (truthy) y a `get_tracking_number`. El estado
    # del radicado ya NO sale de ahí: se lee con `fetchrow` junto a su TIPO,
    # porque una Entrada no se anula (`app/core/reglas_tipo.py`) y esa
    # comprobación necesita el tipo. Se declara "S": el caso de la Entrada tiene
    # su propio test.
    mock_conn.fetchval.return_value = tracking
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {"status": "registered", "doc_type": "S"},
        _anulacion_row(radicado_id=rid),
    ]

    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion",
        json={"causal": "ANU-ERR", "motivo": "Error de radicación"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 201
    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1
    args = inserts[0].args
    # _INSERT_SQL params: (sql, tenant, service, actor, canal, action,
    #                       object_type, object_ref, payload_json, prev_hash, hash)
    assert args[1] == TENANT_HEADERS["X-Tenant-Slug"]
    assert args[2] == "document-service"
    assert args[3] == _USER
    assert args[5] == "document.anulacion_solicitada"
    assert args[6] == "radicado"
    assert args[7] == tracking  # object_ref = tracking_number, no el body
    payload = json.loads(args[8])
    assert payload["id"] == str(rid)
    assert payload["causal"] == "ANU-ERR"
    assert payload["motivo"] == "Error de radicación"


# ---------------------------------------------------------------------------
# (b)/(c) aprobar/rechazar → acciones DISTINTAS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aprobar_audita_con_accion_propia(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    tracking = "2026-DEMO-E-000043"
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),  # resolve_caller_clearance
        _anulacion_row(radicado_id=rid, estado="solicitada"),  # get_active
        _anulacion_row(
            radicado_id=rid, estado="aprobada", decidido_por=uuid.UUID(_SUPERVISOR), decidido_at=NOW
        ),  # decide
    ]
    mock_conn.fetchval.return_value = tracking  # get_tracking_number

    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion/aprobar",
        json={"observacion": "Procede"},
        headers={**TENANT_HEADERS, "X-User-Id": _SUPERVISOR},
    )

    assert resp.status_code == 200
    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1
    args = inserts[0].args
    assert args[3] == _SUPERVISOR
    assert args[5] == "document.anulacion_aprobada"
    assert args[6] == "radicado"
    assert args[7] == tracking
    payload = json.loads(args[8])
    assert payload["id"] == str(rid)
    assert payload["observacion"] == "Procede"


@pytest.mark.asyncio
async def test_rechazar_audita_con_accion_distinta_de_aprobar(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    tracking = "2026-DEMO-E-000044"
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),  # resolve_caller_clearance
        _anulacion_row(radicado_id=rid, estado="solicitada"),  # get_active
        _anulacion_row(
            radicado_id=rid, estado="rechazada", decidido_por=uuid.UUID(_USER), decidido_at=NOW
        ),  # decide
    ]
    mock_conn.fetchval.return_value = tracking  # get_tracking_number

    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion/rechazar",
        json={"observacion": "No procede"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 200
    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1
    args = inserts[0].args
    assert args[5] == "document.anulacion_rechazada"
    assert args[7] == tracking
    payload = json.loads(args[8])
    assert payload["observacion"] == "No procede"


# ---------------------------------------------------------------------------
# (d) Atomicidad: `audit.append` NO es best-effort en anulación — si falla,
# la mutación entera revierte (a diferencia de las LECTURAS, ver
# test_read_audit.py::test_get_document_read_audit_best_effort_survives_failure).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_solicitar_no_sobrevive_a_fallo_de_auditoria(client_with_conn) -> None:
    _client, mock_conn = client_with_conn  # activa los dependency_overrides
    rid = uuid.uuid4()
    mock_conn.fetchval.return_value = "2026-DEMO-E-000050"
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {"status": "registered", "doc_type": "S"},
        _anulacion_row(radicado_id=rid),
    ]

    with patch(
        "app.services.anulacion_service.audit.append",
        new=AsyncMock(side_effect=RuntimeError("audit_log down")),
    ):
        async with _no_raise_client() as client:
            resp = await client.post(
                f"/api/v1/documents/{rid}/anulacion",
                json={"causal": "ANU-ERR", "motivo": "Error de radicación"},
                headers={**TENANT_HEADERS, "X-User-Id": _USER},
            )

    # Una anulación aprobada/solicitada sin traza es peor que una fallida:
    # el fallo de auditoría debe degradar la respuesta a 500, no a 201.
    assert resp.status_code == 500
    # La transacción (`async with self._conn.transaction()`) debe haberse
    # abierto para envolver el INSERT + el intento de auditoría fallido.
    assert mock_conn.transaction.called


@pytest.mark.asyncio
async def test_aprobar_no_sobrevive_a_fallo_de_auditoria(client_with_conn) -> None:
    _client, mock_conn = client_with_conn  # activa los dependency_overrides
    rid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),  # resolve_caller_clearance
        _anulacion_row(radicado_id=rid, estado="solicitada"),  # get_active
        _anulacion_row(
            radicado_id=rid, estado="aprobada", decidido_por=uuid.UUID(_SUPERVISOR), decidido_at=NOW
        ),  # decide
    ]
    mock_conn.fetchval.return_value = "2026-DEMO-E-000051"

    with patch(
        "app.services.anulacion_service.audit.append",
        new=AsyncMock(side_effect=RuntimeError("audit_log down")),
    ):
        async with _no_raise_client() as client:
            resp = await client.post(
                f"/api/v1/documents/{rid}/anulacion/aprobar",
                json={"observacion": "Procede"},
                headers={**TENANT_HEADERS, "X-User-Id": _SUPERVISOR},
            )

    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_rechazar_no_sobrevive_a_fallo_de_auditoria(client_with_conn) -> None:
    _client, mock_conn = client_with_conn  # activa los dependency_overrides
    rid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),  # resolve_caller_clearance
        _anulacion_row(radicado_id=rid, estado="solicitada"),  # get_active
        _anulacion_row(
            radicado_id=rid, estado="rechazada", decidido_por=uuid.UUID(_USER), decidido_at=NOW
        ),  # decide
    ]
    mock_conn.fetchval.return_value = "2026-DEMO-E-000052"

    with patch(
        "app.services.anulacion_service.audit.append",
        new=AsyncMock(side_effect=RuntimeError("audit_log down")),
    ):
        async with _no_raise_client() as client:
            resp = await client.post(
                f"/api/v1/documents/{rid}/anulacion/rechazar",
                json={"observacion": "No procede"},
                headers={**TENANT_HEADERS, "X-User-Id": _USER},
            )

    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_escritura_sin_tenant_slug_falla_en_vez_de_auditar_al_sistema() -> None:
    """Sin `tenant_slug`, la escritura ABORTA — no se archiva bajo SYSTEM_TENANT.

    `audit.append` resuelve `tenant_slug or SYSTEM_TENANT`, así que un llamante
    que omitiera el slug NO perdería la fila: la archivaría bajo el tenant del
    sistema, fuera de la vista de auditoría de la entidad y de su cadena de
    hash — invisible justo donde un auditor la buscaría. Preferimos que la
    anulación falle a que su traza acabe en el sitio equivocado.

    Los tres endpoints de escritura pasan el slug hoy; esto fija el invariante
    para el próximo llamante.
    """
    from app.services.anulacion_service import AnulacionService

    service = AnulacionService(AsyncMock())  # sin tenant_slug (el default)

    with pytest.raises(RuntimeError, match="tenant_slug"):
        service._audit_tenant()


@pytest.mark.asyncio
async def test_decidir_dos_veces_no_escribe_un_asiento_falso(client_with_conn):
    """Si otra persona resolvió la solicitud entremedias, la decisión NO ocurre.

    Antes se comprobaba el estado en una consulta y se aplicaba en otra, así que
    dos aprobaciones simultáneas se aplicaban las dos y la última reescribía
    quién decidió y cuándo. Ahora la condición vive DENTRO del UPDATE, y si no
    casa ninguna fila se corta ANTES del asiento: un asiento afirmando una
    decisión que no se aplicó es peor que no tener ninguno, porque dice quién
    decidió qué y es falso.
    """
    client, mock_conn = client_with_conn
    aid = uuid.uuid4()
    rid = uuid.uuid4()

    asientos = []

    async def _fetchrow(sql, *args, **kw):
        bajo = sql.lower()
        if "update radicado_anulacion" in bajo:
            return None  # otra persona la resolvió: no casa ninguna fila
        if "select" in bajo and "radicado_anulacion" in bajo:
            return {"id": aid, "radicado_id": rid, "estado": "pendiente", "solicitado_por": None}
        return _clearance_row()

    mock_conn.fetchrow.side_effect = _fetchrow

    with patch(
        "orpycamcp_common.audit.append",
        AsyncMock(side_effect=lambda *a, **k: asientos.append(k.get("action"))),
    ):
        resp = await client.post(
            f"/api/v1/documents/{rid}/anulacion/decidir",
            json={"aprobar": True},
            headers={**TENANT_HEADERS, "X-User-Id": _USER},
        )

    assert resp.status_code in (409, 403, 404), resp.status_code
    assert not any(a and "anulacion" in a for a in asientos), (
        "se escribió un asiento de una decisión que no se aplicó"
    )
