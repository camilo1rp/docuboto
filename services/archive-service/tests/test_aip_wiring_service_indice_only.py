# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Modo AIP índice-only del cableado (E10 AIP wiring follow-up, expedientes
100% físicos): ``AipWiringService.intentar_empaquetar`` cuando
``documentos == []`` y el kill-switch ``settings.aip_indice_only_enabled``
está en su default (``True``).

Complementa ``test_aip_wiring_service.py``, que ya cubre:
- ``test_intentar_empaquetar_transferred_is_elegible`` (0 documentos ->
  ``storage_client.empaquetar_aip_internal`` recibe ``documentos=[]`` ->
  ``'empaquetado'``, sin verificar el contenido del asiento de auditoría).
- ``test_intentar_empaquetar_zero_anexos_marks_omitido_and_audits`` /
  ``..._lost_race_no_phantom_audit`` (kill-switch en OFF -> ``'omitido'``,
  comportamiento PREVIO al follow-up).

Este archivo cierra los gaps de ese lado: el CONTENIDO del audit
(``indice_only: True/False``, ``num_documentos``) y el comportamiento de
fallo (fixity/WORM) del path índice-only -- que debe seguir siendo
`backoff -> agotado`, NUNCA `'omitido'` (ese resultado solo lo produce el
kill-switch OFF, no un fallo transitorio de storage-service)."""

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
        code="EXP-2026-0002",
        name="itest expediente fisico",
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
    service._aip.get_or_create = AsyncMock(return_value=aip_row or _aip_row())
    service._archive.get_expediente_by_id = AsyncMock(return_value=exp or _expediente())
    service._index.get_current = AsyncMock(return_value=_indice_current_row())
    service._index.get_by_id = AsyncMock(return_value=indice_row or _indice_by_id_row())


# ---------------------------------------------------------------------------
# Kill-switch ON (default): audit honesto -- `indice_only: True` con 0
# documentos, `indice_only: False` con anexos reales.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intentar_empaquetar_indice_only_audits_indice_only_true() -> None:
    """0 anexos digitales + kill-switch ON (default) -> el asiento de
    auditoría `archive.aip_empaquetado` debe marcar `indice_only: True` y
    `num_documentos: 0` -- distinción honesta para los auditores (comentario
    del propio servicio: "marca honesta para los auditores")."""
    assert settings.aip_indice_only_enabled is True  # confirma el default
    conn = _mock_conn()
    service = _service(conn)
    exp = _expediente(radicados=[])  # sin radicados -> sin anexos posibles
    _wire_happy_path_prereqs(service, exp=exp)
    service._resolve_aip_retention = AsyncMock(return_value=(5, date(2026, 1, 1)))
    service._aip.mark_empaquetado = AsyncMock(return_value=1)
    aip_id = uuid.uuid4()

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=True, aip_id=aip_id)),
        ) as mock_storage,
        patch("app.services.aip_wiring_service.audit.append", new=AsyncMock()) as mock_audit,
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "empaquetado"
    assert mock_storage.await_args.kwargs["documentos"] == []
    mock_audit.assert_awaited_once()
    payload = mock_audit.await_args.kwargs["payload"]
    assert payload["indice_only"] is True
    assert payload["num_documentos"] == 0


@pytest.mark.asyncio
async def test_intentar_empaquetar_with_anexos_audits_indice_only_false() -> None:
    """Un expediente CON anexos digitales reales -> `indice_only: False` en
    el mismo asiento -- el campo distingue correctamente ambos casos, no es
    siempre `True`/siempre ausente."""
    conn = _mock_conn()
    service = _service(conn)
    radicado = _radicado()
    exp = _expediente(radicados=[radicado])
    _wire_happy_path_prereqs(service, exp=exp)
    service._resolve_aip_retention = AsyncMock(return_value=(5, date(2026, 1, 1)))
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
        ) as mock_storage,
        patch("app.services.aip_wiring_service.audit.append", new=AsyncMock()) as mock_audit,
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "empaquetado"
    assert len(mock_storage.await_args.kwargs["documentos"]) == 1
    mock_audit.assert_awaited_once()
    payload = mock_audit.await_args.kwargs["payload"]
    assert payload["indice_only"] is False
    assert payload["num_documentos"] == 1


# ---------------------------------------------------------------------------
# Fallos WORM/fixity en el path índice-only -- backoff -> agotado, NUNCA
# 'omitido' (ese resultado solo lo produce el kill-switch en OFF).
# ---------------------------------------------------------------------------


def _wire_ready_to_call_storage_zero_docs(service: AipWiringService, exp: Expediente) -> None:
    _wire_happy_path_prereqs(service, exp=exp)
    service._resolve_aip_retention = AsyncMock(return_value=(5, date(2026, 1, 1)))
    service._aip.bump_intento = AsyncMock()
    service._aip.mark_omitido = AsyncMock()


@pytest.mark.asyncio
async def test_intentar_empaquetar_indice_only_fixity_mismatch_schedules_backoff_not_omitido() -> None:
    conn = _mock_conn()
    service = _service(conn)
    exp = _expediente(radicados=[])
    _wire_ready_to_call_storage_zero_docs(service, exp)

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=False, fixity_mismatch=True)),
        ) as mock_storage,
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "pendiente"
    assert mock_storage.await_args.kwargs["documentos"] == []
    assert service._aip.bump_intento.await_args.kwargs["ultimo_error"] == "fixity_mismatch"
    assert service._aip.bump_intento.await_args.kwargs["agotado"] is False
    service._aip.mark_omitido.assert_not_awaited()  # NUNCA 'omitido' por un fallo transitorio


@pytest.mark.asyncio
async def test_intentar_empaquetar_indice_only_worm_disabled_schedules_backoff_not_omitido() -> None:
    conn = _mock_conn()
    service = _service(conn)
    exp = _expediente(radicados=[])
    _wire_ready_to_call_storage_zero_docs(service, exp)

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=False, disabled=True)),
        ),
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "pendiente"
    assert service._aip.bump_intento.await_args.kwargs["ultimo_error"] == "worm_disabled"
    service._aip.mark_omitido.assert_not_awaited()


@pytest.mark.asyncio
async def test_intentar_empaquetar_indice_only_exhausts_after_max_intentos() -> None:
    """El path índice-only también AGOTA (no queda reintentando para
    siempre) -- mismo umbral `settings.indice_reconciliation_max_intentos`
    que el path con anexos (`test_aip_wiring_service.py::
    test_intentar_empaquetar_exhausts_after_max_intentos_and_audits`)."""
    conn = _mock_conn()
    service = _service(conn)
    max_intentos = settings.indice_reconciliation_max_intentos
    exp = _expediente(radicados=[])
    _wire_happy_path_prereqs(service, exp=exp, aip_row=_aip_row(intentos=max_intentos - 1))
    service._resolve_aip_retention = AsyncMock(return_value=(5, date(2026, 1, 1)))

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(return_value={}),
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
# Renovación CT (E10 debt, 5.ª fase): `renovar_worm_aip` no distingue un AIP
# índice-only de uno con contenido -- `expediente_aip`/`AipRepository.
# list_worm_renovacion_pending` no tienen NINGUNA columna/filtro relacionada
# con `num_documentos`/índice-only (verificado por inspección del repositorio,
# ver `app/repositories/aip_repository.py`); `renovar_worm_aip` en sí mismo
# no recibe `documentos` como parámetro -- solo `expediente_id`. Este test
# confirma el comportamiento EXTREMO a extremo: empaquetar en modo
# índice-only y luego renovar el MISMO expediente sigue el flujo genérico sin
# ninguna rama especial.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_worm_aip_after_indice_only_packaging_uses_generic_path() -> None:
    conn = _mock_conn()
    service = _service(conn)
    exp = _expediente(radicados=[])
    _wire_happy_path_prereqs(service, exp=exp)
    service._resolve_aip_retention = AsyncMock(return_value=(5, date(2026, 1, 1)))
    service._aip.mark_empaquetado = AsyncMock(return_value=1)
    aip_id = uuid.uuid4()

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=True, aip_id=aip_id)),
        ),
        patch("app.services.aip_wiring_service.audit.append", new=AsyncMock()),
    ):
        empaquetado_resultado = await service.intentar_empaquetar(exp.id)

    assert empaquetado_resultado == "empaquetado"  # AIP índice-only ya vigente

    # Ahora renovar: `renovar_worm_aip` no recibe ni consulta `documentos` en
    # ningún punto -- el flujo es IDÉNTICO al de un AIP con contenido (ver
    # `test_aip_wiring_service_worm_renovacion.py::
    # test_renovar_worm_aip_success_marks_and_audits_when_renovado`).
    service._resolve_ct_retencion_anios = AsyncMock(return_value=25)
    nueva = datetime(2051, 1, 1, tzinfo=UTC)
    from app.clients.storage_client import WormRenewalOutcome

    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=WormRenewalOutcome(ok=True, retain_until=nueva, renovado=True)),
    ) as mock_client:
        renovar_resultado = await service.renovar_worm_aip(exp.id, intentos_previos=0)

    assert renovar_resultado == "renovado"
    # `tipo="aip"` fijo, sin ningún flag/columna de índice-only en la llamada.
    assert mock_client.await_args.kwargs["tipo"] == "aip"
    assert mock_client.await_args.kwargs["expediente_id"] == str(exp.id)
