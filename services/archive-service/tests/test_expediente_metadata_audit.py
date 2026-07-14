# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Auditoría del PATCH de metadatos de expediente (`ArchiveService.update_expediente`).

Fija la decisión del arquitecto sobre el hallazgo "PATCH como ruta alterna a
close/transfer": `status` fue retirado de `ExpedienteUpdate` (ver
`test_archive.py::test_update_expediente_rechaza_status`); lo que queda del
PATCH — name/description/trd_serie_id — ahora deja asiento auditado, con
guarda especial para la reclasificación TRD (Ley 594/2000 art. 24), que altera
la retención/disposición proyectada del expediente.

DB completamente mockeada — sin infraestructura real. Secuencia de mocks del
PATCH exitoso con cambios efectivos (ver también el docstring de
`ArchiveRepository.update_expediente`):

  fetchrow — resolve_caller_clearance (caller_clearance)
  fetchrow — SELECT expediente (get_expediente_by_id, pre-lectura no-read-up)
  fetch    — SELECT radicados (get_expediente_by_id)
  fetchrow — WITH old AS (...FOR UPDATE) UPDATE expedientes ... RETURNING e.*, old_*
  [si reclasifica: fetchrow x2 — get_trd_serie_by_id (serie antes/después)]
  execute  — INSERT expediente_eventos (accion='actualizado'|'reclasificado')
  execute  — pg_advisory_xact_lock (audit.append)
  fetchval — SELECT hash (prev_hash de audit_log)
  execute  — INSERT audit_log (action='archive.expediente_updated'|'...reclasificado')
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

import app.services.archive_service as archive_service_module
from app.schemas.archive import ExpedienteUpdate
from tests.conftest import (
    EXPEDIENTE_ID,
    SERIE_ID,
    TENANT_HEADERS,
    TENANT_SLUG,
    USER_ID,
    make_expediente_record,
    make_expediente_update_row,
    make_trd_serie_record,
)


def _audit_log_calls(mock_conn: AsyncMock) -> list[tuple]:
    return [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]


def _evento_calls(mock_conn: AsyncMock) -> list[tuple]:
    return [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO expediente_eventos" in c.args[0]
    ]


@pytest.mark.asyncio
async def test_patch_name_emite_expediente_updated(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    existing_record = make_expediente_record(exp_id=EXPEDIENTE_ID, name="Viejo nombre")
    updated_record = make_expediente_record(exp_id=EXPEDIENTE_ID, name="Nuevo nombre")
    update_row = make_expediente_update_row(old=existing_record, new=updated_record)

    mock_conn.fetchval.return_value = True  # D-08 gate + audit.append prev_hash
    mock_conn.fetchrow.side_effect = [None, existing_record, update_row]
    mock_conn.fetch.return_value = []

    response = await client.patch(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}",
        json={"name": "Nuevo nombre"},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200

    audit_calls = _audit_log_calls(mock_conn)
    # args: (sql, tenant, service, actor, canal, action, object_type, object_ref, payload, prev_hash, hash)
    entry = next(a for a in audit_calls if a[5] == "archive.expediente_updated")
    assert entry[3] == str(USER_ID)
    assert entry[4] == "api"
    assert entry[6] == "expediente"
    assert entry[7] == existing_record["code"]
    payload = json.loads(entry[8])
    assert payload["changed"] == {"name": {"before": "Viejo nombre", "after": "Nuevo nombre"}}

    evento = next(a for a in _evento_calls(mock_conn) if a[3] == "actualizado")
    assert evento[4] == USER_ID  # actor


@pytest.mark.asyncio
async def test_patch_trd_serie_emite_expediente_reclasificado(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    new_serie_id = uuid.uuid4()
    existing_record = make_expediente_record(
        exp_id=EXPEDIENTE_ID, trd_serie_id=SERIE_ID, exp_status="open"
    )
    updated_record = make_expediente_record(
        exp_id=EXPEDIENTE_ID, trd_serie_id=new_serie_id, exp_status="open"
    )
    update_row = make_expediente_update_row(old=existing_record, new=updated_record)
    old_serie_record = make_trd_serie_record(
        serie_id=SERIE_ID, code="100-20", archivo_gestion_years=2, archivo_central_years=3
    )
    new_serie_record = make_trd_serie_record(
        serie_id=new_serie_id,
        code="100-35",
        archivo_gestion_years=5,
        archivo_central_years=10,
        disposition="eliminate",
    )

    mock_conn.fetchval.return_value = True
    # caller_clearance, get_expediente_by_id, repo.update_expediente,
    # compute_retention(antes)->get_trd_serie_by_id, compute_retention(después)
    mock_conn.fetchrow.side_effect = [
        None,
        existing_record,
        update_row,
        old_serie_record,
        new_serie_record,
    ]
    mock_conn.fetch.return_value = []

    response = await client.patch(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}",
        json={"trd_serie_id": str(new_serie_id)},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200

    audit_calls = _audit_log_calls(mock_conn)
    entry = next(a for a in audit_calls if a[5] == "archive.expediente_reclasificado")
    assert entry[7] == existing_record["code"]
    payload = json.loads(entry[8])
    assert payload["changed"] == {
        "trd_serie_id": {"before": str(SERIE_ID), "after": str(new_serie_id)}
    }
    assert payload["serie_codigo"] == {"before": "100-20", "after": "100-35"}
    # RF-FIR-15: la clave es "proyectada" — lo que aplicaría si se cerrara HOY,
    # NO una afirmación de que `expedientes.disposition` ya cambió en disco.
    assert payload["disposicion_proyectada"]["before"]["disposition_code"] == "CT"
    assert payload["disposicion_proyectada"]["after"]["disposition_code"] == "E"
    assert payload["expediente_status"] == "open"

    evento = next(a for a in _evento_calls(mock_conn) if a[3] == "reclasificado")
    assert evento[4] == USER_ID


@pytest.mark.asyncio
async def test_patch_trd_serie_en_expediente_cerrado_409(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Reclasificar altera la retención/disposición (Ley 594/2000 art. 24): solo
    se permite con el expediente `open` (`_assert_open`, mismo criterio que
    link/unlink). Sobre un expediente cerrado, la disposición ya está
    materializada — cambiar la serie contradiría en silencio lo ya persistido.
    El intento queda como asiento DENEGADO (`archive.expediente_update_denied`),
    y la excepción original (409 `expediente_not_open`) NO se enmascara."""
    client, mock_conn = client_with_conn
    new_serie_id = uuid.uuid4()
    existing_record = make_expediente_record(
        exp_id=EXPEDIENTE_ID, trd_serie_id=SERIE_ID, exp_status="closed"
    )

    mock_conn.fetchval.return_value = True
    # caller_clearance, get_expediente_by_id — la guarda dispara ANTES de
    # llegar a repo.update_expediente (no hay 3.er fetchrow).
    mock_conn.fetchrow.side_effect = [None, existing_record]
    mock_conn.fetch.return_value = []

    response = await client.patch(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}",
        json={"trd_serie_id": str(new_serie_id)},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "expediente_not_open"

    audit_calls = _audit_log_calls(mock_conn)
    denial = next(a for a in audit_calls if a[5] == "archive.expediente_update_denied")
    assert denial[7] == existing_record["code"]
    payload = json.loads(denial[8])
    assert payload["motivo"] == "expediente_not_open"
    assert payload["status_actual"] == "closed"
    # Ningún asiento de éxito se coló pese al intento.
    assert not any(a[5] == "archive.expediente_reclasificado" for a in audit_calls)


@pytest.mark.asyncio
async def test_patch_revierte_si_falla_el_asiento(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Atomicidad mutación<->asiento (gemelo de H1/H2): si `audit.append` falla,
    la excepción se propaga — `update_expediente` nunca devuelve un
    `ExpedienteResponse` de éxito con el asiento forense sin escribir. Invoca
    el servicio DIRECTAMENTE (mismo patrón que
    `test_create_expediente_late_failure_propagates_not_a_fake_success`)."""
    mock_conn = AsyncMock()
    mock_conn.fetchval.return_value = True
    mock_conn.execute.return_value = None
    mock_conn.fetch.return_value = []
    from unittest.mock import MagicMock

    mock_conn.transaction = MagicMock(return_value=AsyncMock())

    existing_record = make_expediente_record(exp_id=EXPEDIENTE_ID, name="Viejo nombre")
    updated_record = make_expediente_record(exp_id=EXPEDIENTE_ID, name="Nuevo nombre")
    update_row = make_expediente_update_row(old=existing_record, new=updated_record)
    mock_conn.fetchrow.side_effect = [None, existing_record, update_row]

    async def _boom(*args: object, **kwargs: object) -> str:
        raise RuntimeError("audit failure (simulated)")

    monkeypatch.setattr(archive_service_module.audit, "append", _boom)

    service = archive_service_module.ArchiveService(mock_conn, TENANT_SLUG)
    payload = ExpedienteUpdate(name="Nuevo nombre")

    with pytest.raises(RuntimeError, match="audit failure"):
        await service.update_expediente(EXPEDIENTE_ID, payload, USER_ID)

    # Llegó a intentar el UPDATE (y el evento) antes de que `audit.append`
    # reventara dentro de la MISMA transacción — el fallo es TARDÍO.
    assert _evento_calls(mock_conn) != []


@pytest.mark.asyncio
async def test_patch_sin_cambios_efectivos_no_emite_asiento(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Body vacío (o valores idénticos a los ya persistidos) -> 200, sin
    `expediente_eventos` ni `audit_log` — no se contamina la cadena con actos
    que no ocurrieron."""
    client, mock_conn = client_with_conn
    existing_record = make_expediente_record(exp_id=EXPEDIENTE_ID, name="Mismo nombre")
    # COALESCE(NULL, e.name) conserva el valor -> old == new.
    update_row = make_expediente_update_row(old=existing_record, new=existing_record)

    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [None, existing_record, update_row]
    mock_conn.fetch.return_value = []

    response = await client.patch(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}",
        json={},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Mismo nombre"
    assert _evento_calls(mock_conn) == []
    assert _audit_log_calls(mock_conn) == []


@pytest.mark.asyncio
async def test_patch_before_sale_del_update_no_de_la_prelectura(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Lección H3: el "before" auditado sale del `RETURNING` del propio UPDATE
    (bajo `FOR UPDATE`), NO de la pre-lectura no-read-up (`get_expediente_by_id`)
    hecha ANTES de la transacción — entre ambas hay una ventana TOCTOU. Se
    simula la carrera: la pre-lectura ve `name="A"` pero el UPDATE, ejecutado
    después (con el lock), ve `name="B"` (otra escritura ganó la carrera)."""
    client, mock_conn = client_with_conn
    existing_record = make_expediente_record(exp_id=EXPEDIENTE_ID, name="A")
    updated_record = make_expediente_record(exp_id=EXPEDIENTE_ID, name="Nuevo nombre")
    raced_old = {**existing_record, "name": "B"}
    update_row = make_expediente_update_row(old=raced_old, new=updated_record)

    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [None, existing_record, update_row]
    mock_conn.fetch.return_value = []

    response = await client.patch(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}",
        json={"name": "Nuevo nombre"},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200

    audit_calls = _audit_log_calls(mock_conn)
    entry = next(a for a in audit_calls if a[5] == "archive.expediente_updated")
    payload = json.loads(entry[8])
    assert payload["changed"]["name"]["before"] == "B"  # NO "A" (la pre-lectura)
