# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de `storage.api.package_aip` — el empaquetado del AIP como acto de
sistema (ADR-047: sustituye a la antigua ruta interna). Único llamador: la fase
de reconciliación de archive (`AipWiringService.intentar_empaquetar`).

El servicio subyacente (`AipService.empaquetar_aip`) ya está probado en
`test_aip_real.py`; aquí solo se prueba lo propio de la función: el `actor`
SIEMPRE es `SYSTEM_RECONCILER_ID` y el mapeo de excepciones a `AipPackagingOutcome`."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from storage import api as storage_api
from storage.core.constants import SYSTEM_RECONCILER_ID
from storage.schemas.preservacion import AipResponse
from storage.services.aip_service import AipYaExisteError
from storage.services.preservation_worm_service import (
    FixityMismatchError,
    PreservationDisabledError,
    RetencionDiferidaSinHoldError,
)
from tests.storage.conftest import TENANT_SLUG

_EMPAQUETAR = "storage.services.aip_service.AipService.empaquetar_aip"
_RETAIN_UNTIL = datetime(2031, 1, 1, tzinfo=UTC)


async def _package() -> storage_api.AipPackagingOutcome:
    return await storage_api.package_aip(
        tenant_slug=TENANT_SLUG,
        expediente_id="EXP-1",
        documentos=[{"document_id": "R-1", "nombre": "oficio.pdf", "file_id": str(uuid.uuid4())}],
        indice_file_id=None,
        retencion_anios=5,
        fecha_inicio=date(2026, 1, 1),
    )


def _fake_aip_response(aip_id: uuid.UUID | None = None) -> AipResponse:
    return AipResponse(
        id=aip_id or uuid.uuid4(),
        expediente_id="EXP-1",
        aip_sha256="c" * 64,
        indice_sha256=None,
        num_documentos=1,
        estado="empaquetado",
        created_at=datetime.now(tz=UTC),
        premis_xml="<premis/>",
        bucket=f"orpycamcp-{TENANT_SLUG}-preservation",
        object_key="aip/EXP-1/x.zip",
        aip_bytes=999,
        formato="application/zip",
        retain_until=_RETAIN_UNTIL,
        retention_mode="COMPLIANCE",
        legal_hold=False,
    )


# ---------------------------------------------------------------------------
# Identidad: SIEMPRE SYSTEM_RECONCILER_ID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_actor_is_always_system_reconciler_id(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    with patch(_EMPAQUETAR, new=AsyncMock(return_value=_fake_aip_response())) as mock_empaquetar:
        outcome = await _package()

    assert outcome.ok
    mock_empaquetar.assert_awaited_once()
    assert mock_empaquetar.await_args.kwargs["actor"] == str(SYSTEM_RECONCILER_ID)


@pytest.mark.asyncio
async def test_happy_path_returns_ok_with_aip_id(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    aip_id = uuid.uuid4()

    with patch(_EMPAQUETAR, new=AsyncMock(return_value=_fake_aip_response(aip_id))):
        outcome = await _package()

    assert outcome.ok
    assert outcome.aip_id == aip_id
    assert outcome.retain_until == _RETAIN_UNTIL


# ---------------------------------------------------------------------------
# Mapeo de excepciones a AipPackagingOutcome
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fixity_mismatch_maps_to_fixity_mismatch(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    with patch(
        _EMPAQUETAR, new=AsyncMock(side_effect=FixityMismatchError("un anexo mutó desde que se vinculó"))
    ):
        outcome = await _package()

    assert not outcome.ok
    assert outcome.fixity_mismatch
    assert not outcome.transient


@pytest.mark.asyncio
async def test_deferred_retention_without_hold_maps_to_fixity_mismatch(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    with patch(_EMPAQUETAR, new=AsyncMock(side_effect=RetencionDiferidaSinHoldError("sin hold"))):
        outcome = await _package()

    assert not outcome.ok
    assert outcome.fixity_mismatch


@pytest.mark.asyncio
async def test_preservation_disabled_maps_to_disabled(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    with patch(
        _EMPAQUETAR, new=AsyncMock(side_effect=PreservationDisabledError("PRESERVATION_ENABLED=false"))
    ):
        outcome = await _package()

    assert not outcome.ok
    assert outcome.disabled
    assert not outcome.transient


@pytest.mark.asyncio
async def test_aip_already_exists_maps_to_ya_existe_with_current_aip_id(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    vigente = uuid.uuid4()

    with patch(_EMPAQUETAR, new=AsyncMock(side_effect=AipYaExisteError("ya hay", aip_id=vigente))):
        outcome = await _package()

    assert not outcome.ok
    assert outcome.ya_existe
    assert outcome.aip_id == vigente


@pytest.mark.asyncio
async def test_unexpected_error_maps_to_transient(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    with patch(_EMPAQUETAR, new=AsyncMock(side_effect=RuntimeError("minio caído"))):
        outcome = await _package()

    assert not outcome.ok
    assert outcome.transient
