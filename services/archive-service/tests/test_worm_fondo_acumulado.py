# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-06-04 (ADR-026 Inc.3, D4): los resolutores WORM ya no devuelven `None` para un
fondo acumulado; la fecha base es `fecha_extrema_final` con el clamp de D4.4.

La prueba que importa es sobre el `retain_until` RESULTANTE (la suma que hará
storage-service), no sobre la fecha base suelta: un fondo cuya retención venció hace
décadas debe acabar con `retain_until == hoy + margen`, ni `closed_at + total` (alarga
décadas un COMPLIANCE irreversible), ni `None` (sin WORM nunca), ni una fecha pasada
(storage la rechazaría).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import settings
from app.services.aip_wiring_service import AipWiringService
from app.services.index_service import IndexService
from app.services.retention_math import base_worm
from app.services.transferencia_service import TransferenciaService

HOY = datetime.now(tz=UTC).date()
MARGEN = settings.worm_fondo_acumulado_margen_dias


def _suma_storage(inicio: date, anios: int) -> date:
    """Lo que hace `RetentionMixin.resolve_retain_until` en storage-service."""
    try:
        return inicio.replace(year=inicio.year + anios)
    except ValueError:
        return inicio.replace(month=2, day=28, year=inicio.year + anios)


def _exp(*, origen: str = "fondo_acumulado", extrema: date | None = date(1985, 12, 31)):
    return SimpleNamespace(
        id=uuid.uuid4(),
        origen=origen,
        closed_at=datetime.now(tz=UTC),
        trd_serie_id=uuid.uuid4(),
        fecha_extrema_final=extrema,
    )


def _repo(exp, *, gestion: int, central: int) -> MagicMock:
    repo = MagicMock()
    repo.get_expediente_by_id = AsyncMock(return_value=exp)
    repo.get_trd_serie_by_id = AsyncMock(
        return_value=SimpleNamespace(archivo_gestion_years=gestion, archivo_central_years=central)
    )
    return repo


async def _resolver(nombre: str, exp, *, gestion: int, central: int):
    """Invoca uno de los TRES resolutores de protección inicial con el mismo expediente."""
    repo = _repo(exp, gestion=gestion, central=central)
    if nombre == "indice":
        svc = IndexService(MagicMock(), "demo")
        svc._archive = repo
        return await svc._resolve_worm_retention(exp.id)
    if nombre == "aip":
        svc2 = AipWiringService(MagicMock(), "demo")
        svc2._archive = repo
        return await svc2._resolve_aip_retention(exp)
    svc3 = TransferenciaService(MagicMock(), "demo")
    svc3._archive = repo
    return await svc3._resolve_worm_retention_acta(
        uuid.uuid4(), exp.id, firmado_at=datetime.now(tz=UTC)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("resolutor", ["indice", "aip", "acta"])
async def test_fondo_vencido_hace_30_anios_queda_en_el_clamp(resolutor: str) -> None:
    exp = _exp(extrema=HOY.replace(year=HOY.year - 30))
    resuelto = await _resolver(resolutor, exp, gestion=0, central=10)

    assert resuelto is not None, "fondo acumulado sin WORM: el resolutor devolvió None"
    total, inicio = resuelto
    assert total == 10
    retain_until = _suma_storage(inicio, total)
    minimo = HOY + timedelta(days=MARGEN)
    # el clamp: ni closed_at+10 (años en el futuro), ni una fecha pasada
    assert abs((retain_until - minimo).days) <= 1
    assert retain_until > HOY + timedelta(days=365), "por debajo del piso de storage"


@pytest.mark.asyncio
@pytest.mark.parametrize("resolutor", ["indice", "aip", "acta"])
async def test_fondo_con_calendario_vigente_usa_la_fecha_extrema(resolutor: str) -> None:
    """Control positivo: si fecha_extrema_final + total cae por encima del margen, la
    base es la fecha extrema tal cual (no se recorta nada)."""
    extrema = HOY.replace(year=HOY.year - 2)
    exp = _exp(extrema=extrema)
    resuelto = await _resolver(resolutor, exp, gestion=0, central=30)

    assert resuelto is not None
    total, inicio = resuelto
    assert inicio == extrema
    assert _suma_storage(inicio, total) == _suma_storage(extrema, 30)


@pytest.mark.asyncio
@pytest.mark.parametrize("resolutor", ["indice", "aip", "acta"])
async def test_expediente_corriente_sigue_usando_el_cierre(resolutor: str) -> None:
    exp = _exp(origen="corriente", extrema=None)
    resuelto = await _resolver(resolutor, exp, gestion=2, central=3)

    assert resuelto is not None
    total, inicio = resuelto
    assert total == 5
    assert inicio == exp.closed_at.date()


@pytest.mark.asyncio
@pytest.mark.parametrize("resolutor", ["indice", "aip", "acta"])
async def test_fondo_sin_fecha_extrema_final_sigue_fail_closed(resolutor: str) -> None:
    """El CHECK de la base lo impide, pero el resolutor no lo da por supuesto."""
    assert await _resolver(resolutor, _exp(extrema=None), gestion=0, central=10) is None


@pytest.mark.asyncio
async def test_renovacion_ct_ya_no_excluye_al_fondo_acumulado() -> None:
    """Los tres resolutores de RENOVACIÓN (ventana rodante desde hoy) tampoco devuelven
    `None` para un fondo."""
    exp = _exp()
    repo = _repo(exp, gestion=0, central=10)
    idx = IndexService(MagicMock(), "demo")
    idx._archive = repo
    aip = AipWiringService(MagicMock(), "demo")
    aip._archive = repo
    acta = TransferenciaService(MagicMock(), "demo")
    acta._archive = repo
    assert await idx._resolve_ct_retencion_anios(exp.id) == 10
    assert await aip._resolve_ct_retencion_anios(exp.id) == 10
    assert await acta._resolve_ct_retencion_anios_acta(exp.id) == 10


@pytest.mark.parametrize("anios", [1, 5, 10, 30, 100])
def test_base_worm_nunca_queda_por_debajo_del_margen(anios: int) -> None:
    """Barrido: sea cual sea la antigüedad del fondo, el resultado supera el piso."""
    for antiguedad in (0, 1, 5, 40, 200):
        extrema = date(HOY.year - antiguedad, 2, 28)
        inicio = base_worm(
            origen="fondo_acumulado",
            closed_at=datetime.now(tz=UTC),
            fecha_extrema_final=extrema,
            total_years=anios,
            margen_dias=MARGEN,
            hoy=HOY,
        )
        assert inicio is not None
        assert _suma_storage(inicio, anios) >= HOY + timedelta(days=MARGEN - 1)


def test_base_worm_bisiesto_no_cae_bajo_el_piso_de_storage() -> None:
    hoy = date(2028, 2, 29)
    inicio = base_worm(
        origen="fondo_acumulado",
        closed_at=None,
        fecha_extrema_final=date(1950, 1, 1),
        total_years=5,
        margen_dias=MARGEN,
        hoy=hoy,
    )
    assert inicio is not None
    assert _suma_storage(inicio, 5) >= hoy + timedelta(days=365)
