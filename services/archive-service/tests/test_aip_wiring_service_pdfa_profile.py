# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Perfil PDF/A configurable por serie/TRD (E10 debt, follow-up de
Increment C2b, RF-FIR-15): `AipWiringService._resolve_aip_pdfa_profile` y su
cableado hacia `storage_client.empaquetar_aip_internal(pdfa_profile=...)`.

Gemelo de la sección de retención de `test_aip_wiring_service.py`
(`_resolve_aip_retention`/`test_intentar_empaquetar_trd_sin_retencion_...`),
pero con una divergencia deliberada que este archivo verifica explícitamente:
un perfil PDF/A AUSENTE (`None`) es una configuración VÁLIDA -- NUNCA
dispara `_bump_backoff` ni bloquea el empaquetado, a diferencia de una
retención ausente (`trd_sin_retencion`, que SÍ es fail-closed obligatorio).

Todo pure-unit, mismo patrón que `test_aip_wiring_service.py`: los tres
repositorios internos (`_aip`/`_archive`/`_index`) son instancias reales con
sus métodos sustituidos por `AsyncMock` uno a uno.
"""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.clients.storage_client import AipPackagingOutcome
from app.models.archive import Expediente, TrdSerie
from app.services.aip_wiring_service import AipWiringService
from tests.test_aip_wiring_service import (
    NOW,
    _expediente,
    _mock_conn,
    _radicado,
    _service,
    _wire_happy_path_prereqs,
)


def _expediente_sin_serie(*, radicados: list | None = None) -> Expediente:
    """`_expediente()` (test_aip_wiring_service.py) trata `trd_serie_id=None`
    como "no lo especifiqué" y lo sustituye por un UUID aleatorio (default de
    conveniencia para los tests de retención, que SIEMPRE necesitan una
    serie) -- no sirve para construir un expediente que de verdad NO tenga
    serie. Este helper construye el `Expediente` directamente para ese caso."""
    return Expediente(
        id=uuid.uuid4(),
        code="EXP-2026-0001",
        name="itest expediente AIP sin serie",
        description=None,
        trd_serie_id=None,
        status="closed",
        opened_by=None,
        opened_at=NOW,
        closed_at=NOW,
        updated_at=NOW,
        radicados=radicados if radicados is not None else [],
    )


def _trd_serie(*, serie_id: uuid.UUID | None = None, pdfa_profile: str | None = None) -> TrdSerie:
    return TrdSerie(
        id=serie_id or uuid.uuid4(),
        code="100",
        name="itest serie AIP pdfa_profile",
        description=None,
        retention_years=5,
        total_retention=10,
        disposition="conserve",
        is_active=True,
        created_at=NOW,
        pdfa_profile=pdfa_profile,
    )


# ---------------------------------------------------------------------------
# `_resolve_aip_pdfa_profile` en aislamiento
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_aip_pdfa_profile_none_when_expediente_has_no_serie() -> None:
    conn = _mock_conn()
    service = _service(conn)
    service._archive.get_trd_serie_by_id = AsyncMock()
    exp = _expediente_sin_serie()

    resultado = await service._resolve_aip_pdfa_profile(exp)

    assert resultado is None
    service._archive.get_trd_serie_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_aip_pdfa_profile_none_when_serie_row_missing() -> None:
    conn = _mock_conn()
    service = _service(conn)
    serie_id = uuid.uuid4()
    service._archive.get_trd_serie_by_id = AsyncMock(return_value=None)
    exp = _expediente(trd_serie_id=serie_id)

    resultado = await service._resolve_aip_pdfa_profile(exp)

    assert resultado is None
    service._archive.get_trd_serie_by_id.assert_awaited_once_with(serie_id)


@pytest.mark.asyncio
async def test_resolve_aip_pdfa_profile_none_when_serie_has_no_policy() -> None:
    """`trd_series.pdfa_profile IS NULL` -- configuración VÁLIDA (retrocompat
    total), no un error."""
    conn = _mock_conn()
    service = _service(conn)
    serie_id = uuid.uuid4()
    service._archive.get_trd_serie_by_id = AsyncMock(
        return_value=_trd_serie(serie_id=serie_id, pdfa_profile=None)
    )
    exp = _expediente(trd_serie_id=serie_id)

    resultado = await service._resolve_aip_pdfa_profile(exp)

    assert resultado is None


@pytest.mark.asyncio
async def test_resolve_aip_pdfa_profile_returns_serie_policy() -> None:
    conn = _mock_conn()
    service = _service(conn)
    serie_id = uuid.uuid4()
    service._archive.get_trd_serie_by_id = AsyncMock(
        return_value=_trd_serie(serie_id=serie_id, pdfa_profile="3a")
    )
    exp = _expediente(trd_serie_id=serie_id)

    resultado = await service._resolve_aip_pdfa_profile(exp)

    assert resultado == "3a"


# ---------------------------------------------------------------------------
# Cableado end-to-end dentro de `intentar_empaquetar`: el perfil resuelto
# llega al `storage_client`, y su ausencia NUNCA dispara backoff.
# ---------------------------------------------------------------------------


def _wire_ready_to_call_storage(service: AipWiringService, exp) -> None:
    _wire_happy_path_prereqs(service, exp=exp)
    service._resolve_aip_retention = AsyncMock(return_value=(5, date(2026, 1, 1)))
    service._aip.bump_intento = AsyncMock()


@pytest.mark.asyncio
async def test_intentar_empaquetar_passes_resolved_pdfa_profile_to_storage_client() -> None:
    conn = _mock_conn()
    service = _service(conn)
    radicado = _radicado()
    serie_id = uuid.uuid4()
    exp = _expediente(trd_serie_id=serie_id, radicados=[radicado])
    _wire_ready_to_call_storage(service, exp)
    service._archive.get_trd_serie_by_id = AsyncMock(
        return_value=_trd_serie(serie_id=serie_id, pdfa_profile="2u")
    )
    service._aip.mark_empaquetado = AsyncMock(return_value=1)

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(
                return_value={
                    str(radicado.radicado_id): [
                        {"file_id": str(uuid.uuid4()), "filename": "oficio.pdf"}
                    ]
                }
            ),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=True, aip_id=uuid.uuid4())),
        ) as mock_storage,
        patch("app.services.aip_wiring_service.audit.append", new=AsyncMock()),
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "empaquetado"
    assert mock_storage.await_args.kwargs["pdfa_profile"] == "2u"


@pytest.mark.asyncio
async def test_intentar_empaquetar_passes_none_pdfa_profile_when_serie_has_no_policy() -> None:
    """Serie SIN `pdfa_profile` propio -- `None` viaja hasta el cliente
    (que lo omite del body, retrocompat con el default global de
    storage-service), y el empaquetado NUNCA se bloquea por esto."""
    conn = _mock_conn()
    service = _service(conn)
    radicado = _radicado()
    serie_id = uuid.uuid4()
    exp = _expediente(trd_serie_id=serie_id, radicados=[radicado])
    _wire_ready_to_call_storage(service, exp)
    service._archive.get_trd_serie_by_id = AsyncMock(
        return_value=_trd_serie(serie_id=serie_id, pdfa_profile=None)
    )
    service._aip.mark_empaquetado = AsyncMock(return_value=1)

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(
                return_value={
                    str(radicado.radicado_id): [
                        {"file_id": str(uuid.uuid4()), "filename": "oficio.pdf"}
                    ]
                }
            ),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=True, aip_id=uuid.uuid4())),
        ) as mock_storage,
        patch("app.services.aip_wiring_service.audit.append", new=AsyncMock()),
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "empaquetado"
    assert mock_storage.await_args.kwargs["pdfa_profile"] is None
    # NUNCA se llega a `_bump_backoff` por causa del perfil -- ningún
    # `ultimo_error` relacionado con pdfa_profile.
    service._aip.bump_intento.assert_not_awaited()


@pytest.mark.asyncio
async def test_intentar_empaquetar_serie_without_pdfa_policy_never_schedules_backoff() -> None:
    """Espejo negativo de `test_intentar_empaquetar_trd_sin_retencion_...`
    (retención SÍ es fail-closed obligatorio): un perfil PDF/A ausente NO
    es una precondición dura -- `resultado='pendiente'` jamás ocurre por
    esta causa, ni siquiera cuando el expediente no tiene serie en
    absoluto."""
    conn = _mock_conn()
    service = _service(conn)
    radicado = _radicado()
    exp = _expediente_sin_serie(radicados=[radicado])
    _wire_ready_to_call_storage(service, exp)
    service._archive.get_trd_serie_by_id = AsyncMock()  # no debería ni llamarse
    service._aip.mark_empaquetado = AsyncMock(return_value=1)

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(
                return_value={
                    str(radicado.radicado_id): [
                        {"file_id": str(uuid.uuid4()), "filename": "oficio.pdf"}
                    ]
                }
            ),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=True, aip_id=uuid.uuid4())),
        ) as mock_storage,
        patch("app.services.aip_wiring_service.audit.append", new=AsyncMock()),
    ):
        resultado = await service.intentar_empaquetar(exp.id)

    assert resultado == "empaquetado"
    assert mock_storage.await_args.kwargs["pdfa_profile"] is None
    service._archive.get_trd_serie_by_id.assert_not_awaited()
    service._aip.bump_intento.assert_not_awaited()
