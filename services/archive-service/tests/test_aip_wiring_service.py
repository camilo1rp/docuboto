# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cableado del AIP OAIS al cierre del expediente (E10 debt "AIP
sin-wiring"): `AipWiringService.intentar_empaquetar`
(`app/services/aip_wiring_service.py`).

Todo pure-unit: `AipRepository`/`ArchiveRepository`/`IndexRepository` son
instancias REALES (creadas en `AipWiringService.__init__`), pero sus métodos
se sustituyen por `AsyncMock` uno a uno (mismo patrón que
`index._resolve_worm_retention = AsyncMock(...)` en
`test_index_service_worm.py`) en vez de mockear la conexión cruda — evita
tener que replicar el orden exacto de `fetchrow`/`execute` de TRES
repositorios distintos en cada test. La atomicidad/idempotencia REAL contra
Postgres se prueba aparte en `tests/integration/test_aip_wiring_atomicity.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.storage_client import AipPackagingOutcome
from app.core.config import settings
from app.models.archive import Expediente, ExpedienteRadicado
from app.services.aip_wiring_service import AipWiringService

TENANT = "demo"
NOW = datetime(2026, 3, 15, tzinfo=UTC)


def _mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _radicado(*, radicado_id: uuid.UUID | None = None, estado: str = "vinculado") -> ExpedienteRadicado:
    return ExpedienteRadicado(
        expediente_id=uuid.uuid4(),
        radicado_id=radicado_id or uuid.uuid4(),
        tracking_number="2026-DEMO-E-000001",
        added_at=NOW,
        estado=estado,
    )


def _expediente(
    *,
    exp_id: uuid.UUID | None = None,
    status: str = "closed",
    closed_at: datetime | None = NOW,
    trd_serie_id: uuid.UUID | None = None,
    radicados: list[ExpedienteRadicado] | None = None,
) -> Expediente:
    return Expediente(
        id=exp_id or uuid.uuid4(),
        code="EXP-2026-0001",
        name="itest expediente AIP",
        description=None,
        trd_serie_id=trd_serie_id if trd_serie_id is not None else uuid.uuid4(),
        status=status,
        opened_by=None,
        opened_at=NOW,
        closed_at=closed_at,
        updated_at=NOW,
        radicados=radicados if radicados is not None else [],
    )


def _aip_row(**overrides) -> dict:
    base = {"expediente_id": uuid.uuid4(), "estado": "pendiente", "intentos": 0}
    base.update(overrides)
    return base


def _indice_current_row(**overrides) -> dict:
    base = {"id": uuid.uuid4()}
    base.update(overrides)
    return base


def _indice_by_id_row(**overrides) -> dict:
    base = {
        "id": uuid.uuid4(),
        "estado": "firmado",
        "worm_protegido_at": NOW,
        "minio_key_firmado": f"{uuid.uuid4()}/indice.xml",
    }
    base.update(overrides)
    return base


def _service(conn: AsyncMock) -> AipWiringService:
    return AipWiringService(conn, TENANT)


def _wire_happy_path_prereqs(
    service: AipWiringService,
    *,
    aip_row: dict | None = None,
    exp: Expediente | None = None,
    indice_row: dict | None = None,
) -> None:
    """Configura los repositorios internos hasta (e incluyendo) la
    resolución del índice VIGENTE firmado+protegido — deja `document_client`
    / `storage_client` / `_resolve_aip_retention` para que cada test los
    configure según el escenario que quiera ejercitar."""
    service._aip.get_or_create = AsyncMock(return_value=aip_row or _aip_row())
    service._archive.get_expediente_by_id = AsyncMock(return_value=exp or _expediente())
    service._index.get_current = AsyncMock(return_value=_indice_current_row())
    service._index.get_by_id = AsyncMock(return_value=indice_row or _indice_by_id_row())


# ---------------------------------------------------------------------------
# Idempotencia: fila ya no-'pendiente' -> no re-dispara
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", ["empaquetado", "omitido", "agotado"])
async def test_intentar_empaquetar_idempotent_when_not_pendiente(estado: str) -> None:
    conn = _mock_conn()
    service = _service(conn)
    service._aip.get_or_create = AsyncMock(return_value=_aip_row(estado=estado))
    service._archive.get_expediente_by_id = AsyncMock()

    resultado = await service.intentar_empaquetar(uuid.uuid4())

    assert resultado == estado
    service._archive.get_expediente_by_id.assert_not_awaited()


# ---------------------------------------------------------------------------
# Precondición dura: expediente elegible
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exp", "case_id"),
    [
        (None, "expediente_inexistente"),
        (_expediente(status="open"), "no_cerrado"),
        (_expediente(closed_at=None), "sin_closed_at"),
    ],
    ids=["expediente_inexistente", "no_cerrado", "sin_closed_at"],
)
async def test_intentar_empaquetar_backoff_when_expediente_not_elegible(exp, case_id: str) -> None:
    conn = _mock_conn()
    service = _service(conn)
    service._aip.get_or_create = AsyncMock(return_value=_aip_row(intentos=0))
    service._archive.get_expediente_by_id = AsyncMock(return_value=exp)
    service._aip.bump_intento = AsyncMock()

    with patch(
        "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal", new=AsyncMock()
    ) as mock_storage:
        resultado = await service.intentar_empaquetar(uuid.uuid4())

    assert resultado == "pendiente"
    mock_storage.assert_not_awaited()
    service._aip.bump_intento.assert_awaited_once()
    assert service._aip.bump_intento.await_args.kwargs["ultimo_error"] == "expediente_no_elegible"


@pytest.mark.asyncio
async def test_intentar_empaquetar_transferred_is_elegible() -> None:
    """`status='transferred'` es TAN elegible como `'closed'` — no se queda
    en backoff por ese motivo."""
    conn = _mock_conn()
    service = _service(conn)
    exp = _expediente(status="transferred", radicados=[])
    _wire_happy_path_prereqs(service, exp=exp)
    service._aip.mark_omitido = AsyncMock(return_value=1)

    with patch(
        "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
        new=AsyncMock(return_value={}),
    ), patch("app.services.aip_wiring_service.audit.append", new=AsyncMock()):
        resultado = await service.intentar_empaquetar(exp.id)

    # Sin radicados vigentes -> 0 documentos -> 'omitido', pero NUNCA
    # 'pendiente' por "expediente_no_elegible".
    assert resultado == "omitido"


# ---------------------------------------------------------------------------
# Precondición dura: índice VIGENTE firmado + protegido bajo WORM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intentar_empaquetar_backoff_when_no_indice_current() -> None:
    conn = _mock_conn()
    service = _service(conn)
    service._aip.get_or_create = AsyncMock(return_value=_aip_row(intentos=0))
    service._archive.get_expediente_by_id = AsyncMock(return_value=_expediente())
    service._index.get_current = AsyncMock(return_value=None)
    service._index.get_by_id = AsyncMock()
    service._aip.bump_intento = AsyncMock()

    resultado = await service.intentar_empaquetar(uuid.uuid4())

    assert resultado == "pendiente"
    service._index.get_by_id.assert_not_awaited()
    assert service._aip.bump_intento.await_args.kwargs["ultimo_error"] == "indice_no_worm_protegido"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"estado": "pendiente_firma"},
        {"worm_protegido_at": None},
        {"minio_key_firmado": None},
        {"minio_key_firmado": ""},
    ],
)
async def test_intentar_empaquetar_backoff_when_indice_not_worm_protegido(overrides: dict) -> None:
    conn = _mock_conn()
    service = _service(conn)
    service._aip.get_or_create = AsyncMock(return_value=_aip_row(intentos=0))
    service._archive.get_expediente_by_id = AsyncMock(return_value=_expediente())
    service._index.get_current = AsyncMock(return_value=_indice_current_row())
    service._index.get_by_id = AsyncMock(return_value=_indice_by_id_row(**overrides))
    service._aip.bump_intento = AsyncMock()

    with patch(
        "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal", new=AsyncMock()
    ) as mock_storage:
        resultado = await service.intentar_empaquetar(uuid.uuid4())

    assert resultado == "pendiente"
    mock_storage.assert_not_awaited()
    assert service._aip.bump_intento.await_args.kwargs["ultimo_error"] == "indice_no_worm_protegido"


# ---------------------------------------------------------------------------
# Payload: nombres sin colisión + indice_file_id resuelto
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intentar_empaquetar_builds_unique_nombres_across_radicados_same_filename() -> None:
    """Dos radicados DISTINTOS cuyo único anexo comparte el MISMO
    `filename` -> el `nombre` dentro del payload del AIP debe ser único
    (prefijo `{radicado_id}__{idx}__...`)."""
    conn = _mock_conn()
    service = _service(conn)
    radicado_1 = _radicado()
    radicado_2 = _radicado()
    indice_row = _indice_by_id_row()
    exp = _expediente(radicados=[radicado_1, radicado_2])
    _wire_happy_path_prereqs(service, exp=exp, indice_row=indice_row)
    service._resolve_aip_retention = AsyncMock(return_value=(5, date(2026, 1, 1)))
    service._aip.mark_empaquetado = AsyncMock(return_value=1)
    aip_id = uuid.uuid4()

    file_id_1 = uuid.uuid4()
    file_id_2 = uuid.uuid4()
    anexos_by_radicado = {
        str(radicado_1.radicado_id): [{"file_id": str(file_id_1), "filename": "oficio.pdf"}],
        str(radicado_2.radicado_id): [{"file_id": str(file_id_2), "filename": "oficio.pdf"}],
    }

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(return_value=anexos_by_radicado),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=True, aip_id=aip_id)),
        ) as mock_storage,
        patch("app.services.aip_wiring_service.audit.append", new=AsyncMock()),
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "empaquetado"
    documentos = mock_storage.await_args.kwargs["documentos"]
    assert len(documentos) == 2
    nombres = [d["nombre"] for d in documentos]
    assert len(set(nombres)) == 2  # sin colisión
    assert nombres[0].startswith(f"{radicado_1.radicado_id}__0__")
    assert nombres[1].startswith(f"{radicado_2.radicado_id}__0__")
    assert nombres[0].endswith("oficio.pdf")

    expected_indice_file_id = uuid.UUID(indice_row["minio_key_firmado"].split("/", 1)[0])
    assert mock_storage.await_args.kwargs["indice_file_id"] == expected_indice_file_id
    service._aip.mark_empaquetado.assert_awaited_once_with(
        exp.id, aip_id, worm_retain_until=None
    )


@pytest.mark.asyncio
async def test_intentar_empaquetar_only_radicados_vigentes_contribute() -> None:
    """Un radicado `estado='excluido'` (soft-delete) NUNCA aporta anexos al
    AIP — mismo criterio que el índice electrónico."""
    conn = _mock_conn()
    service = _service(conn)
    vigente = _radicado(estado="vinculado")
    excluido = _radicado(estado="excluido")
    exp = _expediente(radicados=[vigente, excluido])
    _wire_happy_path_prereqs(service, exp=exp)
    service._resolve_aip_retention = AsyncMock(return_value=(5, date(2026, 1, 1)))
    service._aip.mark_empaquetado = AsyncMock(return_value=1)

    async def _fake_fetch(*, tenant_slug: str, radicado_ids: list[uuid.UUID]) -> dict:
        assert excluido.radicado_id not in radicado_ids
        assert vigente.radicado_id in radicado_ids
        return {str(vigente.radicado_id): [{"file_id": str(uuid.uuid4()), "filename": "a.pdf"}]}

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(side_effect=_fake_fetch),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=True, aip_id=uuid.uuid4())),
        ),
        patch("app.services.aip_wiring_service.audit.append", new=AsyncMock()),
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "empaquetado"


# ---------------------------------------------------------------------------
# 0 anexos digitales -> 'omitido' (TERMINAL, ni siquiera llama a storage)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intentar_empaquetar_zero_anexos_marks_omitido_and_audits() -> None:
    conn = _mock_conn()
    service = _service(conn)
    radicado = _radicado()
    exp = _expediente(radicados=[radicado])
    _wire_happy_path_prereqs(service, exp=exp)
    service._aip.mark_omitido = AsyncMock(return_value=1)

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(return_value={}),  # radicado sin anexos digitales
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal", new=AsyncMock()
        ) as mock_storage,
        patch("app.services.aip_wiring_service.audit.append", new=AsyncMock()) as mock_audit,
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "omitido"
    mock_storage.assert_not_awaited()
    service._aip.mark_omitido.assert_awaited_once_with(exp.id)
    mock_audit.assert_awaited_once()
    assert mock_audit.await_args.kwargs["action"] == "archive.aip_omitido"
    assert mock_audit.await_args.kwargs["payload"]["motivo"] == "sin_anexos_digitales"


@pytest.mark.asyncio
async def test_intentar_empaquetar_zero_anexos_lost_race_no_phantom_audit() -> None:
    """`mark_omitido` casa 0 filas (otra llamada concurrente ya lo marcó) ->
    el resultado SIGUE siendo 'omitido' pero SIN escribir un asiento
    fantasma."""
    conn = _mock_conn()
    service = _service(conn)
    exp = _expediente(radicados=[_radicado()])
    _wire_happy_path_prereqs(service, exp=exp)
    service._aip.mark_omitido = AsyncMock(return_value=0)

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(return_value={}),
        ),
        patch("app.services.aip_wiring_service.audit.append", new=AsyncMock()) as mock_audit,
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "omitido"
    mock_audit.assert_not_awaited()


# ---------------------------------------------------------------------------
# document-service falla (fetch de anexos)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intentar_empaquetar_document_service_error_schedules_backoff() -> None:
    from app.clients import document_client

    conn = _mock_conn()
    service = _service(conn)
    exp = _expediente(radicados=[_radicado()])
    _wire_happy_path_prereqs(service, exp=exp, aip_row=_aip_row(intentos=0))
    service._aip.bump_intento = AsyncMock()

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(side_effect=document_client.DocumentClientError("boom (itest)")),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal", new=AsyncMock()
        ) as mock_storage,
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "pendiente"
    mock_storage.assert_not_awaited()
    assert service._aip.bump_intento.await_args.kwargs["ultimo_error"] == "document_service_error"


# ---------------------------------------------------------------------------
# Retención TRD fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intentar_empaquetar_trd_sin_retencion_schedules_backoff_not_immediate_agotado() -> None:
    conn = _mock_conn()
    service = _service(conn)
    radicado = _radicado()
    exp = _expediente(radicados=[radicado])
    _wire_happy_path_prereqs(service, exp=exp, aip_row=_aip_row(intentos=0))
    service._resolve_aip_retention = AsyncMock(return_value=None)
    service._aip.bump_intento = AsyncMock()

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(
                return_value={
                    str(radicado.radicado_id): [{"file_id": str(uuid.uuid4()), "filename": "a.pdf"}]
                }
            ),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal", new=AsyncMock()
        ) as mock_storage,
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "pendiente"
    mock_storage.assert_not_awaited()
    assert service._aip.bump_intento.await_args.kwargs["ultimo_error"] == "trd_sin_retencion"


# ---------------------------------------------------------------------------
# Resultado del empaquetado en storage-service: fixity / disabled / transient
# ---------------------------------------------------------------------------


def _wire_ready_to_call_storage(service: AipWiringService, exp: Expediente) -> None:
    _wire_happy_path_prereqs(service, exp=exp)
    service._resolve_aip_retention = AsyncMock(return_value=(5, date(2026, 1, 1)))
    service._aip.bump_intento = AsyncMock()


@pytest.mark.asyncio
async def test_intentar_empaquetar_fixity_mismatch_schedules_backoff_not_agotado() -> None:
    conn = _mock_conn()
    service = _service(conn)
    radicado = _radicado()
    exp = _expediente(radicados=[radicado])
    _wire_ready_to_call_storage(service, exp)

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(
                return_value={
                    str(radicado.radicado_id): [{"file_id": str(uuid.uuid4()), "filename": "a.pdf"}]
                }
            ),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=False, fixity_mismatch=True)),
        ),
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "pendiente"
    assert service._aip.bump_intento.await_args.kwargs["ultimo_error"] == "fixity_mismatch"
    assert service._aip.bump_intento.await_args.kwargs["agotado"] is False


@pytest.mark.asyncio
async def test_intentar_empaquetar_worm_disabled_schedules_backoff() -> None:
    conn = _mock_conn()
    service = _service(conn)
    radicado = _radicado()
    exp = _expediente(radicados=[radicado])
    _wire_ready_to_call_storage(service, exp)

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(
                return_value={
                    str(radicado.radicado_id): [{"file_id": str(uuid.uuid4()), "filename": "a.pdf"}]
                }
            ),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=False, disabled=True)),
        ),
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "pendiente"
    assert service._aip.bump_intento.await_args.kwargs["ultimo_error"] == "worm_disabled"


@pytest.mark.asyncio
async def test_intentar_empaquetar_transient_failure_marks_no_confirmado() -> None:
    conn = _mock_conn()
    service = _service(conn)
    radicado = _radicado()
    exp = _expediente(radicados=[radicado])
    _wire_ready_to_call_storage(service, exp)

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(
                return_value={
                    str(radicado.radicado_id): [{"file_id": str(uuid.uuid4()), "filename": "a.pdf"}]
                }
            ),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=False, transient=True)),
        ),
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "pendiente"
    assert service._aip.bump_intento.await_args.kwargs["ultimo_error"] == "aip_no_confirmado"


# ---------------------------------------------------------------------------
# Éxito: marca atómica + asiento, y pérdida de carrera
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intentar_empaquetar_success_marks_and_audits_atomically() -> None:
    from app.core.constants import SYSTEM_RECONCILER_ID

    conn = _mock_conn()
    service = _service(conn)
    radicado = _radicado()
    exp = _expediente(radicados=[radicado])
    _wire_ready_to_call_storage(service, exp)
    service._aip.mark_empaquetado = AsyncMock(return_value=1)
    aip_id = uuid.uuid4()
    file_id = uuid.uuid4()

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(
                return_value={
                    str(radicado.radicado_id): [{"file_id": str(file_id), "filename": "a.pdf"}]
                }
            ),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=True, aip_id=aip_id)),
        ),
        patch("app.services.aip_wiring_service.audit.append", new=AsyncMock()) as mock_audit,
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "empaquetado"
    service._aip.mark_empaquetado.assert_awaited_once_with(
        exp.id, aip_id, worm_retain_until=None
    )
    mock_audit.assert_awaited_once()
    kwargs = mock_audit.await_args.kwargs
    assert kwargs["action"] == "archive.aip_empaquetado"
    assert kwargs["object_type"] == "expediente_aip"
    assert kwargs["object_ref"] == str(exp.id)
    assert kwargs["actor"] == str(SYSTEM_RECONCILER_ID)
    assert kwargs["canal"] == "system"
    assert kwargs["payload"]["aip_id"] == str(aip_id)
    assert kwargs["payload"]["num_documentos"] == 1
    assert kwargs["payload"]["retencion_anios"] == 5


@pytest.mark.asyncio
async def test_intentar_empaquetar_success_propagates_outcome_retain_until_to_mirror() -> None:
    """E10 debt (migración 034): `AipPackagingOutcome.retain_until` (parseado
    de `AipResponse.retain_until` en storage-service, SIEMPRE poblado en un
    201 real) se propaga TAL CUAL a `mark_empaquetado` -- puebla el espejo
    local `expediente_aip.worm_retain_until` que la 5ª fase de renovación
    (`renovar_worm_aip`) necesita para saber cuándo extender. Gemelo del test
    de arriba (`..._marks_and_audits_atomically`), que solo cubre el caso
    `retain_until=None` (default de `AipPackagingOutcome`) -- este cubre el
    caso REAL con un valor poblado."""
    conn = _mock_conn()
    service = _service(conn)
    radicado = _radicado()
    exp = _expediente(radicados=[radicado])
    _wire_ready_to_call_storage(service, exp)
    service._aip.mark_empaquetado = AsyncMock(return_value=1)
    aip_id = uuid.uuid4()
    file_id = uuid.uuid4()
    retain_until = datetime(2051, 1, 1, tzinfo=UTC)

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(
                return_value={
                    str(radicado.radicado_id): [{"file_id": str(file_id), "filename": "a.pdf"}]
                }
            ),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(
                return_value=AipPackagingOutcome(
                    ok=True, aip_id=aip_id, retain_until=retain_until
                )
            ),
        ),
        patch("app.services.aip_wiring_service.audit.append", new=AsyncMock()),
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "empaquetado"
    service._aip.mark_empaquetado.assert_awaited_once_with(
        exp.id, aip_id, worm_retain_until=retain_until
    )


@pytest.mark.asyncio
async def test_intentar_empaquetar_lost_race_on_mark_no_phantom_audit() -> None:
    conn = _mock_conn()
    service = _service(conn)
    radicado = _radicado()
    exp = _expediente(radicados=[radicado])
    _wire_ready_to_call_storage(service, exp)
    service._aip.mark_empaquetado = AsyncMock(return_value=0)  # perdió la carrera

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(
                return_value={
                    str(radicado.radicado_id): [{"file_id": str(uuid.uuid4()), "filename": "a.pdf"}]
                }
            ),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=True, aip_id=uuid.uuid4())),
        ),
        patch("app.services.aip_wiring_service.audit.append", new=AsyncMock()) as mock_audit,
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "omitido"
    mock_audit.assert_not_awaited()


# ---------------------------------------------------------------------------
# Agotamiento tras max_intentos
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intentar_empaquetar_exhausts_after_max_intentos_and_audits() -> None:
    conn = _mock_conn()
    service = _service(conn)
    max_intentos = settings.indice_reconciliation_max_intentos
    radicado = _radicado()
    exp = _expediente(radicados=[radicado])
    _wire_happy_path_prereqs(service, exp=exp, aip_row=_aip_row(intentos=max_intentos - 1))
    service._resolve_aip_retention = AsyncMock(return_value=(5, date(2026, 1, 1)))

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(
                return_value={
                    str(radicado.radicado_id): [{"file_id": str(uuid.uuid4()), "filename": "a.pdf"}]
                }
            ),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=False, transient=True)),
        ),
        patch("app.services.aip_wiring_service.audit.append", new=AsyncMock()) as mock_audit,
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "agotado"
    mock_audit.assert_awaited_once()
    assert mock_audit.await_args.kwargs["action"] == "archive.aip_agotado"


# ---------------------------------------------------------------------------
# Best-effort real: NUNCA propaga
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intentar_empaquetar_never_raises_on_unexpected_exception() -> None:
    conn = _mock_conn()
    service = _service(conn)
    service._aip.get_or_create = AsyncMock(side_effect=RuntimeError("boom (itest)"))

    resultado = await service.intentar_empaquetar(uuid.uuid4())

    assert resultado == "pendiente"
