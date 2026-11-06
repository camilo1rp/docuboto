# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Corregir el vencimiento: asiento, tope y conflicto del asignado."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from document.services.document_service import DocumentService, PlazoError

_RAIZ = Path(__file__).resolve().parents[1]
ACTOR = uuid.uuid4()
RID = uuid.uuid4()
MOTIVO = "El peticionario pidió un plazo más corto."


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _fila(due, termino="igual", registrado=None):
    if termino == "igual":
        termino = due
    return {
        "id": RID,
        "tracking_number": "2026-DEMO-E-000001",
        "due_date": due,
        "termino_inicial": termino,
        "registered_at": registrado or datetime(2026, 1, 1, 15, tzinfo=UTC),
    }


def _servicio(filas, asignado=None, cliente=None):
    conn = AsyncMock()
    conn.transaction = lambda: _Tx()
    conn.fetchrow = AsyncMock(side_effect=filas)
    conn.fetchval = AsyncMock(return_value=asignado)
    return DocumentService(conn, "demo", cliente), conn


def _parches():
    return (
        patch(
            "document.services.document_service.resolve_caller_clearance",
            AsyncMock(return_value=3),
        ),
        patch(
            "document.services.document_service.audit.append",
            AsyncMock(return_value="a" * 32),
        ),
        patch(
            "document.services.document_service.outbox.encolar",
            AsyncMock(return_value={"stream": "s", "event": {}}),
        ),
        patch(
            "document.services.document_service.outbox.enviar",
            AsyncMock(return_value=True),
        ),
    )


@pytest.mark.asyncio
async def test_acortar_deja_asiento_y_evento():
    svc, conn = _servicio([_fila(date(2026, 2, 1)), {"due_date": date(2026, 1, 20)}])
    p_clear, p_audit, p_out, p_env = _parches()
    with p_clear, p_audit as asiento, p_out as encolar, p_env:
        out = await svc.fijar_vencimiento(
            RID, motivo=MOTIVO, actor=ACTOR, due_date=date(2026, 1, 20)
        )
    assert out["due_date"] == date(2026, 1, 20)
    payload = asiento.await_args.kwargs["payload"]
    assert payload["antes"] == "2026-02-01"
    assert payload["despues"] == "2026-01-20"
    assert payload["motivo"] == MOTIVO
    assert payload["origen"] == "manual"
    assert encolar.await_args.kwargs["event_type"] == "document.radicado.due_date_changed"
    assert "FOR UPDATE" in conn.fetchrow.await_args_list[0].args[0]
    assert "nivel_seguridad" in conn.fetchrow.await_args_list[0].args[0]
    update = conn.fetchrow.await_args_list[1].args[0]
    assert "COALESCE(termino_inicial, due_date, $2)" in update
    conn.fetchval.assert_not_awaited()


@pytest.mark.asyncio
async def test_la_misma_fecha_no_escribe():
    svc, conn = _servicio([_fila(date(2026, 2, 1))])
    p_clear, p_audit, p_out, p_env = _parches()
    with p_clear, p_audit as asiento, p_out as encolar, p_env:
        out = await svc.fijar_vencimiento(
            RID, motivo=MOTIVO, actor=ACTOR, due_date=date(2026, 2, 1)
        )
    assert out["due_date"] == date(2026, 2, 1)
    asiento.assert_not_awaited()
    encolar.assert_not_awaited()
    assert conn.fetchrow.await_count == 1


@pytest.mark.asyncio
async def test_el_asignado_no_amplia():
    svc, _conn = _servicio(
        [_fila(date(2026, 2, 1))],
        asignado=1,
    )
    p_clear, p_audit, p_out, p_env = _parches()
    with p_clear, p_audit as asiento, p_out, p_env, pytest.raises(PlazoError) as exc:
        await svc.fijar_vencimiento(
            RID, motivo=MOTIVO, actor=ACTOR, due_date=date(2026, 2, 10)
        )
    assert exc.value.codigo == "ampliacion_del_asignado"
    assert exc.value.status_code == 403
    asiento.assert_not_awaited()


@pytest.mark.asyncio
async def test_ampliar_pasado_el_doble_se_niega():
    # 1 ene → 11 ene son 10 días. El doble cae el 21 ene.
    svc, _conn = _servicio([_fila(date(2026, 1, 11))])
    p_clear, p_audit, p_out, p_env = _parches()
    with p_clear, p_audit, p_out, p_env, pytest.raises(PlazoError) as exc:
        await svc.fijar_vencimiento(
            RID, motivo=MOTIVO, actor=ACTOR, due_date=date(2026, 1, 22)
        )
    assert exc.value.codigo == "supera_el_doble"


@pytest.mark.asyncio
async def test_ampliar_hasta_el_doble_se_permite():
    svc, _conn = _servicio([_fila(date(2026, 1, 11)), {"due_date": date(2026, 1, 21)}])
    p_clear, p_audit, p_out, p_env = _parches()
    with p_clear, p_audit as asiento, p_out, p_env:
        out = await svc.fijar_vencimiento(
            RID, motivo=MOTIVO, actor=ACTOR, due_date=date(2026, 1, 21)
        )
    assert out["due_date"] == date(2026, 1, 21)
    asiento.assert_awaited()


@pytest.mark.asyncio
async def test_los_dias_se_cuentan_desde_la_radicacion():
    cliente = AsyncMock()
    cliente.calculate_due_date = AsyncMock(return_value=date(2026, 1, 20))
    svc, _conn = _servicio(
        [_fila(date(2026, 2, 1)), {"due_date": date(2026, 1, 20)}],
        cliente=cliente,
    )
    p_clear, p_audit, p_out, p_env = _parches()
    with p_clear, p_audit, p_out, p_env:
        await svc.fijar_vencimiento(RID, motivo=MOTIVO, actor=ACTOR, business_days=12)
    kwargs = cliente.calculate_due_date.await_args.kwargs
    assert kwargs["from_date"] == date(2026, 1, 1)
    assert kwargs["business_days"] == 12


@pytest.mark.asyncio
async def test_sin_calendario_no_inventa_la_fecha():
    svc, _conn = _servicio([_fila(date(2026, 2, 1))])
    p_clear, p_audit, p_out, p_env = _parches()
    with p_clear, p_audit, p_out, p_env, pytest.raises(PlazoError) as exc:
        await svc.fijar_vencimiento(RID, motivo=MOTIVO, actor=ACTOR, business_days=5)
    assert exc.value.status_code == 502


def test_el_alta_guarda_el_termino_inicial():
    repo = (_RAIZ / "document/repositories/document_repository.py").read_text()
    lote = (_RAIZ / "document/services/batch_service.py").read_text()
    assert "due_date, termino_inicial" in repo
    assert "due_date, termino_inicial" in lote


def test_la_ruta_no_acepta_origen():
    router = (_RAIZ / "document/routers/documents.py").read_text()
    assert 'require_permission("PERM_DUE_DATE", min_crud=3)' in router
    esquema = (_RAIZ / "app/schemas/document.py").read_text()
    bloque = esquema.split("class DueDateRequest", 1)[1].split("class DueDateResponse", 1)[0]
    assert "origen" not in bloque
