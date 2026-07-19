# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cableado WORM/Object-Lock del ACTA de transferencia firmada (E10 Inc.2
wiring): `TransferenciaService._resolve_worm_retention_acta` (derivación de
retención desde la TRD del expediente, fail-closed, ancla `fecha_inicio` en
`firmado_at` del acta -- NO en el cierre del expediente) y
`TransferenciaService.proteger_worm_acta` (disparo best-effort, marca
atómica + asiento, backoff/agotamiento) -- espejo EXACTO de
`test_index_service_worm.py` (E10 Inc.1) para su gemela del acta.

Todo pure-unit (conexión mockeada) -- la atomicidad/idempotencia REAL contra
Postgres se prueba aparte en
`tests/integration/test_transferencia_acta_worm_atomicity.py`.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.storage_client import WormProtectionOutcome
from app.core.config import settings
from app.services.transferencia_service import TransferenciaService
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


def _acta_firma_row(**overrides) -> dict:
    base = {
        "transferencia_id": uuid.uuid4(),
        "estado": "firmado",
        "firma_id": uuid.uuid4(),
        "minio_key_firmado": f"{uuid.uuid4()}/acta-transferencia.xml",
        "xades_level": "XAdES-T",
        "acreditado": False,
        "last_error": None,
        "reconcile_intentos": 0,
        "reconcile_proximo_at": None,
        "reconcile_agotado": False,
        "created_at": datetime.now(tz=UTC),
        "firmado_at": datetime(2026, 4, 1, tzinfo=UTC),
        "worm_protegido_at": None,
        "worm_object_id": None,
        "worm_reconcile_intentos": 0,
        "worm_reconcile_proximo_at": None,
        "worm_reconcile_ultimo_error": None,
        "worm_reconcile_agotado": False,
    }
    base.update(overrides)
    return base


def _transferencia_row(*, expediente_id: uuid.UUID | None = None, **overrides) -> dict:
    base = {
        "id": uuid.uuid4(),
        "tipo": "primaria",
        "estado": "recibida",
        "expediente_id": expediente_id or uuid.uuid4(),
        "ubicacion_origen_id": None,
        "ubicacion_destino_id": None,
        "creada_por": "itest",
        "observacion": None,
        "created_at": datetime.now(tz=UTC),
        "enviada_at": datetime.now(tz=UTC),
        "enviada_por": "itest",
        "recibida_at": datetime.now(tz=UTC),
        "decidida_por": "itest",
        "motivo_rechazo": None,
        "rechazada_at": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _resolve_worm_retention_acta -- derivación fail-closed desde la TRD,
# fecha_inicio = firmado_at del ACTA (no el cierre del expediente).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_worm_retention_acta_sums_gestion_and_central_years() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    serie_id = uuid.uuid4()
    expediente_id = uuid.uuid4()
    transferencia_id = uuid.uuid4()
    firmado_at = datetime(2026, 4, 1, tzinfo=UTC)
    conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=expediente_id, trd_serie_id=serie_id),
        make_trd_serie_record(serie_id=serie_id, archivo_gestion_years=5, archivo_central_years=8),
    ]

    result = await service._resolve_worm_retention_acta(
        transferencia_id, expediente_id, firmado_at=firmado_at
    )

    assert result == (13, date(2026, 4, 1))


@pytest.mark.asyncio
async def test_resolve_worm_retention_acta_none_when_firmado_at_missing() -> None:
    """Fail-closed sin siquiera tocar BD: `firmado_at=None` no debería
    ocurrir con `estado='firmado'` (ver `mark_acta_firmada`), pero el método
    no lo asume."""
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)

    result = await service._resolve_worm_retention_acta(
        uuid.uuid4(), uuid.uuid4(), firmado_at=None
    )

    assert result is None
    conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_worm_retention_acta_none_when_expediente_missing() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    conn.fetchrow.return_value = None

    result = await service._resolve_worm_retention_acta(
        uuid.uuid4(), uuid.uuid4(), firmado_at=datetime.now(tz=UTC)
    )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_worm_retention_acta_none_when_no_trd_serie() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    conn.fetchrow.return_value = make_expediente_record(trd_serie_id=None)

    result = await service._resolve_worm_retention_acta(
        uuid.uuid4(), uuid.uuid4(), firmado_at=datetime.now(tz=UTC)
    )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_worm_retention_acta_none_when_serie_not_found() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    serie_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        make_expediente_record(trd_serie_id=serie_id),
        None,  # get_trd_serie_by_id: serie borrada/no encontrada
    ]

    result = await service._resolve_worm_retention_acta(
        uuid.uuid4(), uuid.uuid4(), firmado_at=datetime.now(tz=UTC)
    )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_worm_retention_acta_none_when_total_is_zero() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    serie_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        make_expediente_record(trd_serie_id=serie_id),
        make_trd_serie_record(
            serie_id=serie_id, archivo_gestion_years=None, archivo_central_years=None
        ),
    ]

    result = await service._resolve_worm_retention_acta(
        uuid.uuid4(), uuid.uuid4(), firmado_at=datetime.now(tz=UTC)
    )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_worm_retention_acta_none_when_total_exceeds_100_years() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    serie_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        make_expediente_record(trd_serie_id=serie_id),
        make_trd_serie_record(
            serie_id=serie_id, archivo_gestion_years=60, archivo_central_years=60
        ),
    ]

    result = await service._resolve_worm_retention_acta(
        uuid.uuid4(), uuid.uuid4(), firmado_at=datetime.now(tz=UTC)
    )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_worm_retention_acta_anchors_on_firmado_at_not_closed_at() -> None:
    """El plazo se ancla en la FECHA DE FIRMA del acta, no en el cierre del
    expediente -- un acta puede sellarse mucho después del cierre (H-H,
    transferencia primaria/secundaria), y anclar al cierre subestimaría el
    plazo real de custodia del acta como artefacto derivado."""
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    serie_id = uuid.uuid4()
    # closed_at deliberadamente MUY anterior a firmado_at -- si el método
    # usara closed_at por error, la fecha resultante sería 2020, no 2027.
    conn.fetchrow.side_effect = [
        make_expediente_record(
            trd_serie_id=serie_id, exp_status="transferred", closed_at=datetime(2020, 1, 1, tzinfo=UTC)
        ),
        make_trd_serie_record(serie_id=serie_id, archivo_gestion_years=3, archivo_central_years=2),
    ]

    result = await service._resolve_worm_retention_acta(
        uuid.uuid4(), uuid.uuid4(), firmado_at=datetime(2027, 6, 15, tzinfo=UTC)
    )

    assert result == (5, date(2027, 6, 15))


# ---------------------------------------------------------------------------
# proteger_worm_acta -- disparo best-effort, espejo de proteger_worm_indice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proteger_worm_acta_omitted_when_firma_row_missing() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    conn.fetchrow.return_value = None

    resultado = await service.proteger_worm_acta(
        uuid.uuid4(), actor_id=str(USER_ID), origen="close"
    )

    assert resultado == "omitido"


@pytest.mark.asyncio
async def test_proteger_worm_acta_omitted_when_not_firmado() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    conn.fetchrow.return_value = _acta_firma_row(estado="pendiente_firma")

    resultado = await service.proteger_worm_acta(
        uuid.uuid4(), actor_id=str(USER_ID), origen="close"
    )

    assert resultado == "omitido"


@pytest.mark.asyncio
async def test_proteger_worm_acta_omitted_when_already_protected() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    conn.fetchrow.return_value = _acta_firma_row(worm_protegido_at=datetime.now(tz=UTC))

    resultado = await service.proteger_worm_acta(
        uuid.uuid4(), actor_id=str(USER_ID), origen="close"
    )

    assert resultado == "omitido"


@pytest.mark.asyncio
async def test_proteger_worm_acta_omitted_when_no_minio_key() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    conn.fetchrow.return_value = _acta_firma_row(minio_key_firmado=None)

    resultado = await service.proteger_worm_acta(
        uuid.uuid4(), actor_id=str(USER_ID), origen="close"
    )

    assert resultado == "omitido"


@pytest.mark.asyncio
async def test_proteger_worm_acta_omitted_when_transferencia_missing() -> None:
    """El acta existe y está firmada, pero `TransferenciaRepository.get`
    devuelve `None` (caso defensivo: no debería ocurrir con una FK real,
    pero el método no lo asume)."""
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    firma_row = _acta_firma_row()
    conn.fetchrow.side_effect = [firma_row, None]

    resultado = await service.proteger_worm_acta(
        firma_row["transferencia_id"], actor_id=str(USER_ID), origen="close"
    )

    assert resultado == "omitido"


@pytest.mark.asyncio
async def test_proteger_worm_acta_close_uses_public_client_with_actor() -> None:
    """`origen="close"` llama `proteger_artefacto_worm` (X-User-Id=actor_id,
    ruta pública, tipo='acta_transferencia') -- NUNCA la ruta interna."""
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    expediente_id = uuid.uuid4()
    firma_row = _acta_firma_row()
    transferencia = _transferencia_row(expediente_id=expediente_id)
    conn.fetchrow.side_effect = [firma_row, transferencia]
    conn.execute.return_value = "UPDATE 1"
    service._resolve_worm_retention_acta = AsyncMock(return_value=(5, date(2026, 1, 1)))

    with (
        patch(
            "app.clients.storage_client.proteger_artefacto_worm",
            new=AsyncMock(
                return_value=WormProtectionOutcome(ok=True, worm_object_id=uuid.uuid4())
            ),
        ) as mock_public,
        patch(
            "app.clients.storage_client.proteger_artefacto_worm_internal", new=AsyncMock()
        ) as mock_internal,
    ):
        resultado = await service.proteger_worm_acta(
            firma_row["transferencia_id"], actor_id=str(USER_ID), origen="close"
        )

    assert resultado == "protegido"
    mock_public.assert_awaited_once()
    mock_internal.assert_not_awaited()
    assert mock_public.await_args.kwargs["actor_id"] == str(USER_ID)
    assert mock_public.await_args.kwargs["tipo"] == "acta_transferencia"
    assert (
        mock_public.await_args.kwargs["file_id"]
        == firma_row["minio_key_firmado"].split("/", 1)[0]
    )
    assert mock_public.await_args.kwargs["expediente_id"] == str(expediente_id)
    assert mock_public.await_args.kwargs["retencion_anios"] == 5


@pytest.mark.asyncio
async def test_proteger_worm_acta_reconciliation_uses_internal_client() -> None:
    """`origen="reconciliation"` llama `proteger_artefacto_worm_internal`
    (X-Internal-Token, SYSTEM_RECONCILER_ID) -- NUNCA la ruta pública, y
    NUNCA requiere `actor_id`."""
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    firma_row = _acta_firma_row()
    transferencia = _transferencia_row()
    conn.fetchrow.side_effect = [firma_row, transferencia]
    conn.execute.return_value = "UPDATE 1"
    service._resolve_worm_retention_acta = AsyncMock(return_value=(5, date(2026, 1, 1)))

    with (
        patch(
            "app.clients.storage_client.proteger_artefacto_worm_internal",
            new=AsyncMock(
                return_value=WormProtectionOutcome(ok=True, worm_object_id=uuid.uuid4())
            ),
        ) as mock_internal,
        patch(
            "app.clients.storage_client.proteger_artefacto_worm", new=AsyncMock()
        ) as mock_public,
    ):
        resultado = await service.proteger_worm_acta(
            firma_row["transferencia_id"], actor_id=None, origen="reconciliation"
        )

    assert resultado == "protegido"
    mock_internal.assert_awaited_once()
    mock_public.assert_not_awaited()
    assert mock_internal.await_args.kwargs["tipo"] == "acta_transferencia"


@pytest.mark.asyncio
async def test_proteger_worm_acta_success_marks_atomically_and_audits() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    expediente_id = uuid.uuid4()
    firma_row = _acta_firma_row()
    transferencia = _transferencia_row(expediente_id=expediente_id)
    conn.fetchrow.side_effect = [firma_row, transferencia]
    conn.execute.return_value = "UPDATE 1"  # mark_acta_worm_protegido: affected=1
    service._resolve_worm_retention_acta = AsyncMock(return_value=(5, date(2026, 1, 1)))
    worm_object_id = uuid.uuid4()

    with patch(
        "app.clients.storage_client.proteger_artefacto_worm",
        new=AsyncMock(return_value=WormProtectionOutcome(ok=True, worm_object_id=worm_object_id)),
    ):
        resultado = await service.proteger_worm_acta(
            firma_row["transferencia_id"], actor_id=str(USER_ID), origen="close"
        )

    assert resultado == "protegido"

    update_calls = [
        c
        for c in conn.execute.call_args_list
        if c.args and "worm_protegido_at = now()" in c.args[0]
    ]
    assert len(update_calls) == 1
    assert update_calls[0].args[1:] == (firma_row["transferencia_id"], worm_object_id)

    audit_calls = [
        c
        for c in conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0].args[5] == "archive.acta_worm_protegido"
    assert audit_calls[0].args[6] == "transferencia_acta"
    payload = json.loads(audit_calls[0].args[8])
    assert payload["worm_object_id"] == str(worm_object_id)
    assert payload["expediente_id"] == str(expediente_id)
    assert payload["origen"] == "close"


@pytest.mark.asyncio
async def test_proteger_worm_acta_lost_race_does_not_write_phantom_audit() -> None:
    """`mark_acta_worm_protegido` casa 0 filas (otra llamada concurrente ya
    protegió esta acta) -> NO se escribe el asiento fantasma, mismo patrón
    S1 que `mark_acta_firmada`/`proteger_worm_indice`."""
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    firma_row = _acta_firma_row()
    transferencia = _transferencia_row()
    conn.fetchrow.side_effect = [firma_row, transferencia]
    conn.execute.return_value = "UPDATE 0"  # affected=0: perdió la carrera
    service._resolve_worm_retention_acta = AsyncMock(return_value=(5, date(2026, 1, 1)))

    with patch(
        "app.clients.storage_client.proteger_artefacto_worm",
        new=AsyncMock(return_value=WormProtectionOutcome(ok=True, worm_object_id=uuid.uuid4())),
    ):
        resultado = await service.proteger_worm_acta(
            firma_row["transferencia_id"], actor_id=str(USER_ID), origen="close"
        )

    assert resultado == "omitido"
    audit_calls = [
        c
        for c in conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert audit_calls == []


@pytest.mark.asyncio
async def test_proteger_worm_acta_disabled_schedules_backoff_not_agotado() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    firma_row = _acta_firma_row(worm_reconcile_intentos=0)
    transferencia = _transferencia_row()
    conn.fetchrow.side_effect = [firma_row, transferencia]
    service._resolve_worm_retention_acta = AsyncMock(return_value=(5, date(2026, 1, 1)))

    with patch(
        "app.clients.storage_client.proteger_artefacto_worm",
        new=AsyncMock(return_value=WormProtectionOutcome(ok=False, disabled=True)),
    ):
        resultado = await service.proteger_worm_acta(
            firma_row["transferencia_id"], actor_id=str(USER_ID), origen="close"
        )

    assert resultado == "pendiente"
    bump_calls = [
        c
        for c in conn.execute.call_args_list
        if c.args and "worm_reconcile_intentos = worm_reconcile_intentos + 1" in c.args[0]
    ]
    assert len(bump_calls) == 1
    # params: (transferencia_id, proximo_at, ultimo_error, agotado)
    assert bump_calls[0].args[3] == "worm_disabled"
    assert bump_calls[0].args[4] is False  # agotado
    assert bump_calls[0].args[2] is not None  # proximo_at programado


@pytest.mark.asyncio
async def test_proteger_worm_acta_transient_failure_marks_worm_no_confirmado() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    firma_row = _acta_firma_row(worm_reconcile_intentos=0)
    transferencia = _transferencia_row()
    conn.fetchrow.side_effect = [firma_row, transferencia]
    service._resolve_worm_retention_acta = AsyncMock(return_value=(5, date(2026, 1, 1)))

    with patch(
        "app.clients.storage_client.proteger_artefacto_worm_internal",
        new=AsyncMock(return_value=WormProtectionOutcome(ok=False, transient=True)),
    ):
        resultado = await service.proteger_worm_acta(
            firma_row["transferencia_id"], actor_id=None, origen="reconciliation"
        )

    assert resultado == "pendiente"
    bump_calls = [
        c
        for c in conn.execute.call_args_list
        if c.args and "worm_reconcile_intentos = worm_reconcile_intentos + 1" in c.args[0]
    ]
    assert bump_calls[0].args[3] == "worm_no_confirmado"


@pytest.mark.asyncio
async def test_proteger_worm_acta_exhausts_after_max_intentos_and_audits() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    max_intentos = settings.indice_reconciliation_max_intentos
    firma_row = _acta_firma_row(worm_reconcile_intentos=max_intentos - 1)
    transferencia = _transferencia_row()
    conn.fetchrow.side_effect = [firma_row, transferencia]
    service._resolve_worm_retention_acta = AsyncMock(return_value=(5, date(2026, 1, 1)))

    with patch(
        "app.clients.storage_client.proteger_artefacto_worm_internal",
        new=AsyncMock(return_value=WormProtectionOutcome(ok=False, transient=True)),
    ):
        resultado = await service.proteger_worm_acta(
            firma_row["transferencia_id"], actor_id=None, origen="reconciliation"
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
    assert audit_calls[0].args[5] == "archive.acta_worm_reconcile_agotado"


@pytest.mark.asyncio
async def test_proteger_worm_acta_trd_sin_retencion_uses_backoff_not_immediate_agotado() -> None:
    """Fail-closed sin TRD resoluble: entra al MISMO backoff que cualquier
    otro fallo (no se agota de inmediato en el primer intento)."""
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    firma_row = _acta_firma_row(worm_reconcile_intentos=0)
    transferencia = _transferencia_row()
    conn.fetchrow.side_effect = [firma_row, transferencia]
    service._resolve_worm_retention_acta = AsyncMock(return_value=None)

    resultado = await service.proteger_worm_acta(
        firma_row["transferencia_id"], actor_id=None, origen="reconciliation"
    )

    assert resultado == "pendiente"
    bump_calls = [
        c
        for c in conn.execute.call_args_list
        if c.args and "worm_reconcile_intentos = worm_reconcile_intentos + 1" in c.args[0]
    ]
    assert bump_calls[0].args[3] == "trd_sin_retencion"


@pytest.mark.asyncio
async def test_proteger_worm_acta_never_raises_on_unexpected_exception() -> None:
    """Best-effort real: una excepción NO prevista queda contenida -- NUNCA
    se propaga al llamador (el sellado del acta ya es un hecho consumado)."""
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    conn.fetchrow.side_effect = RuntimeError("boom (itest)")

    resultado = await service.proteger_worm_acta(
        uuid.uuid4(), actor_id=str(USER_ID), origen="close"
    )

    assert resultado == "pendiente"
