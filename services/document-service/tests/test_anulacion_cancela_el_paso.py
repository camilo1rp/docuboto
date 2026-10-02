# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""ADR-028: aprobar la anulación cancela el paso activo, en la MISMA transacción.

Carril mockeado (el cableado y las ramas); la atomicidad y el efecto real sobre
`flow_steps`/`flow_events` los prueba `tests/integration/`.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from app.repositories.anulacion_repository import AnulacionRepository

RID, AID, DECIDE = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _repo(*, pasos: list[dict] | None = None, fetchrow=None) -> tuple[AnulacionRepository, AsyncMock]:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.fetchrow.return_value = {"id": AID} if fetchrow is None else fetchrow
    conn.fetch.return_value = pasos or []
    return AnulacionRepository(conn), conn


async def _decide(repo: AnulacionRepository, *, aprobar: bool) -> None:
    await repo.decide(
        anulacion_id=AID, radicado_id=RID, aprobar=aprobar, decidido_por=DECIDE, observacion="ok"
    )


@pytest.mark.asyncio
async def test_aprobar_cancela_los_pasos_activos_y_deja_un_evento_por_paso() -> None:
    paso = {"id": uuid.uuid4(), "tracking_number": "2026-DEMO-E-000001", "step_number": 2, "to_dept": "Jurídica"}
    repo, conn = _repo(pasos=[paso])

    await _decide(repo, aprobar=True)

    (upd,) = conn.fetch.await_args_list
    assert "UPDATE flow_steps SET status = 'cancelled'" in upd.args[0]
    assert "status IN ('pending', 'in_progress')" in upd.args[0]
    assert upd.args[1] == RID
    evento = next(c for c in conn.execute.await_args_list if "INSERT INTO flow_events" in c.args[0])
    assert "'anulacion'" in evento.args[0]
    assert evento.args[4] == DECIDE  # el actor es quien aprobó
    detalles = json.loads(evento.args[6])
    assert detalles == {"step_id": str(paso["id"]), "step_number": 2, "anulacion_id": str(AID)}


@pytest.mark.asyncio
async def test_rechazar_no_toca_el_radicado_ni_los_pasos() -> None:
    repo, conn = _repo()
    await _decide(repo, aprobar=False)
    conn.fetch.assert_not_awaited()
    sql = [c.args[0] for c in conn.execute.await_args_list]
    assert not any("UPDATE radicados" in s or "flow_" in s for s in sql)


@pytest.mark.asyncio
async def test_sin_tablas_de_flujo_la_anulacion_no_se_aborta() -> None:
    """El workflow-service migra aparte: sin `flow_steps` no hay paso que cancelar."""
    repo, conn = _repo()
    conn.fetch.side_effect = asyncpg.UndefinedTableError("flow_steps")
    await _decide(repo, aprobar=True)  # no lanza
    assert any("UPDATE radicados SET status='anulado'" in c.args[0] for c in conn.execute.await_args_list)


@pytest.mark.asyncio
async def test_si_la_decision_no_ocurrio_no_se_toca_nada() -> None:
    """Sin fila (otro la resolvió antes) el repositorio devuelve `None` —antes un
    `assert` lo convertía en un 500— y NO anula el radicado ni cancela pasos por una
    decisión que no fue; el servicio responde 409 `anulacion_ya_decidida`."""
    repo, conn = _repo()
    conn.fetchrow.return_value = None
    resultado = await repo.decide(
        anulacion_id=AID, radicado_id=RID, aprobar=True, decidido_por=DECIDE, observacion="ok"
    )
    assert resultado is None
    conn.fetch.assert_not_awaited()
    assert not any("UPDATE radicados" in c.args[0] for c in conn.execute.await_args_list)


@pytest.mark.asyncio
async def test_la_decision_concurrente_da_409_anulacion_ya_decidida_y_no_500() -> None:
    """[G] La carrera de dos decisiones: `decide` devuelve `None` y el SERVICIO la
    traduce a 409 `anulacion_ya_decidida` (sin asiento de auditoría que afirme una
    decisión que no se aplicó)."""
    from fastapi import HTTPException

    from app.schemas.anulacion import AnulacionDecision
    from app.services.anulacion_service import AnulacionService

    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    svc = AnulacionService(conn, tenant_slug="demo")
    svc._repo = AsyncMock()
    svc._repo.get_active.return_value = {"id": AID, "solicitado_por": uuid.uuid4()}
    svc._repo.decide.return_value = None  # otro la decidió entre la lectura y el UPDATE

    with pytest.raises(HTTPException) as exc:
        await svc.aprobar(RID, AnulacionDecision(observacion="ok"), DECIDE, 3)

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "anulacion_ya_decidida"
    svc._repo.get_tracking_number.assert_not_awaited()  # cortó ANTES del asiento


@pytest.mark.asyncio
async def test_la_decision_se_condiciona_a_un_estado_que_el_check_de_la_tabla_admite() -> None:
    """Regresión real: el `UPDATE` pedía `estado = 'pendiente'`, un valor que el CHECK
    de `radicado_anulacion` NUNCA admite (`solicitada|aprobada|rechazada`), así que
    en una base real NINGUNA aprobación podía aplicarse (siempre 409
    `anulacion_ya_decidida`). Los mocks no lo veían: devolvían la fila igual."""
    repo, conn = _repo()
    await _decide(repo, aprobar=True)
    sql = conn.fetchrow.await_args_list[0].args[0]
    assert "estado = 'solicitada'" in sql and "'pendiente'" not in sql
