# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests del gate WORM en `StorageService.purge_for_disposition` (E10 Increment A,
PIEZA 5). `purge_for_disposition` todavía no está expuesto por ningún router (E04
disposición final es un roadmap item pendiente de integrar) — se ejercita
directamente vía la capa de servicio, con `conn`/MinIO mockeados por completo.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.preservacion_tipos import CONTROL_INSTRUMENT_TIPOS
from app.services.storage_service import StorageService, WormLockActiveError
from tests.conftest import TENANT_SLUG, make_file_record, make_worm_row


def _mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None
    conn.fetch.return_value = []
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


@pytest.mark.asyncio
async def test_purge_blocked_while_retain_until_is_future() -> None:
    conn = _mock_conn()
    file_id = uuid.uuid4()
    record = make_file_record(file_id=file_id)
    active_lock = make_worm_row(
        expediente_id="EXP-1", retain_until=datetime.now(tz=UTC) + timedelta(days=10)
    )
    conn.fetchrow.side_effect = [record, active_lock]

    with patch("app.services.storage_service.get_minio") as get_minio_mock:
        minio = MagicMock()
        minio.remove_object = AsyncMock()
        get_minio_mock.return_value = minio

        service = StorageService(conn, TENANT_SLUG)
        with pytest.raises(WormLockActiveError):
            await service.purge_for_disposition(file_id, "EXP-1", reason="disposicion_final")

        minio.remove_object.assert_not_awaited()

    # Traza en audit_log de la purga bloqueada.
    insert_calls = [
        c
        for c in conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(insert_calls) == 1
    assert insert_calls[0].args[5] == "file.purga_bloqueada_worm"


@pytest.mark.asyncio
async def test_purge_blocked_by_legal_hold_even_if_retain_until_past() -> None:
    conn = _mock_conn()
    file_id = uuid.uuid4()
    record = make_file_record(file_id=file_id)
    active_lock = make_worm_row(
        expediente_id="EXP-2",
        retain_until=datetime.now(tz=UTC) - timedelta(days=1),  # ya venció...
        legal_hold=True,  # ...pero legal_hold sigue bloqueando
    )
    conn.fetchrow.side_effect = [record, active_lock]

    with patch("app.services.storage_service.get_minio") as get_minio_mock:
        minio = MagicMock()
        minio.remove_object = AsyncMock()
        get_minio_mock.return_value = minio

        service = StorageService(conn, TENANT_SLUG)
        with pytest.raises(WormLockActiveError):
            await service.purge_for_disposition(file_id, "EXP-2", reason="disposicion_final")

        minio.remove_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_blocked_by_aip_type_worm_row() -> None:
    """El gate no filtra por `tipo`: una fila `preservacion_worm_objeto` del
    AIP real (Increment B, `tipo='aip'`) bloquea la disposición exactamente
    igual que una del índice (`tipo='indice'`, Increment A) — la disposición
    es un acto todo-o-nada por expediente bajo la misma TRD."""
    conn = _mock_conn()
    file_id = uuid.uuid4()
    record = make_file_record(file_id=file_id)
    active_lock = make_worm_row(
        expediente_id="EXP-AIP",
        tipo="aip",
        retain_until=datetime.now(tz=UTC) + timedelta(days=10),
    )
    conn.fetchrow.side_effect = [record, active_lock]

    with patch("app.services.storage_service.get_minio") as get_minio_mock:
        minio = MagicMock()
        minio.remove_object = AsyncMock()
        get_minio_mock.return_value = minio

        service = StorageService(conn, TENANT_SLUG)
        with pytest.raises(WormLockActiveError):
            await service.purge_for_disposition(file_id, "EXP-AIP", reason="disposicion_final")

        minio.remove_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_allowed_once_worm_lock_expired() -> None:
    """Sin fila activa (expirada o nunca protegida) → la purga procede normalmente."""
    conn = _mock_conn()
    file_id = uuid.uuid4()
    record = make_file_record(file_id=file_id)
    conn.fetchrow.side_effect = [record, None]  # active_lock_for_expediente → nada bloquea
    conn.execute.side_effect = None
    conn.execute.return_value = "DELETE 1"

    with patch("app.services.storage_service.get_minio") as get_minio_mock:
        minio = MagicMock()
        minio.remove_object = AsyncMock()
        get_minio_mock.return_value = minio

        service = StorageService(conn, TENANT_SLUG)
        purged = await service.purge_for_disposition(file_id, "EXP-3", reason="disposicion_final")

        assert purged is True
        minio.remove_object.assert_awaited_once_with(record["bucket"], record["object_key"])


@pytest.mark.asyncio
async def test_purge_canonicalizes_expediente_id_before_gate_query() -> None:
    """El `expediente_id` recibido aquí debe normalizarse con la MISMA regla
    que aplica `ProtegerIndiceRequest` al proteger — de lo contrario, un
    espacio incidental distinto entre ambas llamadas rompería el gate por
    igualdad exacta de string (hallazgo de auditoría Increment A)."""
    conn = _mock_conn()
    file_id = uuid.uuid4()
    record = make_file_record(file_id=file_id)
    active_lock = make_worm_row(
        expediente_id="EXP RARO", retain_until=datetime.now(tz=UTC) + timedelta(days=10)
    )
    conn.fetchrow.side_effect = [record, active_lock]

    with patch("app.services.storage_service.get_minio") as get_minio_mock:
        minio = MagicMock()
        minio.remove_object = AsyncMock()
        get_minio_mock.return_value = minio

        service = StorageService(conn, TENANT_SLUG)
        with pytest.raises(WormLockActiveError):
            await service.purge_for_disposition(
                file_id, "  EXP   RARO  ", reason="disposicion_final"
            )

    # active_lock_for_expediente() se llamó con el string YA canonicalizado.
    worm_query_call = conn.fetchrow.call_args_list[-1]
    assert worm_query_call.args[1] == "EXP RARO"


@pytest.mark.asyncio
async def test_purge_not_found_returns_false_without_worm_query() -> None:
    """Si el archivo no existe, ni siquiera se consulta la tabla WORM."""
    conn = _mock_conn()
    conn.fetchrow.return_value = None

    with patch("app.services.storage_service.get_minio") as get_minio_mock:
        minio = MagicMock()
        minio.remove_object = AsyncMock()
        get_minio_mock.return_value = minio

        service = StorageService(conn, TENANT_SLUG)
        purged = await service.purge_for_disposition(
            uuid.uuid4(), "EXP-4", reason="disposicion_final"
        )

        assert purged is False
        minio.remove_object.assert_not_awaited()

    # Solo 1 fetchrow (get_by_id_any_state) — active_lock_for_expediente no se llamó.
    assert conn.fetchrow.call_count == 1


@pytest.mark.asyncio
async def test_purge_passes_control_instrument_tipos_to_gate_query() -> None:
    """E10 Inc.2 (remedio): `purge_for_disposition` DEBE pasar
    `CONTROL_INSTRUMENT_TIPOS` (hoy `{'acta_transferencia'}`) como
    `exclude_retention_tipos` a `active_lock_for_expediente` — es el wiring
    que hace que la retención del acta deje de postergar la disposición del
    contenido. La cobertura del SQL real (que ese exclude de verdad filtra
    en Postgres) vive en `tests/integration/test_worm_gate_real_sql.py`."""
    conn = _mock_conn()
    file_id = uuid.uuid4()
    record = make_file_record(file_id=file_id)
    conn.fetchrow.side_effect = [record, None]  # nada bloquea
    conn.execute.return_value = "DELETE 1"

    with patch("app.services.storage_service.get_minio") as get_minio_mock:
        minio = MagicMock()
        minio.remove_object = AsyncMock()
        get_minio_mock.return_value = minio

        service = StorageService(conn, TENANT_SLUG)
        purged = await service.purge_for_disposition(file_id, "EXP-CI", reason="disposicion_final")

        assert purged is True

    worm_query_call = conn.fetchrow.call_args_list[-1]
    # repo.active_lock_for_expediente(expediente_id, list(exclude_retention_tipos))
    assert worm_query_call.args[1] == "EXP-CI"
    assert set(worm_query_call.args[2]) == set(CONTROL_INSTRUMENT_TIPOS)


@pytest.mark.asyncio
async def test_purge_blocked_by_acta_transferencia_legal_hold() -> None:
    """Matriz E10 Inc.2 #3 a nivel de wiring: aunque el acta sea un
    instrumento de control excluido por retención, `legal_hold=true` en ESE
    mismo objeto sigue bloqueando la disposición del contenido — el remedio
    solo exime la retención por tiempo, nunca el hold."""
    conn = _mock_conn()
    file_id = uuid.uuid4()
    record = make_file_record(file_id=file_id)
    active_lock = make_worm_row(
        expediente_id="EXP-ACTA-HOLD",
        tipo="acta_transferencia",
        retain_until=datetime.now(tz=UTC) - timedelta(days=1),  # vencido...
        legal_hold=True,  # ...pero el hold sigue bloqueando
    )
    conn.fetchrow.side_effect = [record, active_lock]

    with patch("app.services.storage_service.get_minio") as get_minio_mock:
        minio = MagicMock()
        minio.remove_object = AsyncMock()
        get_minio_mock.return_value = minio

        service = StorageService(conn, TENANT_SLUG)
        with pytest.raises(WormLockActiveError):
            await service.purge_for_disposition(
                file_id, "EXP-ACTA-HOLD", reason="disposicion_final"
            )

        minio.remove_object.assert_not_awaited()
