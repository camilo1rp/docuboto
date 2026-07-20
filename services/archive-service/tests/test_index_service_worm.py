# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cableado WORM/Object-Lock del índice electrónico firmado (E10 Inc.1):
`IndexService._resolve_worm_retention` (derivación de retención desde la
TRD, fail-closed) y `IndexService.proteger_worm_indice` (disparo best-effort,
marca atómica + asiento, backoff/agotamiento).

Todo pure-unit (conexión mockeada) — la atomicidad/idempotencia REAL contra
Postgres se prueba aparte en `tests/integration/test_indice_worm_atomicity.py`.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.storage_client import WormProtectionOutcome
from app.core.config import settings
from app.services.index_service import IndexService
from tests.conftest import USER_ID, make_expediente_record, make_trd_serie_record

TENANT = "demo"


def _mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _indice_row(**overrides) -> dict:
    base = {
        "id": uuid.uuid4(),
        "expediente_id": uuid.uuid4(),
        "estado": "firmado",
        "minio_key_firmado": f"{uuid.uuid4()}/indice.xml",
        "worm_protegido_at": None,
        "worm_object_id": None,
        "worm_reconcile_intentos": 0,
        "worm_reconcile_proximo_at": None,
        "worm_reconcile_ultimo_error": None,
        "worm_reconcile_agotado": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _resolve_worm_retention — derivación fail-closed desde la TRD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_worm_retention_sums_gestion_and_central_years() -> None:
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    serie_id = uuid.uuid4()
    expediente_id = uuid.uuid4()
    closed_at = datetime(2026, 3, 15, tzinfo=UTC)
    conn.fetchrow.side_effect = [
        make_expediente_record(
            exp_id=expediente_id, trd_serie_id=serie_id, exp_status="closed", closed_at=closed_at
        ),
        make_trd_serie_record(
            serie_id=serie_id, archivo_gestion_years=5, archivo_central_years=8
        ),
    ]

    result = await index._resolve_worm_retention(expediente_id)

    assert result == (13, date(2026, 3, 15))


@pytest.mark.asyncio
async def test_resolve_worm_retention_none_when_expediente_missing() -> None:
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    conn.fetchrow.return_value = None

    assert await index._resolve_worm_retention(uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_resolve_worm_retention_none_when_not_closed() -> None:
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    conn.fetchrow.return_value = make_expediente_record(
        trd_serie_id=uuid.uuid4(), exp_status="open", closed_at=None
    )

    assert await index._resolve_worm_retention(uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_resolve_worm_retention_none_when_no_trd_serie() -> None:
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    conn.fetchrow.return_value = make_expediente_record(
        trd_serie_id=None, exp_status="closed", closed_at=datetime.now(tz=UTC)
    )

    assert await index._resolve_worm_retention(uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_resolve_worm_retention_none_when_serie_not_found() -> None:
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    serie_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        make_expediente_record(
            trd_serie_id=serie_id, exp_status="closed", closed_at=datetime.now(tz=UTC)
        ),
        None,  # get_trd_serie_by_id: serie borrada/no encontrada
    ]

    assert await index._resolve_worm_retention(uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_resolve_worm_retention_none_when_total_is_zero() -> None:
    """TRD sin años de retención declarados (ambos None/0) — fail-closed, NO
    se inventa una retención mínima arbitraria."""
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    serie_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        make_expediente_record(
            trd_serie_id=serie_id, exp_status="closed", closed_at=datetime.now(tz=UTC)
        ),
        make_trd_serie_record(serie_id=serie_id, archivo_gestion_years=None, archivo_central_years=None),
    ]

    assert await index._resolve_worm_retention(uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_resolve_worm_retention_none_when_total_exceeds_100_years() -> None:
    """Defensa en profundidad: el mismo techo [1, 100] que exige
    `RetentionMixin` de storage-service, verificado TAMBIÉN en este lado."""
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    serie_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        make_expediente_record(
            trd_serie_id=serie_id, exp_status="closed", closed_at=datetime.now(tz=UTC)
        ),
        make_trd_serie_record(serie_id=serie_id, archivo_gestion_years=60, archivo_central_years=60),
    ]

    assert await index._resolve_worm_retention(uuid.uuid4()) is None


# ---------------------------------------------------------------------------
# proteger_worm_indice — disparo best-effort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proteger_worm_indice_omitted_when_indice_missing() -> None:
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    conn.fetchrow.return_value = None

    resultado = await index.proteger_worm_indice(
        uuid.uuid4(), uuid.uuid4(), actor_id=str(USER_ID), origen="close"
    )

    assert resultado == "omitido"


@pytest.mark.asyncio
async def test_proteger_worm_indice_omitted_when_not_firmado() -> None:
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    conn.fetchrow.return_value = _indice_row(estado="pendiente_firma")

    resultado = await index.proteger_worm_indice(
        uuid.uuid4(), uuid.uuid4(), actor_id=str(USER_ID), origen="close"
    )

    assert resultado == "omitido"


@pytest.mark.asyncio
async def test_proteger_worm_indice_omitted_when_already_protected() -> None:
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    conn.fetchrow.return_value = _indice_row(worm_protegido_at=datetime.now(tz=UTC))

    resultado = await index.proteger_worm_indice(
        uuid.uuid4(), uuid.uuid4(), actor_id=str(USER_ID), origen="close"
    )

    assert resultado == "omitido"


@pytest.mark.asyncio
async def test_proteger_worm_indice_omitted_when_no_minio_key() -> None:
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    conn.fetchrow.return_value = _indice_row(minio_key_firmado=None)

    resultado = await index.proteger_worm_indice(
        uuid.uuid4(), uuid.uuid4(), actor_id=str(USER_ID), origen="close"
    )

    assert resultado == "omitido"


@pytest.mark.asyncio
async def test_proteger_worm_indice_close_uses_public_client_with_actor() -> None:
    """`origen="close"` llama `proteger_indice_worm` (X-User-Id=actor_id, ruta
    pública) — NUNCA la ruta interna."""
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    row = _indice_row()
    conn.fetchrow.return_value = row
    conn.execute.return_value = "UPDATE 1"
    index._resolve_worm_retention = AsyncMock(return_value=(5, date(2026, 1, 1)))

    with (
        patch(
            "app.clients.storage_client.proteger_indice_worm",
            new=AsyncMock(
                return_value=WormProtectionOutcome(ok=True, worm_object_id=uuid.uuid4())
            ),
        ) as mock_public,
        patch(
            "app.clients.storage_client.proteger_indice_worm_internal", new=AsyncMock()
        ) as mock_internal,
    ):
        resultado = await index.proteger_worm_indice(
            row["expediente_id"], row["id"], actor_id=str(USER_ID), origen="close"
        )

    assert resultado == "protegido"
    mock_public.assert_awaited_once()
    mock_internal.assert_not_awaited()
    assert mock_public.await_args.kwargs["actor_id"] == str(USER_ID)
    assert mock_public.await_args.kwargs["file_id"] == row["minio_key_firmado"].split("/", 1)[0]
    assert mock_public.await_args.kwargs["retencion_anios"] == 5


@pytest.mark.asyncio
async def test_proteger_worm_indice_reconciliation_uses_internal_client() -> None:
    """`origen="reconciliation"` llama `proteger_indice_worm_internal`
    (X-Internal-Token, SYSTEM_RECONCILER_ID) — NUNCA la ruta pública, y NUNCA
    requiere `actor_id`."""
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    row = _indice_row()
    conn.fetchrow.return_value = row
    conn.execute.return_value = "UPDATE 1"
    index._resolve_worm_retention = AsyncMock(return_value=(5, date(2026, 1, 1)))

    with (
        patch(
            "app.clients.storage_client.proteger_indice_worm_internal",
            new=AsyncMock(
                return_value=WormProtectionOutcome(ok=True, worm_object_id=uuid.uuid4())
            ),
        ) as mock_internal,
        patch("app.clients.storage_client.proteger_indice_worm", new=AsyncMock()) as mock_public,
    ):
        resultado = await index.proteger_worm_indice(
            row["expediente_id"], row["id"], actor_id=None, origen="reconciliation"
        )

    assert resultado == "protegido"
    mock_internal.assert_awaited_once()
    mock_public.assert_not_awaited()


@pytest.mark.asyncio
async def test_proteger_worm_indice_success_marks_atomically_and_audits() -> None:
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    row = _indice_row()
    conn.fetchrow.return_value = row
    conn.execute.return_value = "UPDATE 1"  # mark_worm_protegido: affected=1
    index._resolve_worm_retention = AsyncMock(return_value=(5, date(2026, 1, 1)))
    worm_object_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    with patch(
        "app.clients.storage_client.proteger_indice_worm",
        new=AsyncMock(
            return_value=WormProtectionOutcome(
                ok=True, worm_object_id=worm_object_id, retain_until=retain_until
            )
        ),
    ):
        resultado = await index.proteger_worm_indice(
            row["expediente_id"], row["id"], actor_id=str(USER_ID), origen="close"
        )

    assert resultado == "protegido"

    update_calls = [
        c
        for c in conn.execute.call_args_list
        if c.args and "worm_protegido_at = now()" in c.args[0]
    ]
    assert len(update_calls) == 1
    # `worm_retain_until` (E10 debt, migración 031) se pasa TAL CUAL desde
    # `WormProtectionOutcome.retain_until` -- no se recalcula ni se
    # trunca aquí, `mark_worm_protegido`/COALESCE del lado del repositorio
    # es quien decide qué hacer si viniera None.
    assert update_calls[0].args[1:] == (row["id"], worm_object_id, retain_until)

    audit_calls = [
        c
        for c in conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0].args[5] == "archive.indice_worm_protegido"
    assert audit_calls[0].args[6] == "expediente_indice"
    payload = json.loads(audit_calls[0].args[8])
    assert payload["worm_object_id"] == str(worm_object_id)
    assert payload["origen"] == "close"


@pytest.mark.asyncio
async def test_proteger_worm_indice_lost_race_does_not_write_phantom_audit() -> None:
    """`mark_worm_protegido` casa 0 filas (otra llamada concurrente ya
    protegió este índice) -> NO se escribe el asiento fantasma, mismo patrón
    S1 que `mark_signed`."""
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    row = _indice_row()
    conn.fetchrow.return_value = row
    conn.execute.return_value = "UPDATE 0"  # affected=0: perdió la carrera
    index._resolve_worm_retention = AsyncMock(return_value=(5, date(2026, 1, 1)))

    with patch(
        "app.clients.storage_client.proteger_indice_worm",
        new=AsyncMock(return_value=WormProtectionOutcome(ok=True, worm_object_id=uuid.uuid4())),
    ):
        resultado = await index.proteger_worm_indice(
            row["expediente_id"], row["id"], actor_id=str(USER_ID), origen="close"
        )

    assert resultado == "omitido"
    audit_calls = [
        c
        for c in conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert audit_calls == []


@pytest.mark.asyncio
async def test_proteger_worm_indice_disabled_schedules_backoff_not_agotado() -> None:
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    row = _indice_row(worm_reconcile_intentos=0)
    conn.fetchrow.return_value = row
    index._resolve_worm_retention = AsyncMock(return_value=(5, date(2026, 1, 1)))

    with patch(
        "app.clients.storage_client.proteger_indice_worm",
        new=AsyncMock(return_value=WormProtectionOutcome(ok=False, disabled=True)),
    ):
        resultado = await index.proteger_worm_indice(
            row["expediente_id"], row["id"], actor_id=str(USER_ID), origen="close"
        )

    assert resultado == "pendiente"
    bump_calls = [
        c
        for c in conn.execute.call_args_list
        if c.args and "worm_reconcile_intentos = worm_reconcile_intentos + 1" in c.args[0]
    ]
    assert len(bump_calls) == 1
    # params: (indice_id, proximo_at, ultimo_error, agotado)
    assert bump_calls[0].args[3] == "worm_disabled"
    assert bump_calls[0].args[4] is False  # agotado
    assert bump_calls[0].args[2] is not None  # proximo_at programado


@pytest.mark.asyncio
async def test_proteger_worm_indice_transient_failure_marks_worm_no_confirmado() -> None:
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    row = _indice_row(worm_reconcile_intentos=0)
    conn.fetchrow.return_value = row
    index._resolve_worm_retention = AsyncMock(return_value=(5, date(2026, 1, 1)))

    with patch(
        "app.clients.storage_client.proteger_indice_worm_internal",
        new=AsyncMock(return_value=WormProtectionOutcome(ok=False, transient=True)),
    ):
        resultado = await index.proteger_worm_indice(
            row["expediente_id"], row["id"], actor_id=None, origen="reconciliation"
        )

    assert resultado == "pendiente"
    bump_calls = [
        c
        for c in conn.execute.call_args_list
        if c.args and "worm_reconcile_intentos = worm_reconcile_intentos + 1" in c.args[0]
    ]
    assert bump_calls[0].args[3] == "worm_no_confirmado"


@pytest.mark.asyncio
async def test_proteger_worm_indice_exhausts_after_max_intentos_and_audits() -> None:
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    max_intentos = settings.indice_reconciliation_max_intentos
    row = _indice_row(worm_reconcile_intentos=max_intentos - 1)
    conn.fetchrow.return_value = row
    index._resolve_worm_retention = AsyncMock(return_value=(5, date(2026, 1, 1)))

    with patch(
        "app.clients.storage_client.proteger_indice_worm_internal",
        new=AsyncMock(return_value=WormProtectionOutcome(ok=False, transient=True)),
    ):
        resultado = await index.proteger_worm_indice(
            row["expediente_id"], row["id"], actor_id=None, origen="reconciliation"
        )

    assert resultado == "agotado"
    bump_calls = [
        c
        for c in conn.execute.call_args_list
        if c.args and "worm_reconcile_intentos = worm_reconcile_intentos + 1" in c.args[0]
    ]
    assert bump_calls[0].args[4] is True  # agotado
    assert bump_calls[0].args[2] is None  # sin próximo intento programado

    audit_calls = [
        c
        for c in conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0].args[5] == "archive.indice_worm_reconcile_agotado"


@pytest.mark.asyncio
async def test_proteger_worm_indice_trd_sin_retencion_uses_backoff_not_immediate_agotado() -> None:
    """Fail-closed sin TRD resoluble: entra al MISMO backoff que cualquier
    otro fallo (no se agota de inmediato en el primer intento) — un
    archivista puede corregir la serie TRD más tarde y la próxima barrida
    reintenta sola."""
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    row = _indice_row(worm_reconcile_intentos=0)
    conn.fetchrow.return_value = row
    index._resolve_worm_retention = AsyncMock(return_value=None)

    resultado = await index.proteger_worm_indice(
        row["expediente_id"], row["id"], actor_id=None, origen="reconciliation"
    )

    assert resultado == "pendiente"
    bump_calls = [
        c
        for c in conn.execute.call_args_list
        if c.args and "worm_reconcile_intentos = worm_reconcile_intentos + 1" in c.args[0]
    ]
    assert bump_calls[0].args[3] == "trd_sin_retencion"


@pytest.mark.asyncio
async def test_proteger_worm_indice_never_raises_on_unexpected_exception() -> None:
    """Best-effort real: una excepción NO prevista (p. ej. BD momentáneamente
    caída durante `get_by_id`) queda contenida — NUNCA se propaga al
    llamador (el cierre del expediente ya es un hecho consumado)."""
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    conn.fetchrow.side_effect = RuntimeError("boom (itest)")

    resultado = await index.proteger_worm_indice(
        uuid.uuid4(), uuid.uuid4(), actor_id=str(USER_ID), origen="close"
    )

    assert resultado == "pendiente"
