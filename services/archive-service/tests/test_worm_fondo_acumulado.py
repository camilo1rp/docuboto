# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-06-04 (ADR-026 Inc.3, D4) enmendada por ADR-043 (C-5 / C7): el CONTENIDO (índice, AIP) de
un fondo acumulado cuyo calendario ya venció NO recibe un COMPLIANCE de `hoy + margen`
(irreversible sobre documentación vencida) sino `RetencionDiferida`: legal hold
`valoracion_pendiente` y ninguna retención hasta que el Comité valore la TVD. El ACTA es un
instrumento de control: su base es el acto (`firmado_at`), también en un fondo.

La prueba que importa es sobre lo que SALE del resolutor (la suma que haría storage), no sobre la
fecha base suelta. Mutaciones que deben caer:
  * restituir el clamp (devolver `_restar_anios(minimo, total)` en vez de `RetencionDiferida`):
    `test_fondo_vencido_hace_30_anios_se_difiere_a_la_valoracion` y el barrido;
  * quitar `tipo="instrumento"` en el acta: `test_el_acta_de_un_fondo_vencido_conserva_retencion_
    por_calendario_con_base_en_el_acto` (devolvería None);
  * dar `RetencionDiferida` a un fondo con calendario vigente: el control positivo
    `test_fondo_con_calendario_vigente_usa_la_fecha_extrema`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import settings
from app.services.aip_wiring_service import AipWiringService
from app.services.index_service import IndexService
from app.services.retention_math import RetencionDiferida, base_worm
from app.services.transferencia_service import TransferenciaService

HOY = datetime.now(tz=UTC).date()
MARGEN = settings.worm_piso_margen_dias
_RESOLVER = "app.services.valoracion_service.ValoracionService.resolver_diferida"


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
@pytest.mark.parametrize("resolutor", ["indice", "aip"])
async def test_fondo_vencido_hace_30_anios_se_difiere_a_la_valoracion(resolutor: str) -> None:
    exp = _exp(extrema=HOY.replace(year=HOY.year - 30))
    with patch(_RESOLVER, new=AsyncMock(return_value=RetencionDiferida())) as asegurar:
        resuelto = await _resolver(resolutor, exp, gestion=0, central=10)

    assert isinstance(resuelto, RetencionDiferida), "fondo vencido: sin retención, solo hold"
    # el hold se asegura ANTES de que el llamador proteja (orden conservador)
    asegurar.assert_awaited_once()
    assert asegurar.await_args.args[0] is exp


@pytest.mark.asyncio
@pytest.mark.parametrize("resolutor", ["indice", "aip"])
async def test_fondo_vencido_propaga_la_falla_del_hold_en_vez_de_proteger_sin_el(
    resolutor: str,
) -> None:
    """Si el hold no se pudo fijar, el resolutor NO devuelve una retención: lanza, y el llamador
    pospone con backoff. Mutación: capturar la excepción y devolver `RetencionDiferida` -> rojo."""
    from app.services.valoracion_service import ValoracionHoldError

    exp = _exp(extrema=HOY.replace(year=HOY.year - 30))
    with patch(_RESOLVER, new=AsyncMock(side_effect=ValoracionHoldError("storage caído"))):
        with pytest.raises(ValoracionHoldError):
            await _resolver(resolutor, exp, gestion=0, central=10)


@pytest.mark.asyncio
async def test_el_acta_de_un_fondo_vencido_conserva_retencion_por_calendario_con_base_en_el_acto() -> None:
    """El acta sobrevive al contenido como prueba: NO se difiere. Su base es `firmado_at`."""
    exp = _exp(extrema=HOY.replace(year=HOY.year - 30))
    firmado = datetime(2026, 3, 1, tzinfo=UTC)
    repo = _repo(exp, gestion=0, central=10)
    svc = TransferenciaService(MagicMock(), "demo")
    svc._archive = repo
    resuelto = await svc._resolve_worm_retention_acta(uuid.uuid4(), exp.id, firmado_at=firmado)

    assert resuelto == (10, firmado.date())


@pytest.mark.asyncio
@pytest.mark.parametrize("resolutor", ["indice", "aip", "acta"])
async def test_fondo_con_calendario_vigente_usa_la_fecha_extrema(resolutor: str) -> None:
    """Control positivo: si fecha_extrema_final + total cae por encima del margen, el contenido
    sigue el calendario NATURAL (COMPLIANCE con base en la fecha extrema; no se difiere nada).
    El acta, base en el acto."""
    extrema = HOY.replace(year=HOY.year - 2)
    exp = _exp(extrema=extrema)
    with patch(_RESOLVER, new=AsyncMock(side_effect=AssertionError("no debe diferirse"))):
        resuelto = await _resolver(resolutor, exp, gestion=0, central=30)

    assert isinstance(resuelto, tuple)
    total, inicio = resuelto
    if resolutor == "acta":
        assert inicio == exp.closed_at.date() or inicio == datetime.now(tz=UTC).date()
    else:
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
@pytest.mark.parametrize("resolutor", ["indice", "aip"])
async def test_fondo_sin_fecha_extrema_final_sigue_fail_closed(resolutor: str) -> None:
    """El CHECK de la base lo impide, pero el resolutor no lo da por supuesto. El acta queda
    fuera a propósito (ADR-043): su base es el acto (`firmado_at`), no la fecha extrema."""
    assert await _resolver(resolutor, _exp(extrema=None), gestion=0, central=10) is None


@pytest.mark.asyncio
async def test_el_acta_no_depende_de_la_fecha_extrema_del_fondo() -> None:
    resuelto = await _resolver("acta", _exp(extrema=None), gestion=0, central=10)
    assert resuelto is not None and resuelto[0] == 10


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
def test_base_worm_nunca_fija_un_retain_until_menor_que_el_margen(anios: int) -> None:
    """Barrido: sea cual sea la antigüedad del fondo, o se difiere (sin retención) o el
    `retain_until` natural supera el piso. NUNCA un `retain_until` en el pasado ni 'recortado'
    hasta el margen (el clamp retirado)."""
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
        if isinstance(inicio, RetencionDiferida):
            assert _suma_storage(extrema, anios) < HOY + timedelta(days=MARGEN)
        else:
            assert inicio == extrema, "sin clamp: la base es la fecha extrema o no hay base"
            assert _suma_storage(inicio, anios) >= HOY + timedelta(days=MARGEN)


def test_base_worm_bisiesto_no_deja_una_retencion_natural_bajo_el_piso_de_storage() -> None:
    hoy = date(2028, 2, 29)
    # fecha extrema + 5 años = 2033-01-01 > hoy + 400 d (2029-04-04): natural, vigente.
    vigente = base_worm(
        origen="fondo_acumulado", closed_at=None, fecha_extrema_final=date(2028, 1, 1),
        total_years=5, margen_dias=MARGEN, hoy=hoy,
    )
    assert vigente == date(2028, 1, 1)
    # Justo bajo el margen: se difiere en vez de fijar algo que storage rechazaría.
    vencido = base_worm(
        origen="fondo_acumulado", closed_at=None, fecha_extrema_final=date(2024, 2, 1),
        total_years=5, margen_dias=MARGEN, hoy=hoy,
    )
    assert isinstance(vencido, RetencionDiferida)


def test_base_worm_instrumento_ignora_el_origen_del_fondo() -> None:
    firmado = datetime(2026, 5, 4, tzinfo=UTC)
    assert base_worm(
        origen="fondo_acumulado", closed_at=firmado, fecha_extrema_final=date(1985, 12, 31),
        total_years=5, margen_dias=MARGEN, hoy=HOY, tipo="instrumento",
    ) == firmado.date()
    assert base_worm(
        origen="fondo_acumulado", closed_at=None, fecha_extrema_final=date(1985, 12, 31),
        total_years=5, margen_dias=MARGEN, hoy=HOY, tipo="instrumento",
    ) is None


def test_la_retencion_diferida_declara_su_motivo() -> None:
    assert RetencionDiferida().motivo == "valoracion_pendiente"
